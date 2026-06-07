"""Tests for semantic feedback — detects memory usage via embedding similarity,
not just n-gram overlap."""
import json
import time
from pathlib import Path

from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
from memor.feedback import _text_was_used, _semantic_match, analyze_session_feedback


def test_ngram_catches_verbatim_reuse():
    assert _text_was_used(
        "we use argon2 for password hashing",
        ["the auth module uses argon2 for password hashing as decided earlier"],
    )


def test_ngram_misses_paraphrase():
    """N-gram matching can't catch paraphrased reuse — this is the gap."""
    result = _text_was_used(
        "we decided to use argon2 for password hashing in the auth module",
        ["the authentication system employs argon2 to hash credentials securely"],
    )
    # N-grams should miss this — the wording is completely different
    assert result is False


class _ControlledEmbedder:
    """Embedder that maps exact texts to fixed vectors for controlled cosine values."""
    def __init__(self, mapping):
        self.dim = len(next(iter(mapping.values())))
        self._map = mapping

    def embed(self, texts):
        return [self._map[t] for t in texts]


def test_semantic_match_catches_paraphrase():
    """Embedding similarity should catch paraphrased reuse that n-grams miss."""
    memory = "argon2 password hashing decision for the auth module"
    response = "the auth module uses argon2 hashing for passwords as we decided"
    emb = _ControlledEmbedder({
        memory: [1.0, 0.0],
        response: [0.9, 0.4],  # cosine ≈ 0.91 — clearly similar
    })
    assert _semantic_match(memory, response, emb) is True


def test_semantic_match_rejects_unrelated():
    memory = "argon2 password hashing decision for the auth module"
    response = "the CSS flexbox layout needs to be centered with proper margins"
    emb = _ControlledEmbedder({
        memory: [1.0, 0.0],
        response: [0.0, 1.0],  # cosine = 0 — orthogonal
    })
    assert _semantic_match(memory, response, emb) is False


def _write_transcript(tmp_path, session_id, assistant_texts):
    """Write a minimal transcript file for feedback analysis."""
    p = tmp_path / f"{session_id}.jsonl"
    lines = []
    for text in assistant_texts:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": text},
        }))
    p.write_text("\n".join(lines))
    return p


def test_feedback_uses_semantic_when_ngram_fails(tmp_path):
    """Full pipeline: memory was paraphrased in the response. N-gram misses it,
    but semantic feedback catches it and records usage."""
    embedder = FakeEmbedder(dim=16)
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    # Store a memory
    mem = Artifact(
        id="mem1", kind="memory", project="p", source="distill",
        text="we use argon2 for password hashing in the auth module",
        token_count=10, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "old-sess"},
    )
    store.add_artifacts([mem], embedder.embed([mem.text]))

    # Simulate a recall event for this session
    now = time.time()
    store.log_recall(project="p", query_preview="auth hashing",
                     hits_count=1, top_score=0.8, tokens_injected=50,
                     latency_ms=5.0, status="ok", session_id="sess1")
    store.record_recall(["mem1"])

    # Write transcript where the agent paraphrases the memory
    transcript = _write_transcript(tmp_path, "sess1", [
        "the auth module uses argon2 hashing for passwords as we decided"
    ])

    used = analyze_session_feedback(store, "sess1", transcript, embedder=embedder)
    assert used >= 1  # semantic match should catch the paraphrase
