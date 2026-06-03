from __future__ import annotations
import json, re
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

_FILLER_STARTS = re.compile(
    r"^(Let me |Now let me |Now I|I'll |I will |Good[,.]|Great[,.]|Perfect[!,.]|Done[!,.]"
    r"|Alright|Sure[,.]|OK[,.]|Moving to |Looking at |Checking )", re.I)
_SKILL_BOILERPLATE = "Base directory for this skill"

MIN_TOKEN_COUNT = 30

def _is_noise(text: str, token_count: int) -> bool:
    if _SKILL_BOILERPLATE in text:
        return True
    if token_count < MIN_TOKEN_COUNT and _FILLER_STARTS.match(text):
        return True
    if token_count < 8:
        return True
    return False

def parse_transcript(path: Path, project: str, *, filter_noise: bool = True) -> list[Artifact]:
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
        token_count = max(1, len(text) // 4)
        if filter_noise and _is_noise(text, token_count):
            continue
        arts.append(Artifact(
            id=f"{session_id}:{i}", kind="session_chunk", project=project,
            source="claude_code", text=text, token_count=token_count,
            created_at=_epoch(rec["timestamp"]),
            meta={"session_id": session_id, "role": msg.get("role"), "ord": i}))
    return arts

def discover_project(transcript_path: Path) -> str:
    return transcript_path.parent.name
