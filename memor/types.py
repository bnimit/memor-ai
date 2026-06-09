from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class Artifact:
    id: str
    kind: str            # session_chunk | research | note | page | memory
    project: str
    source: str
    text: str
    token_count: int
    created_at: float    # epoch seconds
    meta: dict = field(default_factory=dict)

@dataclass
class SessionUsage:
    session_id: str
    project: str
    turn_count: int = 0
    total_input_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_create_tokens: int = 0
    total_output_tokens: int = 0
    tool_call_count: int = 0
    recall_turn_count: int = 0
    first_turn_at: float = 0.0
    last_turn_at: float = 0.0


GLOBAL_PROJECT = "_global"


@dataclass
class Scope:
    project: str | None = None
    since: float | None = None
    until: float | None = None
    kinds: list[str] | None = None

    def matches(self, a: "Artifact") -> bool:
        if self.project is not None and a.project != self.project and a.project != GLOBAL_PROJECT:
            return False
        if self.since is not None and a.created_at < self.since:
            return False
        if self.until is not None and a.created_at > self.until:
            return False
        if self.kinds is not None and a.kind not in self.kinds:
            return False
        return True

@dataclass
class Hit:
    artifact: Artifact
    score: float
    components: dict = field(default_factory=dict)  # {'sim','recency','edge'}

@dataclass
class RetrievalTrace:
    query: str
    scope: Scope
    candidates: int
    hits: list[Hit]
    latency_ms: float
