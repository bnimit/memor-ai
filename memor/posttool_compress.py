"""PostToolUse compression for Claude Code: crush tool output before it lands.

Coverage, not compressor quality, is what caps realized savings. On the
development machine's ledger 87.6% of proxied requests carried nothing
compressible, because by the time the proxy sees a request the bulk of it is
conversation history the compressor deliberately refuses to rewrite. The
compressible mass enters the transcript exactly once, as tool output, and is
resent verbatim on every subsequent turn.

This hook intercepts it there. A 300-line test log crushed at ``PostToolUse``
is crushed for every future request in that session, and it never enters the
prompt prefix at full size, so there is no cached prefix to invalidate and no
cache-write cost to weigh against the saving. That is the same argument the
Cursor shell hook already makes; this brings it to the agent most users run.

What this must never do is corrupt a read. ``updatedToolOutput`` replaces what
the model sees, so a mutilated file read is indistinguishable from the real
file and the agent will edit against content that was never on disk. The rules
below are therefore conservative by construction:

* **Only Bash output is rewritten.** Read, Grep and Glob are left alone. A file
  read is the single most likely input to a subsequent edit, and the proxy path
  already skeletonizes stale reads where it can prove a newer read exists.
  ``PostToolUse`` has no such trajectory view, so it does not guess.
* **Source code is never crushed**, enforced by the existing detector.
* **Failures pass through.** A non-zero exit means the agent is debugging, and
  that is when every line matters least to a token budget and most to the user.
"""
from __future__ import annotations

import json
import sys

#: Tools whose output may be rewritten. Bash is the original and still the
#: bulk of it: machine chatter the agent has already acted on, where the
#: repetitive mass lives. Read/Grep/Glob are absent by design, because their
#: results feed edits.
#:
#: The additions are tools other agents have that Claude Code does not.
#: Measured across 70 real jcode sessions, restricting to Bash declined 1.65M
#: tokens of tool output, more than Bash itself contains. Each addition keeps
#: 100% of answer-critical lines on that corpus.
#:
#: Three exclusions are deliberate, and each was checked rather than assumed:
#:
#: * `batch` is a fan-out, and on the same corpus its calls were 383 bash but
#:   also **171 read**. Its result therefore embeds file reads, so compressing
#:   it launders exactly the payload the Read exclusion exists to protect. It
#:   is the largest single block of savings given up here, and it is given up
#:   on purpose.
#: * `browser` and `webfetch` return a fetched page. There is nothing safely
#:   separable in prose, and the 98% "saving" once measured for `browser` was
#:   the JSON crusher eliding the page itself.
#:
#: Matching is case-insensitive on the caller's side, because agents disagree
#: on capitalisation for the same tool (`Bash` in Claude Code, `bash` in
#: jcode).
COMPRESSIBLE_TOOLS = frozenset({
    "bash", "bg", "todo", "ls", "agentgrep",
})

#: Below this, compression cannot pay for the risk of eliding something.
MIN_CHARS = 2_000


def _tool_response_text(response) -> tuple[str, str] | None:
    """Return (field, text) for the response field worth compressing.

    Bash returns a structured object; older shapes returned a bare string. A
    string cannot be returned through ``updatedToolOutput`` without violating
    the tool's output shape, in which case Claude Code discards the value and
    uses the original -- a silent no-op rather than a corruption, but a no-op
    all the same, so those are skipped explicitly.
    """
    if not isinstance(response, dict):
        return None
    stdout = response.get("stdout")
    if isinstance(stdout, str) and len(stdout) >= MIN_CHARS:
        return ("stdout", stdout)
    return None


def should_compress(request: dict) -> bool:
    """Whether this tool result is safe and worth rewriting."""
    if str(request.get("tool_name") or "").lower() not in COMPRESSIBLE_TOOLS:
        return False
    response = request.get("tool_response")
    if not isinstance(response, dict):
        return False
    # A command that failed is a command the user is debugging. Eliding
    # "repetitive" lines from a stack trace removes the thing being looked for.
    if response.get("interrupted"):
        return False
    for key in ("exit_code", "exitCode", "returncode"):
        code = response.get(key)
        if isinstance(code, int) and code != 0:
            return False
    if response.get("isImage"):
        return False
    return _tool_response_text(response) is not None


def build_response(request: dict, *, ledger: bool = False) -> dict:
    """Build the PostToolUse hook response. Fails open to an empty decision."""
    if not should_compress(request):
        return {}

    # Imported here rather than at module scope: this hook runs on every Bash
    # call, and most of them are short enough to be declined above. Loading a
    # tokenizer to reach that conclusion would tax every command the user runs.
    from memor.compress import compress_text
    from memor.compress.detect import looks_like_source

    response = dict(request.get("tool_response") or {})
    found = _tool_response_text(response)
    if found is None:
        return {}
    field, text = found

    # The source guard is the safety property this hook rests on: the log
    # crusher deletes lines it considers repetitive, which is right for build
    # output and catastrophic for a heredoc'd file or a `cat` of a module.
    #
    # This gate runs *before* `compress_text`, so anything `detect_content_type`
    # learns to route safely is dead code here unless the guard defers to it.
    # Two content types have earned that: a diff, whose +/- lines and hunk
    # headers stay byte-exact while only on-disk-recoverable context goes, and
    # a fetched document, which announces its own provenance and which nobody
    # edits. Both are full of code and so are claimed by the guard.
    #
    # Asking the classifier rather than listing exemptions keeps this in step
    # with `detect_content_type`: a future safe type is handled by adding it
    # there, not by remembering to patch this line too.
    from memor.compress import detect_content_type

    if looks_like_source(text) and detect_content_type(text) == "source":
        return {}

    result = compress_text(text)
    if result.passthrough or result.text == text:
        return {}
    # Refuse a rewrite that does not clearly pay. Every rewrite costs the user
    # some fidelity; a marginal one spends that for nothing.
    if result.tokens_after >= result.tokens_before:
        return {}

    response[field] = result.text
    saved = result.tokens_before - result.tokens_after
    if ledger:
        record_savings(
            request, result.tokens_before, result.tokens_after,
            result.content_type or "log",
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": response,
            "additionalContext": (
                f"[memor] Compressed this command's output "
                f"{result.tokens_before}->{result.tokens_after} tokens "
                f"(saved {saved}). Omitted regions are marked inline with the "
                f"line counts they replaced; rerun the command if you need "
                f"the full output."
            ),
        }
    }


def record_savings(request: dict, before: int, after: int, content_type: str) -> None:
    """Log the saving to the same ledger the proxy writes. Best effort.

    Written to ``proxy_savings`` rather than a second table so the dashboard's
    existing figures cover both paths: a user who saves tokens through hooks
    and never installs the proxy should still see the savings they got.
    """
    try:
        import time
        from pathlib import Path

        from memor.store.sqlite_store import SqliteStore, read_dim

        db_path = str(Path.home() / ".memor" / "memor.db")
        if not Path(db_path).exists():
            return
        store = SqliteStore(db_path, dim=read_dim(db_path, 256))
        store.record_proxy_savings({
            "timestamp": time.time(),
            "agent": request.get("_memor_agent") or "claude",
            "provider": "hook",
            "session_id": request.get("session_id"),
            "tokens_before": before,
            "tokens_after": after,
            "content_types": {content_type: 1},
            "passthrough": 0,
        })
    except Exception:
        pass


def main() -> None:
    """Entry point. Silence plus exit 0 means "no decision", the safe default."""
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    try:
        response = build_response(request, ledger=True)
    except Exception:
        # A crash here must not cost the user their tool result.
        sys.exit(0)

    if not response:
        sys.exit(0)

    print(json.dumps(response))
    sys.exit(0)
