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
    return transcript_path.parent.name
