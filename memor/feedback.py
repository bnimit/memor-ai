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


def _record_epoch(rec: dict) -> float:
    ts = rec.get("timestamp")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        from datetime import datetime
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _extract_stamped_texts(
    transcript_path: Path,
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """(assistant, user) texts paired with when they were written.

    Timestamps are the whole point: a memory can only have been used by text
    written *after* it was recalled, and the previous version compared against
    the entire session in both directions.

    Note ``type == "user"``. The old code tested for ``"human"``, which no
    transcript emits, so the user channel was always empty and rejection
    detection ran on assistant prose alone.
    """
    assistant: list[tuple[float, str]] = []
    user: list[tuple[float, str]] = []
    try:
        lines = transcript_path.read_text(errors="replace").splitlines()
    except OSError:
        return [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("type")
        if kind not in ("assistant", "user"):
            continue
        content = rec.get("message", {}).get("content", "")
        parts = []
        if isinstance(content, str):
            parts.append(content.lower())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", "").lower())
        if not parts:
            continue
        stamped = (_record_epoch(rec), " ".join(parts))
        (assistant if kind == "assistant" else user).append(stamped)
    return assistant, user


def _rejected_after(stamped_user: list[tuple[float, str]],
                    assistant_after: list[str], since: float) -> bool:
    """Did the user push back, or the assistant contradict, after this recall?"""
    for ts, text in stamped_user:
        if (ts <= 0.0 or ts >= since) and any(p in text for p in _REJECTION_PATTERNS):
            return True
    joined = " ".join(assistant_after)
    return any(p in joined for p in _CONTRADICTION_PATTERNS)


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


def _session_recalls(store: SqliteStore, session_id: str,
                     conversation_key: str) -> list[dict]:
    """Recalls belonging to this session, from either delivery path.

    Hook-served recalls carry the session id. Proxy-served ones cannot — no
    agent sends a session header — so they are found by the conversation key
    both sides derive from the opening message.
    """
    clauses, params = [], []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if conversation_key:
        clauses.append("conversation_key = ?")
        params.append(conversation_key)
    if not clauses:
        return []
    rows = store.db.execute(
        f"SELECT id, timestamp FROM recall_log WHERE hits_count > 0 "
        f"AND ({' OR '.join(clauses)}) ORDER BY timestamp", params).fetchall()
    return [{"id": r["id"], "timestamp": r["timestamp"] or 0.0} for r in rows]


def analyze_session_feedback(
    store: SqliteStore, session_id: str, transcript_path: Path,
    *, embedder=None, conversation_key: str = "",
) -> int:
    """Settle the verdict on every memory this session was actually served.

    Rewritten against four defects that between them produced 2,180 uses and
    1,770 rejections for an artifact recalled 40 times:

    * usage was attributed by time window over ``last_recalled``, a single
      mutable column, so a long session credited every artifact in the project
      rather than the eight that were served;
    * one rejection phrase anywhere in a session penalised every one of them;
    * user turns were matched on ``type == "human"``, which never appears in a
      transcript, so the rejection channel read only assistant text and fired
      on phrases as ordinary as "actually use";
    * counters were incremented, so re-analysing a session inflated them again.

    Now each verdict hangs off the recall that served it, is judged only
    against what the assistant wrote *after* that recall, and is written once
    to a pending row that cannot be settled twice.
    """
    recalls = _session_recalls(store, session_id, conversation_key)
    if not recalls:
        return 0
    pending = store.pending_outcomes([r["id"] for r in recalls])
    if not pending:
        return 0

    stamped_assistant, stamped_user = _extract_stamped_texts(transcript_path)
    # A user correction with no assistant reply after it is still evidence —
    # arguably the strongest kind — so this bails only when the transcript
    # says nothing at all.
    if not stamped_assistant and not stamped_user:
        return 0

    at = {r["id"]: r["timestamp"] for r in recalls}
    texts_cache: dict[str, str] = {}
    verdicts: list[tuple[int, str, str, str]] = []

    for item in pending:
        rid, aid = item["recall_id"], item["artifact_id"]
        if aid not in texts_cache:
            row = store.db.execute(
                "SELECT text FROM artifacts WHERE id=?", (aid,)).fetchone()
            texts_cache[aid] = row["text"] if row else ""
        text = texts_cache[aid]
        if not text:
            continue

        # Only what came after the recall can be evidence of using it. A record
        # with no usable timestamp is kept rather than dropped: excluding it
        # would mark every memory in a transcript that lacks timestamps
        # "unused", poisoning the labels exactly as the old bug did, only in
        # the other direction.
        since = at.get(rid, 0.0)
        after = [t for ts, t in stamped_assistant if ts <= 0.0 or ts >= since]

        # Rejection outranks use, and is tested before the "nothing followed"
        # shortcut: a user can correct a memory without the assistant writing
        # anything afterwards, and that correction is the strongest signal
        # available. A memory the agent acted on and was then corrected for is
        # harmful, not helpful, and scoring it "used" would promote exactly the
        # memories that cost the user a correction. Attributable because it is
        # scoped to this recall's window; the old blanket version could not say
        # which memory was wrong.
        if _rejected_after(stamped_user, after, since):
            verdicts.append((rid, aid, "rejected", "phrase"))
        elif not after:
            verdicts.append((rid, aid, "unused", ""))
        elif _text_was_used(text, after):
            verdicts.append((rid, aid, "used", "ngram"))
        elif embedder and _semantic_match(text, " ".join(after), embedder):
            verdicts.append((rid, aid, "used", "semantic"))
        else:
            # An unused hit is a clean negative, not a missing label: the agent
            # saw it and did not use it.
            verdicts.append((rid, aid, "unused", ""))

    store.resolve_outcomes(verdicts)
    return sum(1 for _, _, outcome, _ in verdicts if outcome == "used")
