"""Tests for Cursor composer ingest from Cursor's VS Code state stores."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memor.ingest.cursor import (
    _decode_tool_result,
    cursor_state_key,
    parse_session,
    scan_cursor_sessions,
    workspace_project_map,
)
from memor.ingest.sources import scan_all_sources

_USER = 1
_ASSISTANT = 2


def _bubble(
    *,
    bubble_id: str,
    kind: int,
    text: str = "",
    created_at: str = "2026-07-12T10:10:18.000Z",
    tool: dict | None = None,
    code_blocks: list | None = None,
) -> str:
    payload: dict = {
        "bubbleId": bubble_id,
        "type": kind,
        "text": text,
        "createdAt": created_at,
    }
    if tool is not None:
        payload["toolFormerData"] = tool
    if code_blocks is not None:
        payload["codeBlocks"] = code_blocks
    return json.dumps(payload)


def _make_global_db(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO cursorDiskKV(key, value) VALUES (?,?)", rows)
    con.commit()
    con.close()


def _make_workspace(root: Path, name: str, folder: str, composer_ids: list[str]) -> None:
    entry = root / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "workspace.json").write_text(json.dumps({"folder": folder}))
    con = sqlite3.connect(entry / "state.vscdb")
    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "INSERT INTO ItemTable(key, value) VALUES (?,?)",
        (
            "composer.composerData",
            json.dumps({"allComposers": [{"composerId": c} for c in composer_ids]}),
        ),
    )
    con.commit()
    con.close()


def test_parse_session_extracts_roles_and_order(tmp_path: Path) -> None:
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        (f"bubbleId:comp-1:b2", _bubble(
            bubble_id="b2", kind=_ASSISTANT,
            text=(
                "The bug was a stale migration left over from the previous "
                "branch, so the schema never matched the fixtures. I fixed it "
                "by rerunning the migration before the suite starts."
            ),
            created_at="2026-07-12T10:11:00.000Z")),
        (f"bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_USER,
            text=(
                "Why does the integration test suite fail on a clean checkout "
                "when it passes locally on my machine?"
            ),
            created_at="2026-07-12T10:10:00.000Z")),
    ])

    arts = parse_session(db, "comp-1", "demo")

    assert [a.meta["role"] for a in arts] == ["user", "assistant"]
    assert arts[0].source == "cursor"
    assert arts[0].project == "demo"
    assert arts[0].meta["session_id"] == "comp-1"
    assert arts[0].id == "comp-1:b1"
    # Ordered by createdAt, not by the uuid-keyed row order.
    assert arts[0].created_at < arts[1].created_at


def test_tool_call_bubble_is_ingested_with_its_result(tmp_path: Path) -> None:
    """A tool turn carries the outcome, so it must survive the empty-text filter."""
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={
                "name": "run_terminal_cmd",
                "status": "completed",
                "rawArgs": '{"command": "pytest tests/integration"}',
                "result": json.dumps({
                    "output": (
                        "3 failed: connection refused on port 5432, the "
                        "database container was never started by the fixture"
                    ),
                }),
            })),
    ])

    arts = parse_session(db, "comp-1", "demo")

    assert len(arts) == 1
    assert "run_terminal_cmd" in arts[0].text
    assert "pytest tests/integration" in arts[0].text
    assert "connection refused on port 5432" in arts[0].text


def test_failed_tool_call_records_its_status(tmp_path: Path) -> None:
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={
                "name": "read_file",
                "status": "error",
                "rawArgs": '{"target_file": "services/billing/missing_module.py"}',
                "result": json.dumps({
                    "contents": (
                        "file not found: services/billing/missing_module.py "
                        "was deleted in an earlier refactor of this package"
                    ),
                }),
            })),
    ])

    arts = parse_session(db, "comp-1", "demo")

    assert len(arts) == 1
    assert "[read_file: error]" in arts[0].text


def test_decode_tool_result_unwraps_nested_json_string() -> None:
    assert _decode_tool_result(json.dumps({"contents": "hello"})) == "hello"
    assert _decode_tool_result("plain text") == "plain text"
    assert _decode_tool_result(None) == ""


def test_terminal_failure_survives_the_prose_noise_filter(tmp_path: Path) -> None:
    """The shared scorer drops this text; a tool outcome must be kept anyway.

    "connection refused" reads like none of the decision/bugfix/lesson cues the
    prose filter looks for, yet it is the whole reason to remember the turn.
    """
    from memor.ingest.claude_code import _signal_score
    from memor.tokencount import count_tokens

    result = "connection refused on port 5432 after the retry budget ran out"
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={
                "name": "run_terminal_cmd",
                "status": "completed",
                "rawArgs": '{"command": "pytest"}',
                "result": json.dumps({"output": result}),
            })),
    ])

    arts = parse_session(db, "comp-1", "demo")

    assert len(arts) == 1
    assert result in arts[0].text
    # The prose scorer alone would have discarded it.
    assert _signal_score(arts[0].text, "assistant", count_tokens(arts[0].text)) == 0


def test_bare_tool_call_without_an_outcome_is_dropped(tmp_path: Path) -> None:
    """Keeping tool turns must not mean keeping contentless ones."""
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={"name": "ls", "status": "completed", "rawArgs": "{}"})),
    ])

    assert parse_session(db, "comp-1", "demo") == []


def test_workspace_map_resolves_project_for_its_composers(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "my-service"
    (repo / ".git").mkdir(parents=True)
    ws = tmp_path / "workspaceStorage"
    _make_workspace(ws, "hash1", repo.as_uri(), ["comp-1"])

    mapping = workspace_project_map(ws)

    assert mapping == {"comp-1": "my-service"}


def test_scan_prefers_workspace_project_over_path_inference(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "my-service"
    (repo / ".git").mkdir(parents=True)
    ws = tmp_path / "workspaceStorage"
    _make_workspace(ws, "hash1", repo.as_uri(), ["comp-1"])

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_USER, text="hello")),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=ws)

    assert sessions == [("comp-1", "my-service", sessions[0][2])]


def test_scan_infers_project_from_paths_when_workspace_is_gone(tmp_path: Path) -> None:
    """Most threads outlive their workspace entry, so inference carries the corpus."""
    repo = tmp_path / "repos" / "other-service"
    (repo / ".git").mkdir(parents=True)
    target = repo / "app" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-9:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={
                "name": "read_file",
                "status": "completed",
                "rawArgs": "{}",
                "result": json.dumps({"contents": "x = 1"}),
            },
            code_blocks=[{
                "uri": {"scheme": "file", "path": str(target)},
                "content": "x = 1",
            }])),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][0] == "comp-9"
    assert sessions[0][1] == "other-service"


def test_scan_infers_project_from_a_directory_path(tmp_path: Path) -> None:
    """Cursor logs directories too; those must not resolve to the parent folder."""
    repo = tmp_path / "repos" / "dir-service"
    (repo / ".git").mkdir(parents=True)

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-3:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            code_blocks=[{
                "uri": {"scheme": "file", "path": str(repo)},
                "content": "print(1)",
            }])),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "dir-service"


def test_scan_infers_project_from_escaped_tool_args(tmp_path: Path) -> None:
    """Tool args are a JSON string inside the record, so their quotes are escaped.

    Most threads only ever mention a path this way, so missing the escaped form
    left the bulk of the corpus filed under "unknown".
    """
    repo = tmp_path / "repos" / "escaped-service"
    (repo / ".git").mkdir(parents=True)
    target = repo / "cli.py"
    target.write_text("x = 1\n")

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-7:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            tool={
                "name": "read_file_v2",
                "status": "completed",
                # json.dumps of a dict whose value is itself a JSON string.
                "rawArgs": json.dumps({"path": str(target)}),
                "result": json.dumps({"contents": "x = 1"}),
            })),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "escaped-service"


def test_project_vote_beats_an_incidental_first_path(tmp_path: Path) -> None:
    """The first path in a thread is often not the project being worked on."""
    stray = tmp_path / "repos" / "stray-repo"
    (stray / ".git").mkdir(parents=True)
    real = tmp_path / "repos" / "real-repo"
    (real / ".git").mkdir(parents=True)

    rows = [
        ("bubbleId:comp-8:b0", _bubble(
            bubble_id="b0", kind=_ASSISTANT, text="",
            code_blocks=[{"uri": {"path": str(stray / "once.py")}, "content": "x"}])),
    ]
    for i in range(1, 4):
        rows.append((f"bubbleId:comp-8:b{i}", _bubble(
            bubble_id=f"b{i}", kind=_ASSISTANT, text="",
            code_blocks=[{"uri": {"path": str(real / f"m{i}.py")}, "content": "x"}])))

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, rows)

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "real-repo"


def test_project_survives_a_file_that_no_longer_exists(tmp_path: Path) -> None:
    """Threads outlive the files they touched; the repo is usually still there."""
    repo = tmp_path / "repos" / "living-repo"
    (repo / ".git").mkdir(parents=True)
    gone = repo / "deleted" / "branch" / "scratch.md"

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-5:b1", _bubble(
            bubble_id="b1", kind=_USER,
            text=f"Review the brief at {gone} and summarize the plan")),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "living-repo"


def test_tooling_paths_do_not_win_the_project_vote(tmp_path: Path) -> None:
    """An agent reads its own plugin cache constantly; that is not the project."""
    repo = tmp_path / "repos" / "app-repo"
    (repo / ".git").mkdir(parents=True)
    plugins = tmp_path / "home" / ".claude" / "plugins" / "cache"
    plugins.mkdir(parents=True)

    rows = [
        (f"bubbleId:comp-6:b{i}", _bubble(
            bubble_id=f"b{i}", kind=_ASSISTANT, text="",
            tool={
                "name": "read_file",
                "status": "completed",
                "rawArgs": json.dumps({"path": str(plugins / "skill.md")}),
                "result": json.dumps({"contents": "doc"}),
            }))
        for i in range(4)
    ]
    rows.append(("bubbleId:comp-6:b9", _bubble(
        bubble_id="b9", kind=_ASSISTANT, text="",
        code_blocks=[{"uri": {"path": str(repo / "main.py")}, "content": "x"}])))

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, rows)

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "app-repo"


def test_decode_dashed_path_handles_dashes_in_the_project_name() -> None:
    """The encoding is lossy, so a real directory must settle the split."""
    from memor.ingest.cursor import _decode_dashed_path

    home = Path.home()
    assert _decode_dashed_path(
        str(home).lstrip("/").replace("/", "-")
    ) == home
    # Nothing resolves below an existing head, so the reference is abandoned
    # rather than attributed to the home directory.
    assert _decode_dashed_path(
        str(home).lstrip("/").replace("/", "-") + "-no-such-project-here"
    ) is None
    assert _decode_dashed_path("empty-window") is None


def test_encoded_scratch_dir_attributes_a_research_thread(tmp_path: Path) -> None:
    """A thread that touched no repo file still names its workspace somewhere.

    Pure research sessions are real work, and their only concrete paths point
    at plugin caches, so this is the last signal available.
    """
    repo = tmp_path / "repos" / "research-repo"
    (repo / ".git").mkdir(parents=True)
    encoded = str(repo).lstrip("/").replace("/", "-")

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-2:b1", _bubble(
            bubble_id="b1", kind=_USER,
            text=(
                "Research real-time equities data sources; notes are under "
                f"/Users/someone/.cursor/projects/{encoded}/notes"
            ))),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "research-repo"


def test_scan_falls_back_to_unknown_without_any_path(tmp_path: Path) -> None:
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-4:b1", _bubble(
            bubble_id="b1", kind=_USER, text="a question with no files attached")),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "unknown"


def test_scan_uses_newest_bubble_as_session_mtime(tmp_path: Path) -> None:
    """mtime drives the daemon's pending check, so it must track the last message."""
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_USER, text="first",
            created_at="2026-07-12T10:00:00.000Z")),
        ("bubbleId:comp-1:b2", _bubble(
            bubble_id="b2", kind=_ASSISTANT, text="second",
            created_at="2026-07-12T12:00:00.000Z")),
    ])

    (composer, _project, mtime), = scan_cursor_sessions(
        db, workspace_dir=tmp_path / "missing"
    )

    assert composer == "comp-1"
    assert mtime == 1783857600.0  # 2026-07-12T12:00:00Z


def test_missing_database_scans_clean(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "state.vscdb"

    assert scan_cursor_sessions(missing, workspace_dir=tmp_path / "gone") == []
    assert parse_session(missing, "comp-1", "demo") == []


def test_cursor_state_key_is_namespaced() -> None:
    assert cursor_state_key("comp-1") == "cursor:comp-1"


def test_scan_all_sources_includes_cursor_units(tmp_path: Path) -> None:
    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_USER,
            text="Why does the integration suite fail on a clean checkout?")),
    ])

    units = scan_all_sources(
        claude_projects_dir=tmp_path / "no-claude",
        cursor_db_path=db,
    )

    cursor_units = [u for u in units if u.agent == "cursor"]
    assert len(cursor_units) == 1
    assert cursor_units[0].state_key == "cursor:comp-1"


def test_scan_all_sources_skips_cursor_when_unset(tmp_path: Path) -> None:
    units = scan_all_sources(claude_projects_dir=tmp_path / "no-claude")

    assert [u for u in units if u.agent == "cursor"] == []


def test_paths_outside_home_directories_still_attribute(tmp_path) -> None:
    """Project inference must not depend on where the user keeps code.

    The regex once matched only /Users and /home. That passed on macOS, whose
    pytest temp dirs sit under /Users, and failed on Linux CI, where they sit
    under /tmp. It also silently ignored /opt, /srv, /workspace and every
    container layout. Anchoring the fixture at the filesystem root keeps the
    assumption from creeping back in.
    """
    repo = tmp_path / "srv" / "deploy" / "checkout"
    (repo / ".git").mkdir(parents=True)
    target = repo / "main.py"
    target.write_text("x = 1\n")

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_ASSISTANT, text="",
            code_blocks=[{"uri": {"path": str(target)}, "content": "x = 1"}])),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "checkout"


def test_an_unstattable_candidate_does_not_kill_the_scan(tmp_path) -> None:
    """Cursor embeds base64 protobuf blobs that begin with a slash.

    Those are path-shaped enough to match and long enough to blow past
    NAME_MAX, so stat() raises rather than returning False. One such row used
    to abort the whole ingest with OSError 63.
    """
    blob = "/" + "A" * 4000

    db = tmp_path / "globalStorage" / "state.vscdb"
    _make_global_db(db, [
        ("bubbleId:comp-1:b1", _bubble(
            bubble_id="b1", kind=_USER,
            text=f"see {blob} for the encoded payload")),
    ])

    sessions = scan_cursor_sessions(db, workspace_dir=tmp_path / "missing")

    assert sessions[0][1] == "unknown"
