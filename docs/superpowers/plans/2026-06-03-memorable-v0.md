# Memorable V0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a measured memory layer that ingests real agent artifacts, retrieves scoped context fast, and proves with an eval harness whether it beats baselines — reaching the first honest eval number at Task 7.

**Architecture:** Single Python process, CLI-driven. One SQLite+sqlite-vec store behind a `MemoryStore` interface; pluggable `Embedder` and `LLM`. Pipeline: ingest → (distill) → `query(text, scope)` → eval. Relationships are cheap `edges` rows traversed by recursive CTE. The eval harness is the spine — nothing is claimed without a Δ vs baseline.

**Tech Stack:** Python 3.11+, `sqlite-vec`, `sentence-transformers`, `numpy`, `pytest`, `typer` (CLI), `httpx` (API LLM/embeds), `anthropic`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package + deps + pytest config |
| `memorable/types.py` | Dataclasses: `Artifact`, `Scope`, `Hit`, `RetrievalTrace` |
| `memorable/interfaces.py` | Protocols: `Embedder`, `LLM`, `MemoryStore` |
| `memorable/store/sqlite_store.py` | SQLite+sqlite-vec impl of `MemoryStore` |
| `memorable/embed/local.py` | sentence-transformers `Embedder` |
| `memorable/embed/fake.py` | Deterministic test `Embedder` |
| `memorable/embed/api.py` | OpenAI-compatible `Embedder` |
| `memorable/llm/{base.py,openai_compat.py,anthropic.py}` | `LLM` impls |
| `memorable/ingest/claude_code.py` | Claude Code JSONL → sessions + chunks |
| `memorable/ingest/documents.py` | Markdown/notes/pages → artifacts |
| `memorable/retrieve/retriever.py` | scope→vector→edge-expand→rank→trace |
| `memorable/distill/distiller.py` | session → typed memories + dedup + supersede |
| `memorable/eval/{dataset.py,metrics.py,runner.py}` | counterfactual cases, metrics, baselines |
| `memorable/eval/baselines/{claude_mem.py,graphiti.py}` | external comparative adapters |
| `memorable/cli.py` | `ingest \| distill \| query \| eval` |
| `skill/SKILL.md` + `skill/recall.py` | Claude Code recall skill wrapping `query()` |
| `tests/...` | mirror of the above |

---

### Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `memorable/__init__.py`, `tests/__init__.py`, `.gitignore`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "memorable"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
  "sqlite-vec>=0.1.6",
  "numpy>=1.26",
  "typer>=0.12",
  "httpx>=0.27",
]

[project.optional-dependencies]
local = ["sentence-transformers>=3.0"]
anthropic = ["anthropic>=0.40"]
dev = ["pytest>=8.0"]

[project.scripts]
memorable = "memorable.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Create empty `memorable/__init__.py`, `tests/__init__.py`, and `.gitignore`**

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
*.db
.memorable/
docs/superpowers/  # keep specs/plans? -> actually KEEP these; remove this line
```
(Keep `docs/` tracked — do not ignore specs/plans.)

- [ ] **Step 3: Create venv and install**

Run: `python3 -m venv .venv && .venv/bin/pip install -e ".[local,anthropic,dev]"`
Expected: install succeeds; `.venv/bin/pytest --version` prints a version.

- [ ] **Step 4: git init + commit**

```bash
git init && git add -A && git commit -m "chore: scaffold memorable package"
```

---

### Task 1: Core types

**Files:**
- Create: `memorable/types.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_types.py
from memorable.types import Artifact, Scope, Hit, RetrievalTrace

def test_artifact_defaults_and_scope_match():
    a = Artifact(id="a1", kind="session_chunk", project="stablex",
                 source="cc", text="auth refresh loop", token_count=4,
                 created_at=100.0, meta={})
    assert a.kind == "session_chunk"
    s = Scope(project="stablex", kinds=["session_chunk"])
    assert s.matches(a) is True
    assert Scope(project="other").matches(a) is False
    assert Scope(since=200.0).matches(a) is False  # a.created_at=100 < 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: memorable.types`.

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/types.py
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
class Scope:
    project: str | None = None
    since: float | None = None
    until: float | None = None
    kinds: list[str] | None = None

    def matches(self, a: "Artifact") -> bool:
        if self.project is not None and a.project != self.project:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_types.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/types.py tests/test_types.py && git commit -m "feat: core types (Artifact, Scope, Hit, RetrievalTrace)"
```

---

### Task 2: Interfaces (Protocols)

**Files:**
- Create: `memorable/interfaces.py`
- Test: `tests/test_interfaces.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interfaces.py
from memorable.interfaces import Embedder, LLM, MemoryStore
from memorable.embed.fake import FakeEmbedder  # built in Task 3, import guarded below

def test_protocols_exist():
    assert hasattr(Embedder, "embed")
    assert hasattr(LLM, "complete")
    for m in ("add_artifacts", "add_edge", "search", "neighbors"):
        assert hasattr(MemoryStore, m)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_interfaces.py -v`
Expected: FAIL (`memorable.interfaces` missing). The `FakeEmbedder` import will also fail — comment it out until Task 3, then restore.

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/interfaces.py
from __future__ import annotations
from typing import Protocol, runtime_checkable
from memorable.types import Artifact, Scope

@runtime_checkable
class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...

@runtime_checkable
class LLM(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str: ...

@runtime_checkable
class MemoryStore(Protocol):
    def add_artifacts(self, artifacts: list[Artifact], vectors: list[list[float]]) -> None: ...
    def add_edge(self, src_id: str, dst_id: str, type: str) -> None: ...
    def search(self, vector: list[float], scope: Scope, k: int) -> list[tuple[Artifact, float]]: ...
    def neighbors(self, ids: list[str], types: list[str], hops: int = 1) -> list[Artifact]: ...
    def deactivate(self, artifact_id: str, superseded_by: str) -> None: ...
```

- [ ] **Step 4: Run to verify it passes** (with `FakeEmbedder` import still commented)

Run: `.venv/bin/pytest tests/test_interfaces.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/interfaces.py tests/test_interfaces.py && git commit -m "feat: Embedder/LLM/MemoryStore protocols"
```

---

### Task 3: Fake + local embedders

**Files:**
- Create: `memorable/embed/__init__.py`, `memorable/embed/fake.py`, `memorable/embed/local.py`
- Test: `tests/test_embed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embed.py
from memorable.embed.fake import FakeEmbedder

def test_fake_embedder_deterministic_and_dim():
    e = FakeEmbedder(dim=16)
    v1 = e.embed(["auth refresh loop"])[0]
    v2 = e.embed(["auth refresh loop"])[0]
    assert len(v1) == 16 and v1 == v2          # deterministic
    assert e.embed(["different text"])[0] != v1 # content-sensitive
    import math
    assert abs(math.sqrt(sum(x*x for x in v1)) - 1.0) < 1e-6  # normalized
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_embed.py -v`
Expected: FAIL (`memorable.embed.fake` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/embed/fake.py
import hashlib, math

class FakeEmbedder:
    """Deterministic hash-based embedder for tests. No model download."""
    def __init__(self, dim: int = 16):
        self.dim = dim
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(x*x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out
```

```python
# memorable/embed/local.py
import math

class LocalEmbedder:
    """sentence-transformers embedder. Default bge-small-en-v1.5 (dim 384)."""
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()
    def embed(self, texts: list[str]) -> list[list[float]]:
        embs = self._model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [e.tolist() for e in embs]
```

- [ ] **Step 4: Run to verify it passes**, then restore the `FakeEmbedder` import in `tests/test_interfaces.py`

Run: `.venv/bin/pytest tests/test_embed.py tests/test_interfaces.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/embed tests/test_embed.py tests/test_interfaces.py && git commit -m "feat: fake + local embedders"
```

---

### Task 4: SQLite + sqlite-vec store

**Files:**
- Create: `memorable/store/__init__.py`, `memorable/store/sqlite_store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from memorable.store.sqlite_store import SqliteStore
from memorable.types import Artifact, Scope
from memorable.embed.fake import FakeEmbedder

def make(id, project, text, created, kind="session_chunk"):
    return Artifact(id=id, kind=kind, project=project, source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})

def test_add_search_scope_and_edges(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [make("a1","stablex","auth refresh token loop",100),
            make("a2","stablex","emscripten sync bug",90),
            make("a3","other","auth refresh token loop",100)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    q = e.embed(["auth refresh"])[0]
    hits = s.search(q, Scope(project="stablex"), k=5)
    ids = [a.id for a, _ in hits]
    assert "a1" in ids and "a3" not in ids       # scope filter applied
    assert hits[0][0].id == "a1"                  # most similar first

    s.add_edge("a1", "a2", "fixes")
    nbrs = [a.id for a in s.neighbors(["a1"], ["fixes"], hops=1)]
    assert nbrs == ["a2"]

def test_deactivate_excludes_from_search(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("old","p","use library X",10)], e.embed(["use library X"]))
    s.add_artifacts([make("new","p","use library Y instead",20)], e.embed(["use library Y instead"]))
    s.deactivate("old", superseded_by="new")
    ids = [a.id for a, _ in s.search(e.embed(["use library"])[0], Scope(project="p"), k=5)]
    assert "old" not in ids and "new" in ids
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: FAIL (`memorable.store.sqlite_store` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/store/sqlite_store.py
from __future__ import annotations
import json, sqlite3, struct
import sqlite_vec
from memorable.types import Artifact, Scope

def _serialize(v: list[float]) -> bytes:
    return struct.pack("%sf" % len(v), *v)

class SqliteStore:
    def __init__(self, path: str, dim: int):
        self.dim = dim
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self):
        self.db.executescript(f"""
        CREATE TABLE IF NOT EXISTS artifacts(
          id TEXT PRIMARY KEY, kind TEXT, project TEXT, source TEXT,
          text TEXT, token_count INTEGER, created_at REAL, meta TEXT,
          active INTEGER DEFAULT 1, superseded_by TEXT);
        CREATE TABLE IF NOT EXISTS edges(
          src_id TEXT, dst_id TEXT, type TEXT,
          PRIMARY KEY(src_id, dst_id, type));
        CREATE INDEX IF NOT EXISTS idx_art_project ON artifacts(project, active);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_artifacts USING vec0(
          embedding float[{self.dim}]);
        """)
        self.db.commit()

    def add_artifacts(self, artifacts: list[Artifact], vectors: list[list[float]]) -> None:
        cur = self.db.cursor()
        for a, v in zip(artifacts, vectors):
            cur.execute(
              "INSERT OR REPLACE INTO artifacts(id,kind,project,source,text,token_count,created_at,meta,active,superseded_by)"
              " VALUES(?,?,?,?,?,?,?,?,1,NULL)",
              (a.id, a.kind, a.project, a.source, a.text, a.token_count, a.created_at, json.dumps(a.meta)))
            rowid = cur.execute("SELECT rowid FROM artifacts WHERE id=?", (a.id,)).fetchone()[0]
            cur.execute("INSERT OR REPLACE INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(v)))
        self.db.commit()

    def add_edge(self, src_id: str, dst_id: str, type: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO edges(src_id,dst_id,type) VALUES(?,?,?)",
                        (src_id, dst_id, type))
        self.db.commit()

    def _row_to_artifact(self, r) -> Artifact:
        return Artifact(id=r["id"], kind=r["kind"], project=r["project"], source=r["source"],
                        text=r["text"], token_count=r["token_count"], created_at=r["created_at"],
                        meta=json.loads(r["meta"]))

    def search(self, vector: list[float], scope: Scope, k: int) -> list[tuple[Artifact, float]]:
        # over-fetch from vec index, then apply scope filter in SQL on the join.
        rows = self.db.execute(f"""
          SELECT a.*, v.distance AS distance
          FROM (SELECT rowid, distance FROM vec_artifacts
                WHERE embedding MATCH ? AND k = ?) v
          JOIN artifacts a ON a.rowid = v.rowid
          WHERE a.active = 1
            AND (? IS NULL OR a.project = ?)
            AND (? IS NULL OR a.created_at >= ?)
            AND (? IS NULL OR a.created_at <= ?)
          ORDER BY v.distance ASC
        """, (_serialize(vector), max(k*5, k),
              scope.project, scope.project,
              scope.since, scope.since,
              scope.until, scope.until)).fetchall()
        out = []
        for r in rows[:k]:
            if scope.kinds is not None and r["kind"] not in scope.kinds:
                continue
            sim = 1.0 - float(r["distance"])  # cosine distance -> similarity
            out.append((self._row_to_artifact(r), sim))
        return out

    def neighbors(self, ids: list[str], types: list[str], hops: int = 1) -> list[Artifact]:
        # recursive CTE; hops bounds depth. types filters edge types.
        qmarks_ids = ",".join("?" * len(ids))
        qmarks_types = ",".join("?" * len(types))
        rows = self.db.execute(f"""
          WITH RECURSIVE walk(id, depth) AS (
            SELECT dst_id, 1 FROM edges
              WHERE src_id IN ({qmarks_ids}) AND type IN ({qmarks_types})
            UNION
            SELECT e.dst_id, w.depth+1 FROM edges e JOIN walk w ON e.src_id = w.id
              WHERE w.depth < ? AND e.type IN ({qmarks_types})
          )
          SELECT DISTINCT a.* FROM artifacts a JOIN walk ON a.id = walk.id
          WHERE a.active = 1
        """, (*ids, *types, hops, *types)).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def deactivate(self, artifact_id: str, superseded_by: str) -> None:
        self.db.execute("UPDATE artifacts SET active=0, superseded_by=? WHERE id=?",
                        (superseded_by, artifact_id))
        self.add_edge(superseded_by, artifact_id, "supersedes")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add memorable/store tests/test_store.py && git commit -m "feat: sqlite-vec store with scope search + edge traversal + supersede"
```

---

### Task 5: Claude Code transcript ingest

**Files:**
- Create: `memorable/ingest/__init__.py`, `memorable/ingest/claude_code.py`
- Test: `tests/test_ingest_claude_code.py`, fixture `tests/fixtures/sample.jsonl`

- [ ] **Step 1: Write fixture + failing test**

Fixture `tests/fixtures/sample.jsonl` (two user/assistant turns):
```json
{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop"}}
{"type":"assistant","timestamp":"2026-05-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"The loop is caused by re-issuing the token on 401."}]}}
```

```python
# tests/test_ingest_claude_code.py
from pathlib import Path
from memorable.ingest.claude_code import parse_transcript

def test_parse_transcript_yields_chunks():
    f = Path(__file__).parent / "fixtures" / "sample.jsonl"
    arts = parse_transcript(f, project="stablex")
    assert len(arts) == 2
    assert all(a.kind == "session_chunk" and a.project == "stablex" for a in arts)
    assert "auth refresh loop" in arts[0].text
    assert "401" in arts[1].text
    assert arts[0].created_at < arts[1].created_at
    assert arts[0].meta["session_id"] == arts[1].meta["session_id"]  # same file = same session
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_ingest_claude_code.py -v`
Expected: FAIL (`parse_transcript` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/ingest/claude_code.py
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from memorable.types import Artifact

def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()

def _text_of(message: dict) -> str:
    c = message.get("content", "")
    if isinstance(c, str):
        return c
    parts = []
    for block in c:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)

def parse_transcript(path: Path, project: str) -> list[Artifact]:
    session_id = path.stem
    arts: list[Artifact] = []
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("type") not in ("user", "assistant"):
            continue
        msg = rec.get("message", {})
        text = _text_of(msg).strip()
        if not text:
            continue
        arts.append(Artifact(
            id=f"{session_id}:{i}", kind="session_chunk", project=project,
            source="claude_code", text=text, token_count=max(1, len(text)//4),
            created_at=_epoch(rec["timestamp"]),
            meta={"session_id": session_id, "role": msg.get("role"), "ord": i}))
    return arts

def discover_project(transcript_path: Path) -> str:
    # ~/.claude/projects/<dir-encoded-path>/<uuid>.jsonl  -> use parent dir name
    return transcript_path.parent.name
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_ingest_claude_code.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/ingest tests/test_ingest_claude_code.py tests/fixtures/sample.jsonl && git commit -m "feat: claude code transcript ingest"
```

---

### Task 6: Retriever (scope → vector → edge-expand → rank → trace)

**Files:**
- Create: `memorable/retrieve/__init__.py`, `memorable/retrieve/retriever.py`
- Test: `tests/test_retriever.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retriever.py
from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.retrieve.retriever import Retriever
from memorable.types import Artifact, Scope

def make(id, text, created, kind="session_chunk"):
    return Artifact(id=id, kind=kind, project="stablex", source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})

def test_retriever_ranks_and_traces(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [make("a1","auth refresh token loop",100),
            make("a2","auth refresh token loop",50)]  # same text, older
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=2, recency_weight=0.3, edge_expand=False)
    trace = r.query("auth refresh", Scope(project="stablex"))
    assert trace.hits[0].artifact.id == "a1"          # recency breaks the tie
    assert "sim" in trace.hits[0].components and "recency" in trace.hits[0].components
    assert trace.latency_ms >= 0 and trace.candidates >= 1

def test_edge_expansion_pulls_linked(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    s.add_artifacts([make("bug","emscripten sync crash",100),
                     make("fix","added mutex around sync queue",100)],
                    e.embed(["emscripten sync crash","added mutex around sync queue"]))
    s.add_edge("bug","fix","fixes")
    r = Retriever(s, e, k=1, edge_expand=True)
    trace = r.query("emscripten sync crash", Scope(project="stablex"))
    ids = [h.artifact.id for h in trace.hits]
    assert "fix" in ids        # surfaced via edge even though query didn't match its words
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_retriever.py -v`
Expected: FAIL (`Retriever` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# memorable/retrieve/retriever.py
from __future__ import annotations
import time
from memorable.types import Scope, Hit, RetrievalTrace
from memorable.interfaces import Embedder, MemoryStore

EDGE_TYPES = ["fixes", "supersedes", "part_of", "derived_from"]

class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder, *,
                 k: int = 8, recency_weight: float = 0.2, edge_expand: bool = True):
        self.store, self.embedder = store, embedder
        self.k, self.recency_weight, self.edge_expand = k, recency_weight, edge_expand

    def query(self, text: str, scope: Scope) -> RetrievalTrace:
        t0 = time.perf_counter()
        qv = self.embedder.embed([text])[0]
        base = self.store.search(qv, scope, self.k)          # [(Artifact, sim)]
        candidates = len(base)
        hits: dict[str, Hit] = {}
        # recency normalization over the candidate window
        times = [a.created_at for a, _ in base] or [0.0]
        tmin, tmax = min(times), max(times)
        span = (tmax - tmin) or 1.0
        for a, sim in base:
            rec = (a.created_at - tmin) / span
            score = (1 - self.recency_weight) * sim + self.recency_weight * rec
            hits[a.id] = Hit(a, score, {"sim": sim, "recency": rec, "edge": 0.0})
        # 1-hop edge expansion: linked artifacts inherit a discounted score
        if self.edge_expand and base:
            seed_ids = [a.id for a, _ in base]
            for nb in self.store.neighbors(seed_ids, EDGE_TYPES, hops=1):
                if nb.id not in hits:
                    hits[nb.id] = Hit(nb, 0.5 * max(h.score for h in hits.values()),
                                      {"sim": 0.0, "recency": 0.0, "edge": 1.0})
        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:self.k]
        return RetrievalTrace(query=text, scope=scope, candidates=candidates,
                              hits=ranked, latency_ms=(time.perf_counter()-t0)*1000)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_retriever.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add memorable/retrieve tests/test_retriever.py && git commit -m "feat: retriever with scope, recency blend, edge expansion, trace"
```

---

### Task 7: Eval harness — metrics, baselines, runner (FIRST HONEST NUMBER)

**Files:**
- Create: `memorable/eval/__init__.py`, `memorable/eval/metrics.py`, `memorable/eval/dataset.py`, `memorable/eval/runner.py`
- Test: `tests/test_metrics.py`, `tests/test_eval_runner.py`

- [ ] **Step 1: Write the failing metrics test**

```python
# tests/test_metrics.py
from memorable.eval.metrics import recall_at_k, ndcg_at_k

def test_recall_and_ndcg():
    retrieved = ["a","b","c","d"]
    relevant = {"b","d","z"}
    assert recall_at_k(retrieved, relevant, k=4) == 2/3   # found 2 of 3 relevant
    assert recall_at_k(retrieved, relevant, k=1) == 0.0   # 'a' not relevant
    # ndcg: relevant at ranks 2 and 4 -> between 0 and 1, and rank-2-first beats rank-4-first
    high = ndcg_at_k(["b","a","d","c"], relevant, k=4)
    low = ndcg_at_k(["a","c","b","d"], relevant, k=4)
    assert 0 < low < high <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_metrics.py -v`
Expected: FAIL (`memorable.eval.metrics` missing).

- [ ] **Step 3: Implement metrics**

```python
# memorable/eval/metrics.py
import math

def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    topk = retrieved[:k]
    return len(set(topk) & relevant) / len(relevant)

def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    dcg = sum((1.0 / math.log2(i + 2)) for i, x in enumerate(retrieved[:k]) if x in relevant)
    ideal_n = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_n)) or 1.0
    return dcg / idcg
```

- [ ] **Step 4: Run metrics test to verify pass**

Run: `.venv/bin/pytest tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing dataset + runner test**

```python
# tests/test_eval_runner.py
from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.eval.dataset import EvalCase
from memorable.eval.runner import run_case, BASELINES

def test_run_case_reports_metrics_per_strategy(tmp_path):
    from memorable.types import Artifact
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    def mk(i, text, t): return Artifact(id=i, kind="session_chunk", project="p",
        source="t", text=text, token_count=len(text.split()), created_at=t, meta={})
    arts = [mk("a1","auth refresh token loop fix",100),
            mk("a2","unrelated css styling tweak",90),
            mk("a3","auth token rotation decision",80)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    case = EvalCase(query="auth refresh", scope_project="p",
                    relevant_ids={"a1","a3"}, baseline_full_tokens=1000)
    results = run_case(case, store=s, embedder=e, k=2)
    assert set(results.keys()) >= {"memory", "naive-RAG", "no-memory"}
    assert results["memory"].recall >= results["no-memory"].recall
    assert results["memory"].tokens_sent < case.baseline_full_tokens   # compaction wins on cost
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_eval_runner.py -v`
Expected: FAIL (`memorable.eval.dataset`/`runner` missing).

- [ ] **Step 7: Implement dataset + runner**

```python
# memorable/eval/dataset.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class EvalCase:
    query: str
    scope_project: str
    relevant_ids: set[str]
    baseline_full_tokens: int   # tokens if you dumped full history instead

@dataclass
class CaseResult:
    recall: float
    ndcg: float
    tokens_sent: int
    latency_ms: float
```

```python
# memorable/eval/runner.py
from __future__ import annotations
from memorable.types import Scope
from memorable.retrieve.retriever import Retriever
from memorable.eval.dataset import EvalCase, CaseResult
from memorable.eval.metrics import recall_at_k, ndcg_at_k

BASELINES = ["no-memory", "naive-RAG", "memory"]

def _result(ranked_ids, hits_tokens, latency, case, k):
    return CaseResult(
        recall=recall_at_k(ranked_ids, case.relevant_ids, k),
        ndcg=ndcg_at_k(ranked_ids, case.relevant_ids, k),
        tokens_sent=hits_tokens, latency_ms=latency)

def run_case(case: EvalCase, *, store, embedder, k: int = 8) -> dict[str, CaseResult]:
    scope = Scope(project=case.scope_project)
    out: dict[str, CaseResult] = {}

    # no-memory: retrieve nothing
    out["no-memory"] = _result([], 0, 0.0, case, k)

    # naive-RAG: similarity only, no edges, no distill
    naive = Retriever(store, embedder, k=k, recency_weight=0.0, edge_expand=False)
    tr = naive.query(case.query, scope)
    out["naive-RAG"] = _result([h.artifact.id for h in tr.hits],
                               sum(h.artifact.token_count for h in tr.hits),
                               tr.latency_ms, case, k)

    # memory: full retriever (recency + edge expansion over distilled+raw artifacts)
    mem = Retriever(store, embedder, k=k, recency_weight=0.2, edge_expand=True)
    tr = mem.query(case.query, scope)
    out["memory"] = _result([h.artifact.id for h in tr.hits],
                            sum(h.artifact.token_count for h in tr.hits),
                            tr.latency_ms, case, k)
    return out

def run_suite(cases: list[EvalCase], *, store, embedder, k: int = 8) -> dict[str, dict]:
    """Aggregate mean metrics per strategy + Δ vs naive-RAG and full-history tokens."""
    agg: dict[str, list[CaseResult]] = {}
    for c in cases:
        for strat, res in run_case(c, store=store, embedder=embedder, k=k).items():
            agg.setdefault(strat, []).append(res)
    summary = {}
    n = len(cases) or 1
    full_tokens = sum(c.baseline_full_tokens for c in cases) / n
    for strat, results in agg.items():
        mean_tokens = sum(r.tokens_sent for r in results) / n
        summary[strat] = {
            "recall@k": sum(r.recall for r in results) / n,
            "ndcg@k": sum(r.ndcg for r in results) / n,
            "tokens_sent": mean_tokens,
            "token_savings_vs_full": 1.0 - (mean_tokens / full_tokens) if full_tokens else 0.0,
            "latency_ms_p50": sorted(r.latency_ms for r in results)[len(results)//2],
        }
    return summary
```

- [ ] **Step 8: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_eval_runner.py tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add memorable/eval tests/test_metrics.py tests/test_eval_runner.py && git commit -m "feat: eval metrics + baselines (no-memory/naive-RAG/memory) + suite runner"
```

---

### Task 8: CLI — ingest | query | eval (run on the REAL corpus)

**Files:**
- Create: `memorable/cli.py`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/test_cli_smoke.py
from typer.testing import CliRunner
from memorable.cli import app

def test_cli_query_smoke(tmp_path, monkeypatch):
    runner = CliRunner()
    db = str(tmp_path/"m.db")
    # ingest the bundled fixture transcript
    r1 = runner.invoke(app, ["ingest-cc", "tests/fixtures/sample.jsonl",
                             "--project","stablex","--db",db,"--fake"])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(app, ["query","auth refresh loop","--project","stablex","--db",db,"--fake"])
    assert r2.exit_code == 0
    assert "auth" in r2.output.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -v`
Expected: FAIL (`memorable.cli` missing).

- [ ] **Step 3: Implement CLI**

```python
# memorable/cli.py
from __future__ import annotations
import json
from pathlib import Path
import typer
from memorable.store.sqlite_store import SqliteStore
from memorable.retrieve.retriever import Retriever
from memorable.types import Scope
from memorable.ingest.claude_code import parse_transcript

app = typer.Typer(no_args_is_help=True)

def _embedder(fake: bool):
    if fake:
        from memorable.embed.fake import FakeEmbedder
        return FakeEmbedder(dim=16)
    from memorable.embed.local import LocalEmbedder
    return LocalEmbedder()

@app.command("ingest-cc")
def ingest_cc(path: str, project: str = typer.Option(...), db: str = "memorable.db", fake: bool = False):
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    arts = parse_transcript(Path(path), project=project)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")

@app.command("query")
def query(text: str, project: str = typer.Option(None), db: str = "memorable.db",
          k: int = 8, fake: bool = False):
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    r = Retriever(s, e, k=k)
    trace = r.query(text, Scope(project=project))
    for h in trace.hits:
        typer.echo(f"[{h.score:.3f}] {h.artifact.id} :: {h.artifact.text[:100]}")
    typer.echo(f"-- {len(trace.hits)} hits, {trace.latency_ms:.1f}ms, "
               f"{sum(h.artifact.token_count for h in trace.hits)} tokens")

@app.command("eval")
def eval_cmd(cases_path: str, db: str = "memorable.db", k: int = 8, fake: bool = False):
    from memorable.eval.dataset import EvalCase
    from memorable.eval.runner import run_suite
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    raw = json.loads(Path(cases_path).read_text())
    cases = [EvalCase(query=c["query"], scope_project=c["project"],
                      relevant_ids=set(c["relevant_ids"]),
                      baseline_full_tokens=c["baseline_full_tokens"]) for c in raw]
    summary = run_suite(cases, store=s, embedder=e, k=k)
    typer.echo(json.dumps(summary, indent=2))

if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Ingest the real corpus and get the first number**

Run:
```bash
.venv/bin/memorable ingest-cc ~/.claude/projects/-Users-nimit-Documents-stablex-stablex-saas/*.jsonl \
  --project stablex --db real.db   # (loop over files in a shell for-loop; one call per file)
.venv/bin/memorable query "auth refresh loop" --project stablex --db real.db
```
Expected: real hits from the stablex corpus, latency printed. **This is the first honest end-to-end signal.**

- [ ] **Step 6: Commit**

```bash
git add memorable/cli.py tests/test_cli_smoke.py && git commit -m "feat: CLI (ingest-cc/query/eval)"
```

---

### Task 9: LLM interface + distiller (write path: typed memories, dedup, supersede)

**Files:**
- Create: `memorable/llm/{__init__.py,base.py,openai_compat.py,anthropic.py}`, `memorable/distill/{__init__.py,distiller.py}`
- Test: `tests/test_distiller.py`

- [ ] **Step 1: Write the failing test (with a fake LLM)**

```python
# tests/test_distiller.py
from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.distill.distiller import Distiller
from memorable.types import Artifact
import json

class FakeLLM:
    def __init__(self, payload): self.payload = payload
    def complete(self, prompt, *, max_tokens=1024): return json.dumps(self.payload)

def chunk(i, text, t):
    return Artifact(id=i, kind="session_chunk", project="p", source="cc",
                    text=text, token_count=len(text.split()), created_at=t,
                    meta={"session_id":"s1"})

def test_distill_creates_typed_memories_with_provenance(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    chunks = [chunk("s1:0","we decided to use bcrypt for hashing",10)]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    llm = FakeLLM({"memories":[{"type":"decision","text":"Use bcrypt for password hashing"}]})
    d = Distiller(s, e, llm)
    mem_ids = d.distill_session("s1", chunks, project="p")
    assert len(mem_ids) == 1
    # provenance edge memory --derived_from--> source chunk
    nbrs = [a.id for a in s.neighbors(mem_ids, ["derived_from"], hops=1)]
    assert "s1:0" in nbrs

def test_supersede_on_contradiction(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    c1 = [chunk("s1:0","use bcrypt for hashing",10)]
    s.add_artifacts(c1, e.embed([c.text for c in c1]))
    d = Distiller(s, e, FakeLLM({"memories":[{"type":"decision","text":"Use bcrypt for hashing"}]}))
    old_ids = d.distill_session("s1", c1, project="p")
    c2 = [chunk("s2:0","switch from bcrypt to argon2 for hashing",20)]
    s.add_artifacts(c2, e.embed([c.text for c in c2]))
    d2 = Distiller(s, e, FakeLLM({"memories":[
        {"type":"decision","text":"Use argon2 for hashing","supersedes_text":"Use bcrypt for hashing"}]}))
    new_ids = d2.distill_session("s2", c2, project="p")
    # old memory deactivated, new active
    from memorable.types import Scope
    active = [a.id for a,_ in s.search(e.embed(["hashing"])[0], Scope(project="p"), k=10)]
    assert new_ids[0] in active and old_ids[0] not in active
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_distiller.py -v`
Expected: FAIL (`memorable.distill.distiller` missing).

- [ ] **Step 3: Implement LLM base + distiller**

```python
# memorable/llm/base.py
DISTILL_PROMPT = """You are distilling a coding session into reusable memory.
Return STRICT JSON: {{"memories":[{{"type":"decision|lesson|snippet|bugfix",
"text":"<one concise reusable fact>", "supersedes_text":"<prior fact this reverses, or omit>"}}]}}
Only include durable, reusable facts. Session text:
---
{session_text}
---"""
```

```python
# memorable/distill/distiller.py
from __future__ import annotations
import hashlib, json
from memorable.types import Artifact, Scope
from memorable.llm.base import DISTILL_PROMPT

DEDUP_SIM_THRESHOLD = 0.92

class Distiller:
    def __init__(self, store, embedder, llm):
        self.store, self.embedder, self.llm = store, embedder, llm

    def distill_session(self, session_id: str, chunks: list[Artifact], project: str) -> list[str]:
        session_text = "\n".join(c.text for c in chunks)
        raw = self.llm.complete(DISTILL_PROMPT.format(session_text=session_text))
        data = json.loads(raw)
        created = max((c.created_at for c in chunks), default=0.0)
        new_ids: list[str] = []
        for m in data.get("memories", []):
            text = m["text"].strip()
            mid = f"mem:{session_id}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
            vec = self.embedder.embed([text])[0]
            # dedup: skip if a near-identical active memory already exists in project
            existing = self.store.search(vec, Scope(project=project, kinds=["memory"]), k=1)
            if existing and existing[0][1] >= DEDUP_SIM_THRESHOLD:
                continue
            art = Artifact(id=mid, kind="memory", project=project, source="distill",
                           text=text, token_count=max(1, len(text)//4), created_at=created,
                           meta={"mem_type": m["type"], "session_id": session_id})
            self.store.add_artifacts([art], [vec])
            # provenance edge: memory derived_from each chunk of this session
            for c in chunks:
                self.store.add_edge(mid, c.id, "derived_from")
            # supersede: deactivate the prior memory this one reverses
            sup = m.get("supersedes_text")
            if sup:
                prior = self.store.search(self.embedder.embed([sup])[0],
                                          Scope(project=project, kinds=["memory"]), k=1)
                if prior and prior[0][1] >= 0.8 and prior[0][0].id != mid:
                    self.store.deactivate(prior[0][0].id, superseded_by=mid)
            new_ids.append(mid)
        return new_ids
```

```python
# memorable/llm/openai_compat.py
import httpx
class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url, api_key, model
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        r = httpx.post(f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "max_tokens": max_tokens,
                  "messages": [{"role":"user","content":prompt}]}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
```

```python
# memorable/llm/anthropic.py
class AnthropicLLM:
    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        msg = self.client.messages.create(model=self.model, max_tokens=max_tokens,
            messages=[{"role":"user","content":prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_distiller.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add memorable/llm memorable/distill tests/test_distiller.py && git commit -m "feat: LLM interface + distiller (typed memories, dedup, supersede)"
```

---

### Task 10: Contradiction/supersede eval + edge-expansion ablation

**Files:**
- Modify: `memorable/eval/runner.py` (add `run_ablation`, `run_contradiction_eval`)
- Test: `tests/test_eval_ablation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_ablation.py
from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.eval.runner import run_ablation, run_contradiction_eval
from memorable.types import Artifact

def mk(i,text,t,kind="session_chunk"): return Artifact(id=i,kind=kind,project="p",source="t",
    text=text,token_count=len(text.split()),created_at=t,meta={})

def test_edge_expansion_ablation_reports_both(tmp_path):
    e=FakeEmbedder(dim=16); s=SqliteStore(str(tmp_path/"m.db"),dim=16)
    s.add_artifacts([mk("bug","emscripten sync crash",100),mk("fix","added mutex to queue",100)],
                    e.embed(["emscripten sync crash","added mutex to queue"]))
    s.add_edge("bug","fix","fixes")
    res = run_ablation(query="emscripten sync crash", project="p",
                       relevant_ids={"fix"}, store=s, embedder=e, k=2)
    assert "similarity-only" in res and "similarity+edges" in res
    assert res["similarity+edges"]["recall@k"] >= res["similarity-only"]["recall@k"]

def test_contradiction_eval_prefers_new(tmp_path):
    e=FakeEmbedder(dim=16); s=SqliteStore(str(tmp_path/"m.db"),dim=16)
    s.add_artifacts([mk("old","use bcrypt",10,kind="memory")], e.embed(["use bcrypt"]))
    s.add_artifacts([mk("new","use argon2",20,kind="memory")], e.embed(["use argon2"]))
    s.deactivate("old", superseded_by="new")
    ok = run_contradiction_eval(query="hashing algorithm", project="p",
                                stale_id="old", current_id="new", store=s, embedder=e, k=5)
    assert ok is True   # returns current, suppresses stale
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_eval_ablation.py -v`
Expected: FAIL (`run_ablation`/`run_contradiction_eval` missing).

- [ ] **Step 3: Implement in `runner.py` (append)**

```python
# append to memorable/eval/runner.py
def run_ablation(*, query, project, relevant_ids, store, embedder, k=8):
    out = {}
    for name, expand in (("similarity-only", False), ("similarity+edges", True)):
        r = Retriever(store, embedder, k=k, recency_weight=0.0, edge_expand=expand)
        tr = r.query(query, Scope(project=project))
        ids = [h.artifact.id for h in tr.hits]
        out[name] = {"recall@k": recall_at_k(ids, relevant_ids, k),
                     "ndcg@k": ndcg_at_k(ids, relevant_ids, k)}
    return out

def run_contradiction_eval(*, query, project, stale_id, current_id, store, embedder, k=8):
    r = Retriever(store, embedder, k=k, edge_expand=True)
    ids = [h.artifact.id for h in r.query(query, Scope(project=project)).hits]
    return (current_id in ids) and (stale_id not in ids)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_eval_ablation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/eval/runner.py tests/test_eval_ablation.py && git commit -m "feat: edge-expansion ablation + contradiction/supersede eval"
```

---

### Task 11: Counterfactual dataset builder (auto-label from real transcripts)

**Files:**
- Modify: `memorable/eval/dataset.py` (add `build_counterfactual_cases`)
- Test: `tests/test_dataset_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset_builder.py
from memorable.eval.dataset import build_counterfactual_cases
from memorable.types import Artifact

def art(i, sid, text, t):
    return Artifact(id=i, kind="session_chunk", project="p", source="cc",
                    text=text, token_count=len(text.split()), created_at=t,
                    meta={"session_id": sid, "ord": int(i.split(":")[1])})

def test_build_cases_uses_first_turn_as_query_and_rest_as_need():
    # session s2 references "auth refresh" which session s1 already covered
    arts = [art("s1:0","s1","implemented auth refresh token rotation",10),
            art("s2:0","s2","the auth refresh token rotation is looping",100),
            art("s2:1","s2","root cause was reissuing token on 401",105)]
    cases = build_counterfactual_cases(arts, project="p", min_prior_sessions=1)
    assert len(cases) >= 1
    c = cases[0]
    assert "auth refresh" in c.query.lower()         # query = held-out session's opening turn
    assert "s1:0" in c.relevant_ids                   # prior session that should be recalled
    assert c.baseline_full_tokens > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_dataset_builder.py -v`
Expected: FAIL (`build_counterfactual_cases` missing).

- [ ] **Step 3: Implement builder**

```python
# append to memorable/eval/dataset.py
import re
from memorable.types import Artifact

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{3,}")

def build_counterfactual_cases(artifacts: list["Artifact"], *, project: str,
                               min_prior_sessions: int = 1) -> list[EvalCase]:
    """For each session N (by start time), use its opening turn as the query and
    label prior-session chunks that share salient tokens as the context it needed."""
    by_session: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_session.setdefault(a.meta["session_id"], []).append(a)
    for v in by_session.values():
        v.sort(key=lambda a: a.meta.get("ord", 0))
    sessions = sorted(by_session.items(), key=lambda kv: kv[1][0].created_at)

    cases: list[EvalCase] = []
    for idx, (sid, chunks) in enumerate(sessions):
        if idx < min_prior_sessions:
            continue
        opening = chunks[0].text
        need_tokens = {w.lower() for w in _WORD.findall(opening)}
        prior = [a for j, (_, cs) in enumerate(sessions) if j < idx for a in cs]
        relevant = {a.id for a in prior
                    if need_tokens & {w.lower() for w in _WORD.findall(a.text)}}
        if not relevant:
            continue
        full_tokens = sum(a.token_count for a in prior)   # cost of dumping all prior history
        cases.append(EvalCase(query=opening, scope_project=project,
                              relevant_ids=relevant, baseline_full_tokens=full_tokens))
    return cases
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_dataset_builder.py -v`
Expected: PASS.

- [ ] **Step 5: Wire a `build-cases` CLI command, then generate + run on real corpus**

Add to `memorable/cli.py`:
```python
@app.command("build-cases")
def build_cases(project: str = typer.Option(...), db: str = "memorable.db",
                out: str = "cases.json", fake: bool = False):
    import json
    from memorable.eval.dataset import build_counterfactual_cases
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'", (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_counterfactual_cases(arts, project=project)
    Path(out).write_text(json.dumps([{"query":c.query,"project":c.scope_project,
        "relevant_ids":sorted(c.relevant_ids),"baseline_full_tokens":c.baseline_full_tokens} for c in cases], indent=2))
    typer.echo(f"wrote {len(cases)} cases to {out}")
```
Run:
```bash
.venv/bin/memorable build-cases --project stablex --db real.db --out stablex_cases.json
.venv/bin/memorable eval stablex_cases.json --db real.db
```
Expected: a JSON summary table — **recall@k, ndcg@k, token_savings_vs_full, latency_p50 per strategy (no-memory / naive-RAG / memory)**. This is the V0 success/kill check.

- [ ] **Step 6: Commit**

```bash
git add memorable/eval/dataset.py memorable/cli.py tests/test_dataset_builder.py && git commit -m "feat: counterfactual eval dataset builder + build-cases CLI"
```

---

### Task 12: Documents/research ingest

**Files:**
- Create: `memorable/ingest/documents.py`
- Test: `tests/test_ingest_documents.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_documents.py
from pathlib import Path
from memorable.ingest.documents import parse_document

def test_parse_markdown_chunks_by_heading(tmp_path):
    p = tmp_path/"notes.md"
    p.write_text("# Auth design\nUse argon2.\n\n# Sync\nMutex around queue.\n")
    arts = parse_document(p, project="stablex", kind="note")
    assert len(arts) == 2
    assert arts[0].kind == "note" and arts[0].project == "stablex"
    assert "argon2" in arts[0].text and "Auth design" in arts[0].text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_ingest_documents.py -v`
Expected: FAIL (`parse_document` missing).

- [ ] **Step 3: Implement**

```python
# memorable/ingest/documents.py
from __future__ import annotations
import hashlib, re
from pathlib import Path
from memorable.types import Artifact

def parse_document(path: Path, project: str, kind: str = "note",
                   created_at: float = 0.0) -> list[Artifact]:
    text = path.read_text()
    # split on markdown headings; keep the heading with its body
    parts = re.split(r"(?m)^(#{1,6}\s.*)$", text)
    chunks: list[str] = []
    buf = ""
    for seg in parts:
        if re.match(r"^#{1,6}\s", seg or ""):
            if buf.strip():
                chunks.append(buf.strip())
            buf = seg + "\n"
        else:
            buf += seg
    if buf.strip():
        chunks.append(buf.strip())
    arts = []
    for i, c in enumerate(chunks):
        cid = f"{kind}:{path.stem}:{hashlib.sha1(c.encode()).hexdigest()[:8]}"
        arts.append(Artifact(id=cid, kind=kind, project=project, source=str(path),
                             text=c, token_count=max(1, len(c)//4),
                             created_at=created_at, meta={"path": str(path), "ord": i}))
    return arts
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_ingest_documents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memorable/ingest/documents.py tests/test_ingest_documents.py && git commit -m "feat: markdown/research document ingest"
```

---

### Task 13: External comparative baseline adapters (claude-mem, graphiti)

**Files:**
- Create: `memorable/eval/baselines/__init__.py`, `memorable/eval/baselines/base.py`, `claude_mem.py`, `graphiti.py`
- Test: `tests/test_external_baselines.py`

- [ ] **Step 1: Write the failing test (adapter contract, fakes only — no network)**

```python
# tests/test_external_baselines.py
from memorable.eval.baselines.base import ExternalBaseline

class StubBaseline(ExternalBaseline):
    name = "stub"
    def available(self): return True
    def index(self, artifacts): self._texts = {a.id: a.text for a in artifacts}
    def retrieve(self, query, project, k): return list(self._texts.keys())[:k]

def test_external_baseline_contract():
    from memorable.types import Artifact
    b = StubBaseline()
    assert b.available()
    b.index([Artifact(id="x",kind="note",project="p",source="t",text="hi",
                      token_count=1,created_at=0,meta={})])
    assert b.retrieve("q","p",5) == ["x"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_external_baselines.py -v`
Expected: FAIL (`memorable.eval.baselines.base` missing).

- [ ] **Step 3: Implement the contract + thin adapters**

```python
# memorable/eval/baselines/base.py
from __future__ import annotations
from typing import Protocol
from memorable.types import Artifact

class ExternalBaseline(Protocol):
    name: str
    def available(self) -> bool: ...
    def index(self, artifacts: list[Artifact]) -> None: ...
    def retrieve(self, query: str, project: str, k: int) -> list[str]: ...
```

```python
# memorable/eval/baselines/graphiti.py
from memorable.types import Artifact

class GraphitiBaseline:
    """Adapter for Zep Graphiti. Requires `pip install graphiti-core` + a Neo4j/FalkorDB
    instance. Behind a flag; skipped when unavailable so the suite still runs."""
    name = "graphiti"
    def __init__(self): self._client = None
    def available(self) -> bool:
        try:
            import graphiti_core  # noqa
            return True
        except Exception:
            return False
    def index(self, artifacts: list[Artifact]) -> None:
        raise NotImplementedError("wire to graphiti_core.add_episode per artifact")
    def retrieve(self, query: str, project: str, k: int) -> list[str]:
        raise NotImplementedError("wire to graphiti_core search; map results back to artifact ids")
```

```python
# memorable/eval/baselines/claude_mem.py
from memorable.types import Artifact

class ClaudeMemBaseline:
    """Adapter for the claude-mem plugin store. Behind a flag; skipped when its
    store path is absent so the suite still runs."""
    name = "claude-mem"
    def available(self) -> bool:
        import os
        return os.path.exists(os.path.expanduser("~/.claude-mem"))
    def index(self, artifacts: list[Artifact]) -> None:
        raise NotImplementedError("point at the claude-mem store or re-ingest via its API")
    def retrieve(self, query: str, project: str, k: int) -> list[str]:
        raise NotImplementedError("query claude-mem; map results back to artifact ids")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_external_baselines.py -v`
Expected: PASS. (Real adapters raise `NotImplementedError` until wired; they're skipped via `available()`.)

- [ ] **Step 5: Commit**

```bash
git add memorable/eval/baselines tests/test_external_baselines.py && git commit -m "feat: external comparative baseline contract + graphiti/claude-mem adapter stubs"
```

---

### Task 14: Claude Code recall skill (the delivery vehicle)

**Files:**
- Create: `skill/SKILL.md`, `skill/recall.py`
- Test: `tests/test_skill_recall.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_recall.py
import json, subprocess, sys
from pathlib import Path

def test_recall_script_outputs_context_block(tmp_path):
    # build a tiny db via the CLI (fake embedder), then call the skill entrypoint
    db = str(tmp_path/"m.db")
    subprocess.run([sys.executable,"-m","memorable.cli","ingest-cc",
                    "tests/fixtures/sample.jsonl","--project","stablex","--db",db,"--fake"],check=True)
    out = subprocess.run([sys.executable,"skill/recall.py","--query","auth refresh loop",
                          "--project","stablex","--db",db,"--fake","--k","3"],
                         capture_output=True,text=True,check=True)
    payload = json.loads(out.stdout)
    assert "context" in payload and "trace" in payload
    assert payload["trace"]["hits"] >= 1
    assert "auth" in payload["context"].lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_skill_recall.py -v`
Expected: FAIL (`skill/recall.py` missing).

- [ ] **Step 3: Implement the skill entrypoint + SKILL.md**

```python
# skill/recall.py
from __future__ import annotations
import argparse, json
from memorable.store.sqlite_store import SqliteStore
from memorable.retrieve.retriever import Retriever
from memorable.types import Scope

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--db", default="memorable.db")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--fake", action="store_true")
    a = p.parse_args()
    if a.fake:
        from memorable.embed.fake import FakeEmbedder; e = FakeEmbedder(dim=16)
    else:
        from memorable.embed.local import LocalEmbedder; e = LocalEmbedder()
    s = SqliteStore(a.db, dim=e.dim)
    trace = Retriever(s, e, k=a.k).query(a.query, Scope(project=a.project))
    context = "\n\n".join(f"- ({h.artifact.kind}) {h.artifact.text}" for h in trace.hits)
    print(json.dumps({
        "context": context,
        "trace": {"hits": len(trace.hits), "latency_ms": round(trace.latency_ms,1),
                  "tokens": sum(h.artifact.token_count for h in trace.hits),
                  "scored": [{"id":h.artifact.id,"score":round(h.score,3),
                              "components":h.components} for h in trace.hits]}}))

if __name__ == "__main__":
    main()
```

```markdown
<!-- skill/SKILL.md -->
---
name: memorable-recall
description: Recall relevant past coding/research context for a query, scoped to a project, instead of re-deriving it. Use when starting work that resembles past sessions, or when the user asks "what did we decide / how did we fix X".
---

# Memorable Recall

Run: `python skill/recall.py --query "<task or question>" --project "<project>" --db <path> --k 8`

Returns JSON: `context` (a compact block to paste into your working context) and
`trace` (scored hits with component breakdown for inspection). Always pass `--project`
to scope retrieval and minimize tokens. Read the `context`, cite artifact ids when you
use a fact, and prefer recalled decisions over re-deriving them.
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_skill_recall.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest -q`
Expected: all tests pass.
```bash
git add skill tests/test_skill_recall.py && git commit -m "feat: claude code recall skill wrapping query()"
```

---

## Self-Review

**Spec coverage:**
- Universal artifact store → Task 4 (`artifacts` table, all `kind`s) ✓
- Pluggable Embedder/LLM → Tasks 2,3,9 ✓
- Claude Code + documents ingest → Tasks 5,12 ✓
- Scope-filtered retrieval + recency + edge expansion + trace → Task 6 ✓
- Distill / dedup / supersede → Task 9 ✓
- Edges table + CTE traversal → Task 4; ablation → Task 10 ✓
- Eval: internal baselines (no-memory/last-N/naive-RAG) → Task 7; counterfactual cases → Task 11; contradiction eval → Task 10; external baselines (claude-mem/graphiti) → Task 13 ✓
- Skill delivery → Task 14 ✓
- *Note:* `last-N` baseline is named in the spec but Task 7 ships `no-memory`/`naive-RAG`/`memory`. ADD `last-N` to `run_case` in Task 7 (retrieve the most recent k chunks in scope by `created_at`, no vector search) — small, do it during Task 7 implementation.
- Amortized token accounting (write-cost ÷ reuses) is specified; Task 7/11 report read-side `token_savings_vs_full`. Full amortization needs distill token counts — track in a follow-up once distill runs at corpus scale (logged as a known gap, not silently dropped).

**Placeholder scan:** No TBD/TODO in code steps; external baseline adapters intentionally raise `NotImplementedError` and are gated by `available()` (documented, not a placeholder).

**Type consistency:** `Scope`/`Artifact`/`Hit`/`RetrievalTrace` signatures consistent across Tasks 1–14. `MemoryStore` methods (`add_artifacts`, `add_edge`, `search`, `neighbors`, `deactivate`) match between interface (Task 2) and impl (Task 4) and all callers. `Retriever(store, embedder, *, k, recency_weight, edge_expand)` consistent in Tasks 6,7,10,14.

## Known gaps / fast-follows (tracked, not hidden)
- Add `last-N` baseline in Task 7 (noted above).
- Amortized write-cost accounting once distillation runs at scale.
- External baseline adapters (`graphiti`, `claude-mem`) need real wiring beyond the contract.
