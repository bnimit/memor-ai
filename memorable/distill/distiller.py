from __future__ import annotations
import hashlib, json, re
from memorable.types import Artifact, Scope
from memorable.llm.base import DISTILL_PROMPT

DEDUP_SIM_THRESHOLD = 0.92

def _extract_json(raw: str) -> dict:
    # Strip markdown code fences if present
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    text = m.group(1).strip() if m else raw.strip()
    return json.loads(text)


class Distiller:
    def __init__(self, store, embedder, llm):
        self.store, self.embedder, self.llm = store, embedder, llm

    def distill_session(
        self, session_id: str, chunks: list[Artifact], project: str
    ) -> list[str]:
        session_text = "\n".join(c.text for c in chunks)
        raw = self.llm.complete(DISTILL_PROMPT.format(session_text=session_text))
        data = _extract_json(raw)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for m in data.get("memories", []):
            text = m["text"].strip()
            mid = f"mem:{session_id}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
            vec = self.embedder.embed([text])[0]
            # dedup: skip if a near-identical active memory already exists in project
            existing = self.store.search(
                vec, Scope(project=project, kinds=["memory"]), k=1
            )
            if existing and existing[0][1] >= DEDUP_SIM_THRESHOLD:
                continue
            art = Artifact(
                id=mid,
                kind="memory",
                project=project,
                source="distill",
                text=text,
                token_count=max(1, len(text) // 4),
                created_at=created,
                meta={"mem_type": m["type"], "session_id": session_id},
            )
            self.store.add_artifacts([art], [vec])
            # provenance edge: memory derived_from each chunk of this session
            for c in chunks:
                self.store.add_edge(mid, c.id, "derived_from")
            # supersede: deactivate the prior memory this one reverses
            sup = m.get("supersedes_text")
            if sup:
                prior = self.store.search(
                    self.embedder.embed([sup])[0],
                    Scope(project=project, kinds=["memory"]),
                    k=1,
                )
                if prior and prior[0][1] >= 0.8 and prior[0][0].id != mid:
                    self.store.deactivate(prior[0][0].id, superseded_by=mid)
            new_ids.append(mid)
        return new_ids
