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

* **Only Bash-like output is compressed lossily.** Read and Glob are left
  alone. A file read is the single most likely input to a subsequent edit, and
  the proxy path already skeletonizes stale reads where it can prove a newer
  read exists. ``PostToolUse`` has no such trajectory view, so it does not
  guess. Grep (content mode) and MCP results are rewritten too, but only
  losslessly; see ``LOSSLESS_TOOL_PATTERNS``.
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

#: Tools whose output is only ever rewritten *losslessly*: every byte of
#: content survives, only redundant structure goes. They sit outside
#: ``COMPRESSIBLE_TOOLS`` because no line-dropping compressor may touch them.
#:
#: * Claude Code's ``Grep`` in content mode, through ``compress_search`` alone,
#:   which keeps every file, line number and match and only stops repeating the
#:   path on each line. The old objection, that grep results feed edits, is
#:   weaker than it was for Read: Claude Code refuses an Edit to a file the
#:   agent has not Read, so the edit is made against the Read, not the grep.
#: * MCP tool results (``mcp__*``), by removing JSON formatting whitespace and
#:   nothing else. The JSON crusher is deliberately not used: it samples long
#:   arrays, which is wrong when the list *is* the answer (thirty issues asked
#:   for, ten returned). Whitespace is stripped by a scanner rather than by
#:   parsing and re-printing, which would round long decimals and merge
#:   duplicate keys.
LOSSLESS_TOOL_PATTERNS = ("grep", "mcp__.*")


#: Below this, compression cannot pay for the risk of eliding something.
#:
#: Was 2,000, which was a guess. Measured over 400 sessions, the band between
#: 1KB and 2KB holds 1,147 payloads worth 0.57% of tool-result tokens -- 80% of
#: everything available beneath the old floor. Going lower adds 0.14% across
#: 2,600 more payloads, so the remaining tail is not worth the calls.
#:
#: Admitting the band was checked to the same standard as the log crusher
#: rather than assumed. Of the newly admitted payloads the log bucket loses no
#: answer-critical line at all; the 112 apparent losses in search output were
#: the path-prefix fold that `compress_search` performs by design (111 of them)
#: plus one `=== section header ===`, and the 40 in JSON were `},` lines that
#: disappear when the crusher reindents. No match text and no code was lost.
MIN_CHARS = 1_000


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


#: Longest prompt text used as a query. A pasted log in the prompt would
#: otherwise turn every word in it into a term.
_QUERY_PROMPT_CHARS = 2_000


def _last_user_prompt(transcript_path) -> str:
    """The user's latest typed prompt, from the transcript's tail. Never raises.

    Bounded by the same tail window recall uses, so a long tool loop that has
    pushed the prompt out of the window yields nothing rather than a slow read.
    """
    if not transcript_path:
        return ""
    try:
        from memor.episodes import _prompt_text, is_user_prompt
        from memor.trajectory import read_tail

        for line in reversed(read_tail(transcript_path)):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_user_prompt(rec):
                return _prompt_text(rec)[:_QUERY_PROMPT_CHARS]
    except Exception:
        return ""
    return ""


def hook_query(request: dict) -> str:
    """What the user is looking for: the command, its description, the prompt.

    Passed to the log compressor so lines naming what was asked about survive.
    Safe here and nowhere else in the pipeline, because this hook compresses a
    payload once, before it enters the transcript.
    """
    tool_input = request.get("tool_input") or {}
    parts = [tool_input.get(k) for k in ("command", "description")]
    parts.append(_last_user_prompt(request.get("transcript_path")))
    return "\n".join(p for p in parts if isinstance(p, str) and p.strip())


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


def minify_json(text: str) -> str | None:
    """``text`` with whitespace outside strings removed, or None.

    None when it is not JSON, or when nothing would be saved. Byte-for-byte the
    same values: strings, numbers, key order and duplicate keys are untouched,
    which a parse-and-dump round trip does not guarantee.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    out: list[str] = []
    in_string = escaped = False
    for ch in stripped:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch not in " \t\n\r":
            out.append(ch)
    result = "".join(out)
    if len(result) >= len(text):
        return None
    try:
        json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return None
    return result


def _respond(request: dict, updated, before: int, after: int, content_type: str,
             note: str, *, ledger: bool) -> dict:
    if ledger:
        record_savings(request, before, after, content_type)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated,
            "additionalContext": (
                f"[memor] {note} {before}->{after} tokens (saved {before - after})."),
        }
    }


def _build_grep(request: dict, *, ledger: bool) -> dict:
    """Fold a content-mode Grep result; every match and line number is kept."""
    response = request.get("tool_response")
    if not isinstance(response, dict) or response.get("mode") != "content":
        return {}
    text = response.get("content")
    if not isinstance(text, str) or len(text) < MIN_CHARS:
        return {}
    from memor.compress.search import compress_search
    from memor.tokencount import count_tokens

    folded = compress_search(text)
    if folded == text:
        return {}
    before, after = count_tokens(text), count_tokens(folded)
    if after >= before:
        return {}
    return _respond(request, {**response, "content": folded}, before, after, "search",
                    "Folded repeated paths in this search output; every match and "
                    "line number is kept.", ledger=ledger)


def _build_mcp(request: dict, *, ledger: bool) -> dict:
    """Strip JSON formatting whitespace from an MCP result, in its own shape.

    MCP results arrive as a string or a list of content blocks, and are passed
    back to the model without schema validation, so the replacement keeps
    exactly the form the server sent.
    """
    response = request.get("tool_response")
    texts: list[str] = []
    if isinstance(response, str):
        texts = [response]
    elif isinstance(response, list):
        texts = [b["text"] for b in response
                 if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)]
    if sum(len(t) for t in texts) < MIN_CHARS:
        return {}
    minified = {t: minify_json(t) for t in texts}
    if not any(minified.values()):
        return {}
    if isinstance(response, str):
        updated = minified[response]
    else:
        updated = [
            {**b, "text": minified.get(b.get("text")) or b["text"]}
            if isinstance(b, dict) and b.get("type") == "text"
            and isinstance(b.get("text"), str) else b
            for b in response
        ]
    from memor.tokencount import count_tokens

    before = sum(count_tokens(t) for t in texts)
    after = sum(count_tokens(minified[t] or t) for t in texts)
    if after >= before:
        return {}
    return _respond(request, updated, before, after, "json",
                    "Removed JSON formatting whitespace from this result; no value "
                    "changed.", ledger=ledger)


def build_response(request: dict, *, ledger: bool = False) -> dict:
    """Build the PostToolUse hook response. Fails open to an empty decision."""
    name = str(request.get("tool_name") or "")
    if name.lower() == "grep":
        return _build_grep(request, ledger=ledger)
    if name.startswith("mcp__"):
        return _build_mcp(request, ledger=ledger)
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

    result = compress_text(text, query=hook_query(request))
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
