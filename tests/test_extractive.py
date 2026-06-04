"""Tests for extractive (LLM-free) distillation."""
from memor.distill.extractive import extract_key_chunks, _tfidf_scores, _heuristic_score, classify_chunk
from memor.distill.distiller import ExtractiveDistiller
from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _chunk(i, text, tok=None, role="assistant"):
    return Artifact(id=f"s1:{i}", kind="session_chunk", project="p", source="cc",
                    text=text, token_count=tok or max(1, len(text) // 4),
                    created_at=100.0 + i, meta={"session_id": "s1", "role": role, "ord": i})


def test_tfidf_scores_nonzero_for_content():
    chunks = [
        _chunk(0, "argon2 hashing algorithm decision for auth module"),
        _chunk(1, "let me check the code now"),
        _chunk(2, "argon2 is more resistant to GPU attacks than bcrypt"),
    ]
    scores = _tfidf_scores(chunks)
    assert all(s > 0 for s in scores)
    # Chunks with more unique terms should generally score higher
    # but the combined score (TF-IDF + heuristic) is what matters, not TF-IDF alone
    assert len(scores) == 3


def test_heuristic_drops_short_chunks():
    short = _chunk(0, "ok", tok=2)
    assert _heuristic_score(short) <= -1.0


def test_heuristic_boosts_signal_patterns():
    decision = _chunk(0, "we decided to use argon2 for all password hashing going forward", tok=50)
    filler = _chunk(1, "Let me check the other files now", tok=20)
    assert _heuristic_score(decision) > _heuristic_score(filler)


def test_extract_key_chunks_reduces_count():
    e = FakeEmbedder(dim=16)
    chunks = [
        _chunk(0, "fix the auth refresh loop in the login handler", role="user", tok=40),
        _chunk(1, "Let me check the code", tok=5),
        _chunk(2, "The loop is caused by re-issuing the token on 401 without checking "
                  "the retry count. Here is the fix: we should add a max retry of 3 and "
                  "exponential backoff between attempts.", tok=120),
        _chunk(3, "Good, moving on", tok=4),
        _chunk(4, "we decided to use argon2 for password hashing because it is memory-hard "
                  "and resistant to GPU-based attacks", tok=80),
        _chunk(5, "Now let me check the tests", tok=7),
        _chunk(6, "Perfect!", tok=2),
        _chunk(7, "The invoice model uses UUID primary keys while all other models use "
                  "integer auto-increment PKs for compatibility with the legacy system", tok=90),
    ]
    selected = extract_key_chunks(chunks, e, max_extracts=3)
    assert len(selected) <= 3
    # The short filler chunks should NOT be in the selection
    selected_texts = [c.text for c in selected]
    assert "Let me check the code" not in selected_texts
    assert "Good, moving on" not in selected_texts
    assert "Perfect!" not in selected_texts


def test_extract_preserves_temporal_order():
    e = FakeEmbedder(dim=16)
    chunks = [_chunk(i, f"substantial content about topic {i} with enough words to pass filter", tok=50)
              for i in range(20)]
    selected = extract_key_chunks(chunks, e, max_extracts=5)
    ords = [c.meta["ord"] for c in selected]
    assert ords == sorted(ords)


def test_classify_chunk_decision():
    assert classify_chunk("we decided to use argon2 for password hashing") == "decision"
    assert classify_chunk("the approach is to use a connection pool") == "decision"

def test_classify_chunk_bugfix():
    assert classify_chunk("the fix is to add a retry counter") == "bugfix"
    assert classify_chunk("root cause was a race condition in the token refresh") == "bugfix"

def test_classify_chunk_lesson():
    assert classify_chunk("always use parameterized queries to prevent SQL injection") == "lesson"
    assert classify_chunk("never use eval() on user input") == "lesson"

def test_classify_chunk_snippet():
    assert classify_chunk("here is the implementation:\n```python\ndef foo():\n    pass\n```\n" + "x" * 200) == "snippet"

def test_classify_chunk_extract_fallback():
    assert classify_chunk("the module handles authentication for the web app") == "extract"


def test_extractive_distiller_stores_memories(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    chunks = [
        _chunk(0, "fix the auth refresh loop in the login handler which keeps reissuing tokens", tok=50, role="user"),
        _chunk(1, "The auth refresh loop bug is caused by re-issuing the token on every 401 "
                  "response without checking the retry count. The fix is to add a maximum "
                  "retry of 3 attempts with exponential backoff between each attempt.", tok=150),
    ]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    d = ExtractiveDistiller(s, e)
    mem_ids = d.distill_session("s1", chunks, project="p")
    assert len(mem_ids) >= 1
    mems = s.db.execute("SELECT COUNT(*) FROM artifacts WHERE kind='memory'").fetchone()[0]
    assert mems >= 1
    edges = s.db.execute("SELECT COUNT(*) FROM edges WHERE type='derived_from'").fetchone()[0]
    assert edges >= 1


def test_extractive_distiller_classifies_types(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    chunks = [
        _chunk(0, "the fix is to add a maximum retry of 3 attempts with exponential "
                  "backoff between each attempt to prevent the infinite loop", tok=150),
    ]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    d = ExtractiveDistiller(s, e)
    d.distill_session("s1", chunks, project="p")
    row = s.db.execute(
        "SELECT meta FROM artifacts WHERE kind='memory' LIMIT 1"
    ).fetchone()
    import json
    meta = json.loads(row["meta"])
    assert meta["mem_type"] == "bugfix"
