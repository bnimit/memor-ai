from __future__ import annotations
import hashlib, json, re
from datetime import datetime
from pathlib import Path
from memor.types import Artifact
from memor.tokencount import count_tokens
from memor.redact import redact_text

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

_FILLER_STARTS = re.compile(
    r"^(Let me |Now let me |Now I|I'll |I will |Good[,.]|Great[,.]|Perfect[!,.]|Done[!,.]"
    r"|Alright|Sure[,.]|OK[,.]|Moving to |Looking at |Checking )", re.I)
_SKILL_BOILERPLATE = "Base directory for this skill"

_DECISION_RE = re.compile(
    r"(we decided|the approach is|instead of|switched to|chose .+ over|"
    r"trade-?off|architecture:|design decision)", re.I)
_BUGFIX_RE = re.compile(
    r"(the fix is|root cause|the bug was|the issue was|caused by|"
    r"the problem is|this fails because|the error occurs)", re.I)
_LESSON_RE = re.compile(
    r"(always use|never use|never do|important:|note:|pattern:|"
    r"best practice|lesson learned|rule of thumb|should always|should never)", re.I)

_BASE64_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")
_PERMISSION_RE = re.compile(r"^(Allow |Permission |Do you want to allow |Grant )", re.I)
_PATH_LINE_RE = re.compile(r"^[/~][\w./\-]+$")
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

MIN_SIGNAL_TOKENS = 8
MIN_FILLER_TOKENS = 30
MIN_USER_TOKENS = 6
MIN_CODE_TOKENS = 40
MIN_LONG_TOKENS = 100


def _strip_system_reminders(text: str) -> str:
    """Strip <system-reminder>...</system-reminder> tags and their content."""
    return _SYSTEM_REMINDER_RE.sub("", text)


def _is_file_listing(text: str) -> bool:
    """Return True if text is a pure file listing (multiple lines of just paths)."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    return all(_PATH_LINE_RE.match(l.strip()) for l in lines)


def _signal_score(text: str, role: str, token_count: int) -> float:
    """Return a score > 0 for signal content, 0 for noise."""
    # Hard noise filters
    if token_count < MIN_SIGNAL_TOKENS:
        return 0.0
    if _SKILL_BOILERPLATE in text:
        return 0.0
    if _BASE64_RE.search(text):
        return 0.0
    if _PERMISSION_RE.match(text):
        return 0.0
    if _is_file_listing(text):
        return 0.0
    if _FILLER_STARTS.match(text) and token_count < MIN_FILLER_TOKENS:
        return 0.0

    score = 0.0

    if role == "user" and token_count >= MIN_USER_TOKENS:
        score += 1.0
    elif role == "assistant":
        if _DECISION_RE.search(text):
            score += 2.0
        if _BUGFIX_RE.search(text):
            score += 2.0
        if _LESSON_RE.search(text):
            score += 2.0
        if "```" in text and token_count >= MIN_CODE_TOKENS:
            score += 1.0
        if score == 0.0 and token_count >= MIN_LONG_TOKENS:
            score += 0.5

    return score


def parse_transcript(path: Path, project: str, *, filter_noise: bool = True) -> list[Artifact]:
    session_id = path.stem
    arts: list[Artifact] = []
    seen_hashes: set[str] = set()
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
        text = _strip_system_reminders(text).strip()
        if not text:
            continue
        text, _ = redact_text(text)
        if not text.strip():
            continue
        # Deduplicate by MD5 hash within session
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in seen_hashes:
            continue
        seen_hashes.add(text_hash)
        token_count = max(1, count_tokens(text))
        role = msg.get("role", "")
        if filter_noise and _signal_score(text, role, token_count) == 0:
            continue
        arts.append(Artifact(
            id=f"{session_id}:{i}", kind="session_chunk", project=project,
            source="claude_code", text=text, token_count=token_count,
            created_at=_epoch(rec["timestamp"]),
            meta={"session_id": session_id, "role": role, "ord": i}))
    return arts

def discover_project(transcript_path: Path) -> str:
    return transcript_path.parent.name
