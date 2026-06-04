from __future__ import annotations
import hashlib, json, re
from memor.types import Artifact, Scope
from memor.llm.base import DISTILL_PROMPT
from memor.distill.extractive import extract_key_chunks
from memor.tokencount import count_tokens

DEDUP_SIM_THRESHOLD = 0.92

def _extract_json(raw: str) -> dict:
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    text = m.group(1).strip() if m else raw.strip()
    return json.loads(text)


def _store_memory(store, embedder, text: str, mem_type: str, session_id: str,
                  project: str, created: float, source_chunks: list[Artifact]) -> str | None:
    """Store a single memory with dedup check and provenance edges. Returns id or None if deduped."""
    mid = f"mem:{session_id}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
    vec = embedder.embed([text])[0]
    existing = store.search(vec, Scope(project=project, kinds=["memory"]), k=1)
    if existing and existing[0][1] >= DEDUP_SIM_THRESHOLD:
        return None
    art = Artifact(
        id=mid, kind="memory", project=project, source="distill",
        text=text, token_count=max(1, count_tokens(text)), created_at=created,
        meta={"mem_type": mem_type, "session_id": session_id},
    )
    store.add_artifacts([art], [vec])
    for c in source_chunks:
        store.add_edge(mid, c.id, "derived_from")
    return mid


class Distiller:
    """Two-step distiller: extractive pre-filter (free) → LLM abstractive (paid).
    The extractive step reduces LLM input by ~90%."""

    def __init__(self, store, embedder, llm):
        self.store, self.embedder, self.llm = store, embedder, llm

    def distill_session(
        self, session_id: str, chunks: list[Artifact], project: str
    ) -> list[str]:
        # Step 1: extractive pre-filter (local, free)
        key_chunks = extract_key_chunks(chunks, self.embedder)
        # Step 2: LLM abstractive distillation on the filtered set
        session_text = "\n".join(c.text for c in key_chunks)
        raw = self.llm.complete(DISTILL_PROMPT.format(session_text=session_text))
        data = _extract_json(raw)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for m in data.get("memories", []):
            text = m["text"].strip()
            mid = _store_memory(self.store, self.embedder, text, m["type"],
                                session_id, project, created, key_chunks)
            if mid is None:
                continue
            sup = m.get("supersedes_text")
            if sup:
                prior = self.store.search(
                    self.embedder.embed([sup])[0],
                    Scope(project=project, kinds=["memory"]), k=1,
                )
                if prior and prior[0][1] >= 0.8 and prior[0][0].id != mid:
                    self.store.deactivate(prior[0][0].id, superseded_by=mid)
            new_ids.append(mid)
        return new_ids


class ExtractiveDistiller:
    """LLM-free distiller. Stores the key extracted chunks as memories directly.
    Used as automatic fallback when no API key is available."""

    def __init__(self, store, embedder):
        self.store, self.embedder = store, embedder

    def distill_session(
        self, session_id: str, chunks: list[Artifact], project: str
    ) -> list[str]:
        key_chunks = extract_key_chunks(chunks, self.embedder)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for c in key_chunks:
            mid = _store_memory(self.store, self.embedder, c.text, "extract",
                                session_id, project, created, [c])
            if mid is not None:
                new_ids.append(mid)
        return new_ids
