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
