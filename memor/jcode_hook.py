"""Ingest trigger for jcode's turn_end / session_end hooks.

jcode's hooks are observers: detached, fire-and-forget, stdout discarded. That
rules out recall, which has to hand context back to the prompt, but makes them
the right place to *ingest*, because nothing here can delay a turn.

The hook fires per turn, so this must be cheap and must never raise into the
caller. It ingests only the one session it was told about, using the cwd jcode
provides, which is the reliable source of project scope.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _session_path(session_id: str) -> Path | None:
    from memor.ingest.jcode import JCODE_SESSIONS_DIR

    if not session_id:
        return None
    candidate = JCODE_SESSIONS_DIR / f"{session_id}.json"
    return candidate if candidate.exists() else None


def main() -> int:
    """Ingest the session named by the hook. Always exits 0."""
    try:
        session_id = os.environ.get("JCODE_HOOK_SESSION_ID", "")
        cwd = os.environ.get("JCODE_HOOK_CWD", "")
        path = _session_path(session_id)
        if path is None:
            return 0

        from memor.daemon import DEFAULT_DB, _make_embedder
        from memor.ingest.jcode import parse_session, working_dir_for
        from memor.project import resolve_project
        from memor.store.sqlite_store import SqliteStore

        # jcode's own cwd wins: the session JSON frequently has a null
        # working_dir, and a wrong project scope leaks memories across projects.
        work_dir = cwd or working_dir_for(path)
        project = resolve_project(work_dir) if work_dir else "unknown"

        arts = parse_session(path, project, filter_noise=True,
                             session_id=session_id)
        if not arts:
            return 0

        embedder = _make_embedder()
        store = SqliteStore(str(DEFAULT_DB), dim=embedder.dim)
        store.add_artifacts(arts, embedder.embed([a.text for a in arts]))
    except Exception as exc:
        # A hook must never surface a failure into the agent's turn, but a
        # silent swallow hides real bugs -- this one hid a UNIQUE constraint
        # violation that dropped every ingest on the floor. Log and move on;
        # the daemon re-ingests the same session on its next pass.
        try:
            log = Path.home() / ".memor" / "jcode-hook.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as fh:
                fh.write(f"{type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
