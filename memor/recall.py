from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from memor.types import Scope


@dataclass
class RecallResult:
    hits_count: int
    top_score: float
    tokens_injected: int
    latency_ms: float
    status: str
    status_message: str
    formatted_context: str
    hit_ids: list[str] = None


def _format_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _detect_status(store, project: str, hits_count: int) -> str:
    if hits_count > 0:
        llm_mems = store.db.execute("""
            SELECT COUNT(*) as c FROM artifacts
            WHERE kind='memory' AND project=? AND active=1
              AND json_extract(meta, '$.mem_type') IN ('decision','lesson','snippet','bugfix')
        """, (project,)).fetchone()["c"]
        if llm_mems > 0:
            return "ok"
        return "extractive_only"
    return "no_hits"


def _status_message(status: str, project: str, hits_count: int,
                    tokens: int, top_score: float) -> str:
    if status == "ok":
        return f"Memor: recalled {hits_count} memories ({tokens} tokens, {top_score:.2f} top score)"
    if status == "extractive_only":
        return f"Memor: recalled {hits_count} memories ({tokens} tokens, {top_score:.2f} top score)"
    if status == "no_hits":
        return f'Memor: no relevant memories for project "{project}" yet'
    if status == "empty_db":
        return 'Memor: memory store is empty — run "memor daemon" to start ingesting sessions'
    if status == "no_embedder":
        return "Memor: inactive — run 'memor setup-model' to download the embedding model"
    if status == "skipped_trivial":
        return "Memor: skipped — trivial prompt"
    return f"Memor: status={status}"


DEFAULT_MAX_TOKENS = 1500
_TEXT_TRUNCATE_LEN = 600


def _injected_token_count(artifact) -> int:
    """Token cost of the text actually injected (after 600-char truncation)."""
    if len(artifact.text) <= _TEXT_TRUNCATE_LEN:
        return artifact.token_count
    from memor.tokencount import count_tokens
    return max(1, count_tokens(artifact.text[:_TEXT_TRUNCATE_LEN]))


DEFAULT_MIN_SIMILARITY = 0.0


def recall(query: str, project: str, db_path: str, *,
           embedder=None, k: int = 8, threshold: float = 0.3,
           max_tokens: int = DEFAULT_MAX_TOKENS,
           min_similarity: float = DEFAULT_MIN_SIMILARITY,
           exclude_ids: set[str] | None = None,
           session_id: str = "") -> RecallResult:
    t0 = time.perf_counter()

    if not Path(db_path).exists():
        ms = (time.perf_counter() - t0) * 1000
        return RecallResult(
            hits_count=0, top_score=0.0, tokens_injected=0,
            latency_ms=ms, status="empty_db",
            status_message=_status_message("empty_db", project, 0, 0, 0.0),
            formatted_context="")

    from memor.store.sqlite_store import SqliteStore
    from memor.retrieve.retriever import Retriever

    store = SqliteStore(db_path, dim=embedder.dim)
    import os
    type_halflife = os.environ.get("MEMOR_TYPE_HALFLIFE", "0") == "1"
    supersession = os.environ.get("MEMOR_SUPERSESSION", "0") == "1"
    retriever = Retriever(store, embedder, k=k, min_similarity=min_similarity,
                          type_halflife=type_halflife, supersession=supersession)
    trace = retriever.query(query, Scope(project=project))

    hits = list(trace.hits)
    if session_id:
        hits = [h for h in hits if h.artifact.meta.get("session_id") != session_id]
    if exclude_ids:
        hits = [h for h in hits if h.artifact.id not in exclude_ids]
    if threshold > 0.0:
        hits = [h for h in hits if h.score >= threshold]
    if max_tokens > 0:
        budget_hits = []
        running = 0
        for h in hits:
            cost = _injected_token_count(h.artifact)
            if running + cost > max_tokens and budget_hits:
                break
            budget_hits.append(h)
            running += cost
        hits = budget_hits
    top_score = hits[0].score if hits else 0.0
    tokens = sum(_injected_token_count(h.artifact) for h in hits)

    if not hits:
        status = "no_hits"
    else:
        status = _detect_status(store, project, len(hits))

    msg = _status_message(status, project, len(hits), tokens, top_score)

    lines = []
    if hits:
        lines.append(f"## Recalled Memories (project: {project})")
        lines.append("")
        for i, h in enumerate(hits, 1):
            a = h.artifact
            kind_tag = a.meta.get("mem_type", a.kind)
            text = a.text if len(a.text) <= _TEXT_TRUNCATE_LEN else a.text[:_TEXT_TRUNCATE_LEN] + "..."
            source_parts = []
            sid = a.meta.get("session_id")
            if sid:
                source_parts.append(f"session {sid[:8]}")
            source_parts.append(_format_timestamp(a.created_at))
            source = ", ".join(source_parts)
            lines.append(f"### {i}. [{kind_tag}] {text}")
            lines.append(f"Source: {source} | score: {h.score:.3f}")
            lines.append("")

    lines.append("---")
    lines.append(msg)
    formatted = "\n".join(lines)

    ms = (time.perf_counter() - t0) * 1000
    return RecallResult(
        hits_count=len(hits), top_score=top_score, tokens_injected=tokens,
        latency_ms=ms, status=status, status_message=msg,
        formatted_context=formatted,
        hit_ids=[h.artifact.id for h in hits])
