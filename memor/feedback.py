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

_REJECTION_PATTERNS = [
    "no that's wrong", "that's not right", "that's incorrect", "that's outdated",
    "no, we", "no we", "actually we", "actually, we",
    "we switched", "we moved", "we changed", "we no longer",
    "that's not how", "not what i meant", "wrong approach",
    "we don't use", "we stopped using", "we dropped",
]

_CONTRADICTION_PATTERNS = [
    "however, looking at the current code",
    "but the current implementation",
    "actually use", "actually uses",
    "instead of what the memory",
    "contrary to the recalled",
    "the memory is outdated",
    "this is no longer",
]


def _extract_texts(transcript_path: Path) -> tuple[list[str], list[str]]:
    """Extract (assistant_texts, user_texts) from a transcript JSONL."""
    assistant_texts = []
    user_texts = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message", {})
        content = msg.get("content", "")
        parts = []
        if isinstance(content, str):
            parts.append(content.lower())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", "").lower())
        if rec.get("type") == "assistant":
            assistant_texts.extend(parts)
        elif rec.get("type") == "human":
            user_texts.extend(parts)
    return assistant_texts, user_texts


def _extract_assistant_texts(transcript_path: Path) -> list[str]:
    assistant_texts, _ = _extract_texts(transcript_path)
    return assistant_texts


def _detect_negative_signals(assistant_texts: list[str], user_texts: list[str]) -> bool:
    """Detect if user rejected or assistant contradicted recalled content."""
    combined_user = " ".join(user_texts)
    for pattern in _REJECTION_PATTERNS:
        if pattern in combined_user:
            return True

    combined_assistant = " ".join(assistant_texts)
    for pattern in _CONTRADICTION_PATTERNS:
        if pattern in combined_assistant:
            return True

    return False


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

    assistant_texts, user_texts = _extract_texts(transcript_path)
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

    if _detect_negative_signals(assistant_texts, user_texts) and recalled_ids:
        store.record_negative(list(recalled_ids))

    return len(used_ids)
