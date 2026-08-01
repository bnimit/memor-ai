"""Multi-agent ingest source registry — Claude, Kimi, Goose."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from memor.ingest.claude_code import parse_transcript
from memor.ingest.goose import (
    GOOSE_DB_PATH,
    goose_state_key,
    parse_session as parse_goose_session,
    scan_goose_sessions,
)
from memor.ingest.kimi import (
    KIMI_JSON_PATH,
    KIMI_SESSIONS_DIR,
    parse_wire,
    scan_kimi_sessions,
)
from memor.types import Artifact

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class IngestUnit:
    """One ingestible session unit for the daemon poll cycle."""
    state_key: str
    mtime: float
    project: str
    agent: str
    parse: Callable[[], list[Artifact]]
    path: Path | None = None  # Claude transcript path (usage/feedback extras)


def scan_claude_units(projects_dir: Path) -> list[IngestUnit]:
    from memor.project import resolve_project_from_claude_dir

    units: list[IngestUnit] = []
    if not projects_dir.is_dir():
        return units
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project = resolve_project_from_claude_dir(project_dir.name)
        for path in sorted(project_dir.rglob("*.jsonl")):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            def _parse(p=path, proj=project) -> list[Artifact]:
                return parse_transcript(p, project=proj, filter_noise=True)

            units.append(IngestUnit(
                state_key=str(path),
                mtime=mtime,
                project=project,
                agent="claude",
                parse=_parse,
                path=path,
            ))
    return units


def scan_kimi_units(
    sessions_dir: Path,
    *,
    kimi_json_path: Path = KIMI_JSON_PATH,
) -> list[IngestUnit]:
    units: list[IngestUnit] = []
    for wire, project, session_id in scan_kimi_sessions(
        sessions_dir, kimi_json_path=kimi_json_path
    ):
        try:
            mtime = wire.stat().st_mtime
        except OSError:
            continue

        def _parse(p=wire, proj=project, sid=session_id) -> list[Artifact]:
            return parse_wire(p, project=proj, filter_noise=True, session_id=sid)

        units.append(IngestUnit(
            state_key=str(wire),
            mtime=mtime,
            project=project,
            agent="kimi",
            parse=_parse,
            path=wire,
        ))
    return units


def scan_goose_units(db_path: Path) -> list[IngestUnit]:
    units: list[IngestUnit] = []
    for session_id, project, mtime in scan_goose_sessions(db_path):
        def _parse(sid=session_id, proj=project, db=db_path) -> list[Artifact]:
            return parse_goose_session(db, sid, proj, filter_noise=True)

        units.append(IngestUnit(
            state_key=goose_state_key(session_id),
            mtime=mtime,
            project=project,
            agent="goose",
            parse=_parse,
            path=None,
        ))
    return units


def scan_all_sources(
    *,
    claude_projects_dir: Path | None = None,
    kimi_sessions_dir: Path | None = None,
    kimi_json_path: Path | None = None,
    goose_db_path: Path | None = None,
) -> list[IngestUnit]:
    """Scan enabled sources. Pass None to skip a source (except Claude when dir given).

    Claude is scanned when ``claude_projects_dir`` is not None.
    Kimi/Goose are scanned only when their paths are not None.
    """
    units: list[IngestUnit] = []
    if claude_projects_dir is not None:
        units.extend(scan_claude_units(claude_projects_dir))
    if kimi_sessions_dir is not None:
        units.extend(scan_kimi_units(
            kimi_sessions_dir,
            kimi_json_path=kimi_json_path or KIMI_JSON_PATH,
        ))
    if goose_db_path is not None:
        units.extend(scan_goose_units(goose_db_path))
    return units


def default_local_source_paths() -> dict[str, Path]:
    """Home-dir paths used by the daemon and ``memor backfill``."""
    return {
        "claude_projects_dir": CLAUDE_PROJECTS_DIR,
        "kimi_sessions_dir": KIMI_SESSIONS_DIR,
        "kimi_json_path": KIMI_JSON_PATH,
        "goose_db_path": GOOSE_DB_PATH,
    }
