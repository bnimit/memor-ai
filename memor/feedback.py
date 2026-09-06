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
import re
from pathlib import Path
from memor.store.sqlite_store import SqliteStore

_NGRAM_SIZE = 3
_MIN_WORDS = 4
_MATCH_RATIO = 0.10
_SEMANTIC_SIM_THRESHOLD = 0.45

#: Explicit contradictions. Rare in practice but unambiguous when they appear.
_REJECTION_PATTERNS = [
    "no that's wrong", "that's not right", "that's incorrect", "that's outdated",
    "no, we", "no we",
    "we switched", "we moved", "we changed", "we no longer",
    "that's not how", "not what i meant", "wrong approach",
    "we don't use", "we stopped using", "we dropped",
]

#: The form disagreement actually takes: a rhetorical question that asserts a
#: correction while sounding like a query. On 947 real user turns the explicit
#: patterns above fired twice, once wrongly; every genuine correction in the
#: sample was one of these instead.
#:
#: Not anchored to sentence start, because real ones arrive mid-clause -- "When
#: we were working on the Gltf reader did we not cover the extensions?" -- and
#: anchoring cost four of nine labelled corrections. Selectivity comes instead
#: from requiring a negated auxiliary with a specific subject, a construction
#: that ordinary requests do not use.
#:
#: ``be we`` in the ``shouldn't`` branch is not a typo. Real corrections here
#: read "shouldn't be we fix those together", and a detector tuned only to
#: grammatical English misses the register it is meant to read.
_CORRECTIVE_FRAME = re.compile(
    r"\b(?:"
    r"i thought\b"
    r"|isn'?t\s+(?:it|that|this|the)\b"
    r"|wasn'?t\s+(?:it|that|this|the)\b"
    r"|shouldn'?t\s+(?:be\s+)?(?:we|it|that|this)\b"
    r"|didn'?t\s+(?:we|you|it|that)\b"
    r"|don'?t\s+we\b"
    r"|did\s+we\s+not\b"
    r"|aren'?t\s+(?:they|these|those|we)\b"
    r")",
    re.I,
)

#: "Actually we" reads as pushback in isolation and as agreement in
#: "Actually we can include yesterday's changes too", which was one of only two
#: hits the old list produced on real traffic. The distinguishing word is what
#: follows: a correction says what stopped being true.
_ACTUALLY_CORRECTION = re.compile(
    r"\bactually,?\s+we\s+"
    r"(?:don'?t|do\s+not|never|stopped|switched|moved|changed|dropped|no\s+longer)\b",
    re.I,
)

#: Harness-generated, not user text. An interrupt can mean the agent was wrong,
#: or the user changed their mind, or they hit the wrong key, and attributing
#: harm to whichever memory sat in context would be a guess.
_NOT_USER_SIGNAL = re.compile(r"^\s*\[request interrupted", re.I)


def looks_like_correction(text: str) -> bool:
    """True when a user turn pushes back on something the agent said.

    Feeds the rejection half of ``memory_quality``. Both failure modes here are
    silent and neither is safe: missing corrections leaves every quality score
    counting hits with no misses, and over-firing marks ordinary questions as
    harm, which would penalise whichever memory happened to be in context.
    """
    if not text:
        return False
    lowered = text.lower()
    if _NOT_USER_SIGNAL.match(lowered):
        return False
    if _ACTUALLY_CORRECTION.search(lowered):
        return True
    if _CORRECTIVE_FRAME.search(lowered):
        return True
    return any(p in lowered for p in _REJECTION_PATTERNS)


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


#: One conversational turn, normalised across agents:
#: ``(epoch_seconds, "assistant" | "user", lowercased_text)``.
#:
#: Every harness records the same three facts and disagrees only on how. Making
#: that the interface is what lets the analyzer grade a Goose session, which has
#: no transcript file at all -- its history is rows in SQLite, so a reader keyed
#: on a filesystem path could not express it.
StampedTurn = tuple[float, str, str]


def stamped_turns_from_claude(transcript_path: Path) -> list[StampedTurn]:
    """Turns from a Claude Code transcript.

    Note ``type == "user"``. The old code tested for ``"human"``, which no
    transcript emits, so the user channel was always empty and rejection
    detection ran on assistant prose alone.
    """
    turns: list[StampedTurn] = []
    try:
        lines = transcript_path.read_text(errors="replace").splitlines()
    except OSError:
        return []
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
        role = rec.get("type")
        if role not in ("assistant", "user"):
            continue
        text = _text_of_content(rec.get("message", {}).get("content", ""))
        if text:
            turns.append((_record_epoch(rec), role, text))
    return turns


def stamped_turns_from_jcode(session_path: Path) -> list[StampedTurn]:
    """Turns from a jcode session.

    jcode splits a session across a settled ``.json`` and a ``.journal.jsonl``
    of appends. ``memor.ingest.jcode`` already reconciles the two, so this
    reuses that rather than re-deriving the format in a second place.
    """
    from memor.ingest.jcode import _messages

    turns: list[StampedTurn] = []
    for msg in _messages(session_path):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("assistant", "user"):
            continue
        text = _text_of_content(msg.get("content", ""))
        if text:
            turns.append((_record_epoch(msg), role, text))
    return turns


def stamped_turns_from_goose(db_path: Path, session_id: str) -> list[StampedTurn]:
    """Turns from one Goose session.

    Goose keeps messages in SQLite with an integer ``created_timestamp``, so
    there is no transcript file to point at. Read-only URI, because this runs
    against the user's live Goose database while Goose may be writing to it.
    """
    import sqlite3

    if not Path(db_path).is_file():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT role, content_json, created_timestamp FROM messages "
            "WHERE session_id = ? ORDER BY created_timestamp, id",
            (session_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    from memor.ingest.goose import _text_from_content_json

    turns: list[StampedTurn] = []
    for row in rows:
        role = (row["role"] or "").strip()
        if role not in ("assistant", "user"):
            continue
        text = (_text_from_content_json(row["content_json"] or "") or "").strip()
        if text:
            turns.append((float(row["created_timestamp"] or 0), role, text.lower()))
    return turns


def _text_of_content(content) -> str:
    """Flatten a message body to lowercased text, whatever shape it arrived in.

    Agents disagree: a bare string, or a list of typed blocks. Only ``text``
    blocks are taken -- tool calls and results are the agent's machinery, not
    evidence that it used a memory.
    """
    if isinstance(content, str):
        return content.lower()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(parts).lower()
    return ""


def _extract_stamped_texts(
    transcript_path: Path,
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """(assistant, user) texts paired with when they were written.

    Timestamps are the whole point: a memory can only have been used by text
    written *after* it was recalled, and the previous version compared against
    the entire session in both directions.

    Retained as a thin adapter over :func:`stamped_turns_from_claude` so the
    split-channel shape the analyzer wants has one definition.
    """
    return _split_channels(stamped_turns_from_claude(transcript_path))


def _split_channels(
    turns: list[StampedTurn],
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """Separate normalised turns into the assistant and user channels."""
    assistant = [(ts, text) for ts, role, text in turns if role == "assistant"]
    user = [(ts, text) for ts, role, text in turns if role == "user"]
    return assistant, user


#: How long after a recall a correction can still be blamed on it.
#:
#: Ordering alone is not attribution. With the sharper detector, 85 of 170
#: 'used' verdicts on the local store had a correction somewhere later in the
#: session -- median gap 330 minutes, longest ten days. A working session
#: contains pushback about *something*, and charging it to whichever memory was
#: served that morning is the attribution bug the rewrite above already fixed
#: once, arriving by a different route.
#:
#: An hour is deliberately generous. A correction can take a few turns to
#: surface, and an unrecorded rejection costs one missing data point while a
#: misattributed one actively demotes a memory that did nothing wrong.
_REJECTION_WINDOW_S = 3600.0


def _rejected_after(stamped_user: list[tuple[float, str]],
                    assistant_after: list[str], since: float) -> bool:
    """Did the user push back, or the assistant contradict, after this recall?"""
    for ts, text in stamped_user:
        # An undated turn is kept rather than dropped: a transcript without
        # usable timestamps would otherwise contribute no evidence at all.
        dated = ts > 0.0
        if dated and not (since <= ts <= since + _REJECTION_WINDOW_S):
            continue
        if looks_like_correction(text):
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


def stamped_turns_from_kimi(wire_path: Path) -> list[StampedTurn]:
    """Turns from a Kimi ``wire.jsonl``.

    Kimi records a protocol stream rather than a conversation: a user turn is a
    ``TurnBegin`` envelope and an assistant turn arrives as a run of
    ``ContentPart`` fragments. Consecutive assistant parts are joined, because
    the n-gram check needs a whole reply to match against -- scored fragment by
    fragment, a memory quoted across a sentence boundary would never register.
    """
    turns: list[StampedTurn] = []
    try:
        lines = wire_path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    from memor.ingest.kimi import _user_input_text

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
        msg = rec.get("message") or {}
        payload = msg.get("payload") or {}
        kind = msg.get("type")

        if kind == "TurnBegin":
            text = _user_input_text(payload.get("user_input")).strip()
            role = "user"
        elif kind == "ContentPart" and payload.get("type") == "text":
            text = (payload.get("text") or "").strip()
            role = "assistant"
        else:
            continue
        if not text:
            continue

        stamp = _record_epoch(rec)
        if turns and role == "assistant" and turns[-1][1] == "assistant":
            prev = turns[-1]
            turns[-1] = (prev[0], "assistant", prev[2] + " " + text.lower())
        else:
            turns.append((stamp, role, text.lower()))
    return turns


def stamped_turns_from_codex(rollout_path: Path) -> list[StampedTurn]:
    """Turns from a Codex rollout.

    The prose lives in ``response_item`` records; ``event_msg`` duplicates them
    and would double-count every turn the grader sees. Tool blocks are inlined
    into assistant prose by Codex Desktop and are dropped through the ingest
    parser's own rule, so the grader reads the same text the store keeps.
    """
    from memor.ingest.codex import _TOOL_BLOCK_RE, _records, _text_of

    turns: list[StampedTurn] = []
    for rec in _records(rollout_path):
        if rec.get("type") != "response_item":
            continue
        payload = rec.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in ("assistant", "user"):
            continue
        text = _text_of(payload.get("content"))
        if not text or _TOOL_BLOCK_RE.match(text):
            continue
        turns.append((_record_epoch(rec), role, text.lower()))
    return turns


def turns_for_unit(unit) -> list[StampedTurn]:
    """Normalised turns for one ingest unit, whatever agent produced it.

    The daemon already knows each unit's agent, so the dispatch belongs here
    rather than in a chain of conditionals at the call site. Unknown agents
    return nothing, which leaves their recalls pending -- the same state as
    before, rather than a wrong verdict from a mis-parsed transcript.
    """
    agent = getattr(unit, "agent", "")
    path = getattr(unit, "path", None)

    if agent == "claude" and path is not None:
        return stamped_turns_from_claude(path)
    if agent == "jcode" and path is not None:
        return stamped_turns_from_jcode(path)
    if agent == "codex" and path is not None:
        return stamped_turns_from_codex(path)
    if agent == "kimi" and path is not None:
        return stamped_turns_from_kimi(path)
    if agent == "goose":
        from memor.ingest.goose import GOOSE_DB_PATH

        session_id = _goose_session_id(unit)
        if session_id:
            return stamped_turns_from_goose(GOOSE_DB_PATH, session_id)
    return []


def _goose_session_id(unit) -> str:
    """Recover a Goose session id from its ingest state key.

    Goose units carry no path, so the id lives in the state key the scanner
    builds. Parsed rather than stored so that ``IngestUnit`` keeps one shape
    across every source.
    """
    key = getattr(unit, "state_key", "") or ""
    return key.split(":", 1)[1] if key.startswith("goose:") else ""


def analyze_session_feedback(
    store: SqliteStore, session_id: str, transcript_path: Path | None = None,
    *, embedder=None, conversation_key: str = "",
    turns: list[StampedTurn] | None = None,
) -> int:
    """Settle the verdict on every memory this session was actually served.

    Accepts either a Claude ``transcript_path`` or pre-read ``turns``. The
    second form exists because Goose keeps no transcript file -- its history is
    rows in SQLite -- so a path is not a shape every agent can supply. Callers
    that already have a path keep working unchanged.

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

    stamped_assistant, stamped_user = (
        _split_channels(turns) if turns is not None
        else _extract_stamped_texts(transcript_path) if transcript_path is not None
        else ([], [])
    )
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
