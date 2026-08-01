from __future__ import annotations
import hashlib, json, re
from memor.types import Artifact, Scope
from memor.llm.base import DISTILL_PROMPT
from memor.distill.extractive import extract_key_chunks
from memor.tokencount import count_tokens

DEDUP_SIM_THRESHOLD = 0.92
SUPERSEDE_SIM_THRESHOLD = 0.80

_REPLACEMENT_RE = re.compile(
    r"(instead of|no longer|switched from|ripped out|replaced .+ with|"
    r"deprecated|migrated from|removed .+ in favor|moved away from|"
    r"changed .+ to|swapped .+ for|dropped .+ for)", re.I)

def _extract_json(raw: str) -> dict:
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    text = m.group(1).strip() if m else raw.strip()
    return json.loads(text)


def _store_memory(store, embedder, text: str, mem_type: str, session_id: str,
                  project: str, created: float, source_chunks: list[Artifact]) -> str | None:
    """Store a single memory with dedup/supersession check and provenance edges."""
    mid = f"mem:{session_id}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
    vec = embedder.embed([text])[0]
    existing = store.search(vec, Scope(project=project, kinds=["memory"]), k=1)
    if existing:
        old_art, sim = existing[0]
        if sim >= DEDUP_SIM_THRESHOLD:
            return None
        if (sim >= SUPERSEDE_SIM_THRESHOLD
                and created > old_art.created_at
                and _REPLACEMENT_RE.search(text)):
            art = Artifact(
                id=mid, kind="memory", project=project, source="distill",
                text=text, token_count=max(1, count_tokens(text)), created_at=created,
                meta={"mem_type": mem_type, "session_id": session_id},
            )
            store.add_artifacts([art], [vec])
            store.deactivate(old_art.id, superseded_by=mid)
            for c in source_chunks:
                store.add_edge(mid, c.id, "derived_from")
            return mid
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


class LocalDistiller:
    """Extract-then-rewrite distiller for the local GGUF model. Stores a rich
    `value` artifact plus retrieval keys (fact, optional questions). Raw chunks
    are never stored as memories."""

    def __init__(self, store, embedder, llm, *, gen_questions: bool = False):
        self.store, self.embedder, self.llm = store, embedder, llm
        self.gen_questions = gen_questions

    def distill_session(self, session_id: str, chunks: list[Artifact],
                        project: str) -> list[str]:
        from memor.distill.schema import build_prompt, parse_memories, GBNF_GRAMMAR
        key_chunks = extract_key_chunks(chunks, self.embedder)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for c in key_chunks:
            prompt = build_prompt(c.text, with_questions=self.gen_questions)
            raw = self.llm.complete(prompt, grammar=GBNF_GRAMMAR)
            for m in parse_memories(raw, with_questions=self.gen_questions):
                fact = m["fact"]
                fact_vec = self.embedder.embed([fact])[0]
                if self.store.find_similar_fact(fact_vec, project, threshold=0.92):
                    continue
                mid = f"mem:{session_id}:{hashlib.sha1(fact.encode()).hexdigest()[:8]}"
                art = Artifact(
                    id=mid, kind="memory", project=project, source="distill",
                    text=m["value"], token_count=max(1, count_tokens(m["value"])),
                    created_at=created,
                    meta={"mem_type": m["type"], "session_id": session_id, "fact": fact})
                self.store.add_artifacts([art], [self.embedder.embed([m["value"]])[0]])
                keys = [("fact", fact)]
                for q in m.get("questions", []):
                    keys.append(("question", q))
                key_vecs = self.embedder.embed([kt for _, kt in keys])
                self.store.add_keys(mid, keys, key_vecs)
                for src in key_chunks:
                    self.store.add_edge(mid, src.id, "derived_from")
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
        from memor.distill.extractive import classify_chunk, score_chunks, MIN_MEMORY_SIGNAL
        key_chunks = extract_key_chunks(chunks, self.embedder)
        scores = score_chunks(key_chunks)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for c, score in zip(key_chunks, scores):
            if score < MIN_MEMORY_SIGNAL:
                continue
            mem_type = classify_chunk(c.text)
            mid = _store_memory(self.store, self.embedder, c.text, mem_type,
                                session_id, project, created, [c])
            if mid is not None:
                new_ids.append(mid)
        return new_ids
