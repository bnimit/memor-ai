from __future__ import annotations
from pathlib import Path


def resolve_project(cwd: str) -> str:
    """Resolve project name from a working directory by finding the git root."""
    p = Path(cwd).resolve()
    git_root = _find_git_root(p)
    if git_root:
        return git_root.name
    return p.name


def _find_git_root(path: Path) -> Path | None:
    current = path
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def decode_claude_dir(dirname: str) -> str:
    """Decode a Claude projects directory name back to a filesystem path.
    '-Users-nimit-Documents-Projects-plirin' -> '/Users/nimit/Documents/Projects/plirin'

    Dashes in the encoded name are ambiguous — they may represent either a path
    separator or a literal dash in a directory name. This function walks the
    real filesystem to resolve ambiguity greedily (longest matching segment wins).
    Falls back to naive split-on-dash if the filesystem walk yields no result.
    """
    stripped = dirname.lstrip("-")
    tokens = stripped.split("-")

    result = _fs_decode(tokens)
    if result is not None:
        return result

    # Naive fallback: treat every dash as a separator
    return "/" + "/".join(tokens)


def _fs_decode(tokens: list[str]) -> str | None:
    """Greedily match tokens against the real filesystem, joining dashes as needed."""
    path = Path("/")
    remaining = list(tokens)

    while remaining:
        # Try to match a segment by joining increasing numbers of tokens with dashes
        matched = False
        for end in range(len(remaining), 0, -1):
            candidate = "-".join(remaining[:end])
            candidate_path = path / candidate
            if candidate_path.exists():
                path = candidate_path
                remaining = remaining[end:]
                matched = True
                break
        if not matched:
            # No filesystem match found; give up and return None to trigger fallback
            return None

    return str(path)


def resolve_project_from_claude_dir(dirname: str) -> str:
    """Resolve project name from a Claude projects directory name."""
    decoded = decode_claude_dir(dirname)
    return resolve_project(decoded)
