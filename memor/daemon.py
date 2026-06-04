"""Auto-ingest daemon: polls ~/.claude/projects/ for new/modified .jsonl transcripts,
then auto-distills new sessions into compact memories."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from memor.ingest.claude_code import parse_transcript
from memor.store.sqlite_store import SqliteStore
from memor.types import Scope

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_DIR = Path.home() / ".memor"
DEFAULT_DB = STATE_DIR / "memor.db"
STATE_FILE = STATE_DIR / "ingested.json"
DISTILLED_FILE = STATE_DIR / "distilled.json"
POLL_INTERVAL = 30  # seconds
MAX_DISTILL_TOKENS = 4000  # cap text sent to LLM per session


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


def load_distilled_state() -> set[str]:
    """Load the set of already-distilled session IDs."""
    if DISTILLED_FILE.exists():
        try:
            return set(json.loads(DISTILLED_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_distilled_state(distilled: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DISTILLED_FILE.write_text(json.dumps(sorted(distilled), indent=2))


def _make_llm():
    """Try to create an LLM for distillation. Returns None if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from memor.llm.anthropic import AnthropicLLM
        return AnthropicLLM(model="claude-sonnet-4-6", api_key=api_key)
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1")
    if api_key:
        from memor.llm.openai_compat import OpenAICompatLLM
        return OpenAICompatLLM(base_url=base_url, api_key=api_key, model="gpt-4o-mini")
    return None


def ingest_file(path: Path, project: str, store: SqliteStore, embedder) -> int:
    """Ingest a single transcript file. Returns number of chunks ingested."""
    arts = parse_transcript(path, project=project, filter_noise=True)
    if not arts:
        return 0
    vecs = embedder.embed([a.text for a in arts])
    store.add_artifacts(arts, vecs)
    return len(arts)


def distill_new_sessions(
    store: SqliteStore, embedder, llm, distilled: set[str]
) -> set[str]:
    """Distill any sessions that haven't been distilled yet. Returns updated set.
    Uses full LLM distiller if llm is provided, otherwise falls back to extractive-only."""
    if llm:
        from memor.distill.distiller import Distiller
        d = Distiller(store, embedder, llm)
    else:
        from memor.distill.distiller import ExtractiveDistiller
        d = ExtractiveDistiller(store, embedder)
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='session_chunk'"
    ).fetchall()
    by_session: dict[str, list] = {}
    for r in rows:
        a = store._row_to_artifact(r)
        sid = a.meta.get("session_id", "?")
        by_session.setdefault(sid, []).append(a)

    for sid, chunks in by_session.items():
        if sid in distilled:
            continue
        chunks.sort(key=lambda a: a.meta.get("ord", 0))
        total_tok = sum(c.token_count for c in chunks)
        # Cap context sent to LLM
        if total_tok > MAX_DISTILL_TOKENS:
            selected = chunks[:10] + chunks[-5:]
        else:
            selected = chunks
        project = chunks[0].project
        try:
            mem_ids = d.distill_session(sid, selected, project=project)
            distilled.add(sid)
            if mem_ids:
                print(f"  distilled session {sid[:20]}... -> {len(mem_ids)} memories")
        except Exception as e:
            print(f"  ERROR distilling {sid[:20]}...: {e}")
    return distilled


def run_poll_cycle(
    state: dict[str, float],
    store: SqliteStore,
    embedder,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    llm=None,
    distilled: set[str] | None = None,
) -> tuple[dict[str, float], set[str]]:
    """Run one poll cycle: ingest new files, then distill new sessions.
    Returns (updated ingest state, updated distilled set)."""
    if distilled is None:
        distilled = set()

    new_ingested = False
    transcripts = scan_transcripts(projects_dir)

    # Pre-filter to only files that are new or modified
    pending = [
        (path, project)
        for path, project in transcripts
        if not (state.get(str(path)) is not None and path.stat().st_mtime <= state[str(path)])
    ]

    bulk = len(pending) > 10
    total_pending = len(pending)

    for idx, (path, project) in enumerate(pending):
        path_str = str(path)
        current_mtime = path.stat().st_mtime
        progress_prefix = f"[{idx + 1}/{total_pending}] " if bulk else ""
        try:
            count = ingest_file(path, project, store, embedder)
            state[path_str] = current_mtime
            if count > 0:
                print(f"  {progress_prefix}ingested {count} chunks from {path.name} (project: {project})")
                new_ingested = True
            else:
                print(f"  {progress_prefix}skipped {path.name} (0 chunks after filtering)")
        except Exception as e:
            print(f"  {progress_prefix}ERROR ingesting {path.name}: {e}")

    # Auto-distill new sessions (LLM if available, extractive fallback otherwise)
    if new_ingested:
        mode = "abstractive" if llm else "extractive (LLM-free)"
        print(f"  running {mode} distillation on new sessions...")
        distilled = distill_new_sessions(store, embedder, llm, distilled)

    return state, distilled


def _make_embedder():
    """Pick the best available embedder: API if key exists, local ONNX as fallback."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        from memor.embed.api import APIEmbedder
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return APIEmbedder(base_url=base_url, api_key=api_key)
    try:
        from memor.embed.local import LocalEmbedder
        return LocalEmbedder()
    except ImportError:
        raise SystemExit(
            "No embedder available. Either:\n"
            "  1. Set OPENAI_API_KEY for API embeddings (recommended)\n"
            "  2. pip install memor-ai[local] for offline ONNX embeddings"
        )


def run_daemon(poll_interval: int = POLL_INTERVAL, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> None:
    """Run the daemon loop (foreground, blocking)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    embedder = _make_embedder()
    store = SqliteStore(str(DEFAULT_DB), dim=embedder.dim)
    state = load_state()
    distilled = load_distilled_state()

    llm = _make_llm()

    print(f"memor daemon started")
    print(f"  watching: {projects_dir}")
    print(f"  db: {DEFAULT_DB}")
    print(f"  poll interval: {poll_interval}s")
    print(f"  tracking {len(state)} previously ingested files")
    print(f"  {len(distilled)} sessions already distilled")
    if llm:
        print(f"  distillation: abstractive (LLM key found — extractive pre-filter + LLM)")
    else:
        print(f"  distillation: extractive only (set ANTHROPIC_API_KEY for full abstractive)")
    print()

    try:
        while True:
            print(f"[{time.strftime('%H:%M:%S')}] polling...")
            state, distilled = run_poll_cycle(
                state, store, embedder, projects_dir, llm=llm, distilled=distilled
            )
            save_state(state)
            save_distilled_state(distilled)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\ndaemon stopped.")
        save_state(state)
        save_distilled_state(distilled)
