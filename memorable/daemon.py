"""Auto-ingest daemon: polls ~/.claude/projects/ for new/modified .jsonl transcripts."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from memorable.ingest.claude_code import parse_transcript
from memorable.store.sqlite_store import SqliteStore

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_DIR = Path.home() / ".memorable"
DEFAULT_DB = STATE_DIR / "memorable.db"
STATE_FILE = STATE_DIR / "ingested.json"
POLL_INTERVAL = 30  # seconds


def _project_name_from_dir(dirname: str) -> str:
    """Derive a clean project name from a Claude projects directory name.

    Claude stores projects as e.g. '-Users-nimit-Documents-Projects-plirin'.
    We extract the last path component as the project name.
    """
    # The dirname is a path encoded with dashes replacing slashes
    # e.g. '-Users-nimit-Documents-Projects-plirin' -> 'plirin'
    parts = dirname.strip("-").split("-")
    # Return the last non-empty part
    return parts[-1] if parts else dirname


def load_state() -> dict[str, float]:
    """Load the ingested state file: {filepath -> mtime}."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict[str, float]) -> None:
    """Persist the ingested state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def scan_transcripts(projects_dir: Path) -> list[tuple[Path, str]]:
    """Scan for all .jsonl transcript files, returning (path, project_name) pairs."""
    results = []
    if not projects_dir.is_dir():
        return results
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_name = _project_name_from_dir(project_dir.name)
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            results.append((jsonl_file, project_name))
    return results


def ingest_file(path: Path, project: str, store: SqliteStore, embedder) -> int:
    """Ingest a single transcript file. Returns number of chunks ingested."""
    arts = parse_transcript(path, project=project, filter_noise=True)
    if not arts:
        return 0
    vecs = embedder.embed([a.text for a in arts])
    store.add_artifacts(arts, vecs)
    return len(arts)


def run_poll_cycle(
    state: dict[str, float],
    store: SqliteStore,
    embedder,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
) -> dict[str, float]:
    """Run one poll cycle. Returns updated state dict."""
    transcripts = scan_transcripts(projects_dir)
    for path, project in transcripts:
        path_str = str(path)
        current_mtime = path.stat().st_mtime
        previous_mtime = state.get(path_str)

        if previous_mtime is not None and current_mtime <= previous_mtime:
            continue  # already ingested, not modified

        try:
            count = ingest_file(path, project, store, embedder)
            state[path_str] = current_mtime
            if count > 0:
                print(f"  ingested {count} chunks from {path.name} (project: {project})")
            else:
                print(f"  skipped {path.name} (0 chunks after filtering)")
        except Exception as e:
            print(f"  ERROR ingesting {path.name}: {e}")
            # Don't update state so we retry next cycle
    return state


def run_daemon(poll_interval: int = POLL_INTERVAL, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> None:
    """Run the daemon loop (foreground, blocking)."""
    from memorable.embed.local import LocalEmbedder

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    embedder = LocalEmbedder()
    store = SqliteStore(str(DEFAULT_DB), dim=embedder.dim)
    state = load_state()

    print(f"memorable daemon started")
    print(f"  watching: {projects_dir}")
    print(f"  db: {DEFAULT_DB}")
    print(f"  poll interval: {poll_interval}s")
    print(f"  tracking {len(state)} previously ingested files")
    print()

    try:
        while True:
            print(f"[{time.strftime('%H:%M:%S')}] polling...")
            state = run_poll_cycle(state, store, embedder, projects_dir)
            save_state(state)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\ndaemon stopped.")
        save_state(state)
