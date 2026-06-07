"""Feedback analyzer — detects whether recalled memories were used by the agent.

After a session ends, cross-references recall_log with the transcript to see
if the agent's responses referenced recalled content. Updates memory_quality
scores accordingly.

Two matching strategies:
1. N-gram overlap (fast, catches verbatim reuse)
2. Semantic similarity via embeddings (catches paraphrased reuse)
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from memor.store.sqlite_store import SqliteStore

_NGRAM_SIZE = 3
_MIN_WORDS = 4
_MATCH_RATIO = 0.10
_SEMANTIC_SIM_THRESHOLD = 0.45


def _extract_assistant_texts(transcript_path: Path) -> list[str]:
    texts = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content.lower())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", "").lower())
    return texts


def _text_was_used(memory_text: str, assistant_texts: list[str]) -> bool:
    words = memory_text.lower().split()
    if len(words) < _MIN_WORDS:
        return False
    ngrams = []
    for i in range(len(words) - _NGRAM_SIZE + 1):
        ngrams.append(" ".join(words[i:i + _NGRAM_SIZE]))
    if not ngrams:
        return False
    matches = 0
    for phrase in ngrams:
        for text in assistant_texts:
            if phrase in text:
                matches += 1
                break
    return matches >= max(1, math.ceil(len(ngrams) * _MATCH_RATIO))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _semantic_match(memory_text: str, response_text: str, embedder) -> bool:
    """Check if memory content appears in the response via embedding similarity.
    Catches paraphrased reuse that n-gram matching misses."""
    if len(memory_text.split()) < _MIN_WORDS:
        return False
    vecs = embedder.embed([memory_text, response_text])
    return _cosine(vecs[0], vecs[1]) >= _SEMANTIC_SIM_THRESHOLD


def analyze_session_feedback(
    store: SqliteStore, session_id: str, transcript_path: Path,
    *, embedder=None,
) -> int:
    recalled_ids = set()
    rows = store.db.execute("""
        SELECT q.artifact_id FROM memory_quality q
        JOIN artifacts a ON a.id = q.artifact_id
        WHERE a.active = 1
          AND a.project = (
              SELECT project FROM recall_log
              WHERE session_id = ? AND hits_count > 0
              LIMIT 1
          )
          AND q.last_recalled >= (
              SELECT MIN(timestamp) FROM recall_log
              WHERE session_id = ? AND hits_count > 0
          )
          AND q.last_recalled <= (
              SELECT MAX(timestamp) FROM recall_log
              WHERE session_id = ? AND hits_count > 0
          ) + 5
    """, (session_id, session_id, session_id)).fetchall()
    for row in rows:
        recalled_ids.add(row["artifact_id"])

    if not recalled_ids:
        return 0

    assistant_texts = _extract_assistant_texts(transcript_path)
    if not assistant_texts:
        return 0

    used_ids = []
    combined_response = " ".join(assistant_texts) if embedder else ""
    for aid in recalled_ids:
        art = store.db.execute(
            "SELECT text FROM artifacts WHERE id=?", (aid,)
        ).fetchone()
        if not art:
            continue
        if _text_was_used(art["text"], assistant_texts):
            used_ids.append(aid)
        elif embedder and _semantic_match(art["text"], combined_response, embedder):
            used_ids.append(aid)

    if used_ids:
        store.record_usage(used_ids)

    return len(used_ids)
