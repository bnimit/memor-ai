"""PostToolUse output compression for Claude Code.

Coverage is what caps realized savings: 87.6% of proxied requests on the
development ledger carried nothing compressible, because the proxy sees a
conversation whose bulk is history it must leave byte-exact. This hook works
at the point where that bulk is created instead.

Most of these tests are about what the hook refuses to do. ``updatedToolOutput``
replaces what the model sees, so an over-eager rewrite is not a missed saving,
it is the agent reasoning about output that never existed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from memor.posttool_compress import build_response, main, should_compress


def _bash_request(stdout: str, **response_extra) -> dict:
    response = {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}
    response.update(response_extra)
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest"},
        "tool_response": response,
        "session_id": "s1",
    }


def _noisy_log(lines: int = 400) -> str:
    body = "\n".join(f"INFO  module.sub{i % 7}: handled request {i} in 3ms"
                     for i in range(lines))
    return body + "\nERROR failed to connect to db\n"


# --- the saving ------------------------------------------------------------

def test_long_bash_log_is_compressed():
    request = _bash_request(_noisy_log())
    out = build_response(request)

    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert len(updated["stdout"]) < len(request["tool_response"]["stdout"])
    # The error line is the reason the user ran the command.
    assert "ERROR failed to connect to db" in updated["stdout"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_response_shape_is_preserved():
    """A value that does not match the tool's schema is silently discarded.

    Claude Code validates ``updatedToolOutput`` against the tool's output
    shape and falls back to the original when it does not match, so dropping a
    field turns the whole feature into a no-op that still looks like it works.
    """
    request = _bash_request(_noisy_log())
    updated = build_response(request)["hookSpecificOutput"]["updatedToolOutput"]
    assert set(updated) == {"stdout", "stderr", "interrupted", "isImage"}
    assert updated["interrupted"] is False
    assert updated["isImage"] is False


def test_context_tells_the_model_output_was_elided():
    """Silent truncation invites the model to treat partial output as whole."""
    out = build_response(_bash_request(_noisy_log()))
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "memor" in context.lower()
    assert "rerun" in context.lower()


# --- the refusals ----------------------------------------------------------

def test_read_output_is_never_touched():
    """A file read is the most likely input to the next edit.

    Eliding lines here produces an edit against content that was never in the
    file, and nothing in the transcript reveals it happened.
    """
    request = {
        "tool_name": "Read",
        "tool_response": {"file": {"contents": _noisy_log()}},
    }
    assert should_compress(request) is False
    assert build_response(request) == {}


def test_grep_and_glob_are_not_touched():
    for tool in ("Grep", "Glob", "Edit", "Write", "WebFetch"):
        request = _bash_request(_noisy_log())
        request["tool_name"] = tool
        assert should_compress(request) is False, tool


def test_failed_command_passes_through():
    """A non-zero exit is the moment the user needs every line."""
    for key in ("exit_code", "exitCode", "returncode"):
        request = _bash_request(_noisy_log(), **{key: 1})
        assert should_compress(request) is False, key


def test_successful_command_with_explicit_zero_exit_is_compressed():
    request = _bash_request(_noisy_log(), exit_code=0)
    assert should_compress(request) is True


def test_interrupted_command_passes_through():
    request = _bash_request(_noisy_log(), interrupted=True)
    assert should_compress(request) is False


def test_source_code_is_never_crushed():
    """`cat`ing a module must come back byte-exact.

    The log crusher deletes lines it reads as repetitive, which mangles code
    into something that still looks plausible.
    """
    source = "\n".join(
        [f"def handler_{i}(request):\n    return process(request, {i})\n"
         for i in range(80)]
    )
    request = _bash_request(source)
    assert build_response(request) == {}


def test_short_output_is_left_alone():
    assert should_compress(_bash_request("all tests passed\n")) is False


def test_incompressible_output_produces_no_rewrite():
    """No rewrite at all is better than a rewrite that saves nothing."""
    request = _bash_request("".join(f"{i} unique-token-{i*7919}\n" for i in range(60)))
    out = build_response(request)
    if out:
        updated = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        assert len(updated) < len(request["tool_response"]["stdout"])


def test_string_tool_response_is_skipped():
    """Older/other shapes cannot be returned without violating the schema."""
    request = _bash_request(_noisy_log())
    request["tool_response"] = _noisy_log()
    assert should_compress(request) is False


def test_missing_tool_response_is_safe():
    assert should_compress({"tool_name": "Bash"}) is False
    assert build_response({"tool_name": "Bash"}) == {}


def test_image_output_is_skipped():
    request = _bash_request(_noisy_log(), isImage=True)
    assert should_compress(request) is False


# --- process contract -------------------------------------------------------

def _run_hook(payload: dict, home: Path | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a real process, with HOME pointed somewhere disposable.

    ``main()`` writes savings to the ledger under ``Path.home()``, and a
    subprocess does not inherit a monkeypatched ``Path.home``. Without an
    overridden HOME these tests append fixture rows to the developer's real
    ~/.memor/memor.db -- which happened, and produced 35 identical
    "6007 -> 138" rows that then showed up in the dashboard as live traffic.
    """
    env = dict(os.environ)
    env["HOME"] = str(home or Path(tempfile.mkdtemp()))
    return subprocess.run(
        [sys.executable, "-c",
         "from memor.posttool_compress import main; main()"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
    )


def test_hook_emits_only_json_on_stdout():
    """Claude Code parses stdout as JSON; stray output breaks the contract."""
    proc = _run_hook(_bash_request(_noisy_log()))
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert "hookSpecificOutput" in parsed


def test_hook_stays_silent_when_it_declines():
    proc = _run_hook(_bash_request("short\n"))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_hook_survives_malformed_stdin():
    """Exit 0 and say nothing: a broken hook must not cost a tool result."""
    env = dict(os.environ)
    env["HOME"] = tempfile.mkdtemp()
    proc = subprocess.run(
        [sys.executable, "-c", "from memor.posttool_compress import main; main()"],
        input="not json at all",
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_running_the_hook_never_writes_to_the_real_home(tmp_path):
    """Tests must not append fixture rows to the developer's own ledger.

    ``main()`` records savings under ``Path.home()``, and a subprocess ignores
    a monkeypatched ``Path.home``. This suite wrote 35 identical rows into a
    real ~/.memor/memor.db before that was noticed, and they were then read
    back off the dashboard as though they were live traffic.
    """
    import sqlite3

    from memor.store.sqlite_store import SqliteStore

    home = tmp_path / "home"
    (home / ".memor").mkdir(parents=True)
    SqliteStore(str(home / ".memor" / "memor.db"), dim=16)

    proc = _run_hook(_bash_request(_noisy_log()), home=home)
    assert proc.returncode == 0

    db = sqlite3.connect(str(home / ".memor" / "memor.db"))
    written = db.execute("SELECT COUNT(*) FROM proxy_savings").fetchone()[0]
    db.close()
    # The saving landed in the sandbox, which proves HOME was honoured and
    # therefore that the real ledger was not the thing being written to.
    assert written == 1


# --- install / uninstall ----------------------------------------------------

def test_install_registers_matcher_covering_the_allowlist(tmp_path):
    """The matcher and the allowlist must not drift apart.

    The matcher was pinned to `Bash` while the hook's own allowlist grew.
    Claude Code never spawns the hook for a tool the matcher excludes, so the
    additions were unreachable and nothing said so.
    """
    import re

    from memor.cli import _install_posttool_compress
    from memor.posttool_compress import COMPRESSIBLE_TOOLS

    settings = tmp_path / "settings.json"
    _install_posttool_compress(settings, "/usr/local/bin/memor-posttool-compress")

    data = json.loads(settings.read_text())
    group = data["hooks"]["PostToolUse"][0]
    for tool in COMPRESSIBLE_TOOLS:
        assert re.fullmatch(group["matcher"], tool), tool
    # Claude Code capitalises; jcode does not. One matcher serves both.
    assert re.fullmatch(group["matcher"], "Bash")
    # Still an allowlist: no process spawned for what it always refuses.
    for tool in ("Read", "Edit", "Grep", "Glob", "batch", "browser"):
        assert not re.fullmatch(group["matcher"], tool), tool
    assert "memor-posttool-compress" in group["hooks"][0]["command"]


def test_install_is_idempotent_and_preserves_other_hooks(tmp_path):
    from memor.cli import _install_posttool_compress

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "lint.sh"}]}
            ],
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "memor-hook"}]}
            ],
        }
    }))

    for _ in range(3):
        _install_posttool_compress(settings, "/bin/memor-posttool-compress")

    data = json.loads(settings.read_text())
    post = data["hooks"]["PostToolUse"]
    assert len(post) == 2  # the user's lint hook plus exactly one of ours
    assert any(g["hooks"][0]["command"] == "lint.sh" for g in post)
    # The recall hook must survive a compression install.
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "memor-hook"


def test_uninstall_removes_only_our_hook(tmp_path):
    from memor.cli import _install_posttool_compress, _uninstall_posttool_compress

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PostToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "lint.sh"}]}
        ]}
    }))
    _install_posttool_compress(settings, "/bin/memor-posttool-compress")
    assert _uninstall_posttool_compress(settings) is True

    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PostToolUse"]) == 1
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "lint.sh"


def test_uninstall_leaves_no_empty_scaffolding(tmp_path):
    from memor.cli import _install_posttool_compress, _uninstall_posttool_compress

    settings = tmp_path / "settings.json"
    _install_posttool_compress(settings, "/bin/memor-posttool-compress")
    _uninstall_posttool_compress(settings)
    assert "PostToolUse" not in json.loads(settings.read_text()).get("hooks", {})


def test_uninstall_is_a_noop_when_not_installed(tmp_path):
    from memor.cli import _uninstall_posttool_compress

    assert _uninstall_posttool_compress(tmp_path / "missing.json") is False
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    assert _uninstall_posttool_compress(settings) is False


def test_savings_are_written_to_the_shared_ledger(tmp_path, monkeypatch):
    """Hook savings must reach the same ledger the dashboard reads.

    A user who never installs the proxy still saves tokens through this path,
    and savings that are not recorded cannot be reported, which is how a real
    benefit ends up looking like no benefit at all.
    """
    import sqlite3

    from memor.store.sqlite_store import SqliteStore

    home = tmp_path / "home"
    (home / ".memor").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    SqliteStore(str(home / ".memor" / "memor.db"), dim=16)

    from memor.posttool_compress import build_response as build

    out = build(_bash_request(_noisy_log()), ledger=True)
    assert out  # the rewrite happened

    db = sqlite3.connect(str(home / ".memor" / "memor.db"))
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        "SELECT agent, provider, tokens_before, tokens_after, passthrough, "
        "session_id FROM proxy_savings")]
    db.close()

    assert len(rows) == 1
    assert rows[0]["provider"] == "hook"  # distinguishable from proxy traffic
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["tokens_after"] < rows[0]["tokens_before"]
    assert rows[0]["passthrough"] == 0


def test_ledger_failure_never_blocks_the_rewrite(tmp_path, monkeypatch):
    """Losing a statistic must not cost the user the compression."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nonexistent"))
    out = build_response(_bash_request(_noisy_log()), ledger=True)
    assert out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_real_pytest_output_compresses_substantially():
    """Guards the case the whole feature was built for.

    pytest progress lines were previously classified as prose and passed
    through untouched; this is the shape that regression would reappear in.
    """
    from memor.tokencount import count_tokens

    out = "\n".join(
        [f"tests/test_module_{i}.py {'.' * (i % 12 + 1)}"
         f"{' ' * 20}[{i * 2:3d}%]" for i in range(45)]
    )
    out = ("============ test session starts ============\n" + out
           + "\n============ 1188 passed in 6.02s ============")
    result = build_response(_bash_request(out))
    assert result
    new = result["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert count_tokens(new) < count_tokens(out) * 0.5
    # The line the user actually reads must survive.
    assert "1188 passed" in new


def test_declining_does_not_load_the_tokenizer():
    """This hook runs on every Bash call, including trivial ones.

    tiktoken costs ~50ms to import. Paying that to conclude that `ls` produced
    two lines is a cost the user pays hundreds of times a day for nothing, so
    the early-exit path must not touch it.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json,sys\n"
         "from memor.posttool_compress import build_response\n"
         "build_response(json.loads(sys.argv[1]))\n"
         "print('tiktoken' in sys.modules)",
         json.dumps(_bash_request("short output\n"))],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.stdout.strip() == "False", proc.stderr


def test_failing_test_run_keeps_every_diagnostic():
    """The verdict is not always in the last few lines.

    The log crusher keeps a positional tail, and pytest often prints a
    warnings epilogue after the summary. Failure detail must survive on
    content, not on position.
    """
    body = "\n".join("." * 72 + f" [{i * 4:3d}%]" for i in range(25))
    tail = "\n".join([
        "=================================== FAILURES ===================================",
        "    def test_broken():",
        ">       assert add(2, 2) == 5",
        "E       assert 4 == 5",
        "tests/test_math.py:12: AssertionError",
        "=========================== short test summary info ============================",
        "FAILED tests/test_math.py::test_broken - assert 4 == 5",
        "========================= 1 failed, 1227 passed in 6.1s ========================",
        "=============================== warnings summary ===============================",
        "  StarletteDeprecationWarning: httpx with starlette.testclient is deprecated",
        "    from starlette.testclient import TestClient as TestClient  # noqa",
        "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html",
    ])

    from memor.compress import compress_text

    result = compress_text(body + "\n" + tail)
    assert result.tokens_after < result.tokens_before
    for needle in (
        "1 failed, 1227 passed",
        "FAILED tests/test_math.py::test_broken",
        "assert 4 == 5",
        "tests/test_math.py:12",
    ):
        assert needle in result.text, needle


def test_read_and_grep_are_still_never_compressed():
    """The safety property the hook rests on, restated after widening.

    Read/Grep/Glob feed edits directly. A mutilated read is indistinguishable
    from the real file, and the agent edits against content that was never on
    disk.
    """
    from memor.posttool_compress import build_response

    for tool in ("Read", "read", "Grep", "grep", "Glob", "glob", "Edit"):
        req = {"tool_name": tool,
               "tool_response": {"stdout": "log line\n" * 900, "exit_code": 0}}
        assert build_response(req) == {}, f"{tool} must never be rewritten"


def test_tool_name_matching_is_case_insensitive():
    """Agents disagree on capitalisation for the same tool."""
    from memor.posttool_compress import build_response

    payload = "\n".join(f"2026-01-01 INFO step {i}" for i in range(400))
    for tool in ("Bash", "bash", "BASH"):
        out = build_response({"tool_name": tool,
                              "tool_response": {"stdout": payload, "exit_code": 0}})
        assert out, f"{tool} should compress"


def test_fetched_pages_and_fanout_are_not_rewritten():
    """Three exclusions that each protect something specific.

    `browser`/`webfetch` return a page: the 98% saving once measured for
    `browser` was the JSON crusher eliding the page itself. `batch` is a
    fan-out whose calls were 383 bash but also 171 read on the measured
    corpus, so compressing its result launders exactly the payload the Read
    exclusion exists to protect.
    """
    import json

    from memor.posttool_compress import build_response

    page = json.dumps({"content": {"content": "Pricing is 29 EUR. " * 500}})
    for tool in ("browser", "webfetch", "WebFetch", "batch"):
        out = build_response({"tool_name": tool,
                              "tool_response": {"stdout": page, "exit_code": 0}})
        assert out == {}, f"{tool} must not be rewritten"


def test_floor_admits_payloads_between_1kb_and_2kb():
    """The floor was 2,000 chars, and the corpus says that was too high.

    On 400 sessions, 1,147 payloads between 1KB and 2KB reach a real
    compressor for a 0.57% gain -- 80% of everything available below the old
    floor, with the remaining 20% spread thinly under 500 chars.

    Safety was probed to the same standard as the log crusher: of the newly
    admitted payloads, the log bucket loses no answer-critical line, and the
    112 'losses' flagged in search output were the path-prefix fold (111) plus
    one section header, not match text.
    """
    from memor.posttool_compress import MIN_CHARS, should_compress

    assert MIN_CHARS == 1_000

    body = "\n".join(f"2026-01-01 12:00:0{i%10} INFO worker {i} handled request" for i in range(30))
    assert 1_000 <= len(body) < 2_000, len(body)
    assert should_compress({"tool_name": "Bash",
                            "tool_response": {"stdout": body, "exit_code": 0}})


def test_floor_still_refuses_genuinely_small_output():
    """Below 1KB the measured gain is 0.14% total, so the floor stays."""
    from memor.posttool_compress import should_compress

    assert not should_compress({"tool_name": "Bash",
                                "tool_response": {"stdout": "x" * 400, "exit_code": 0}})
