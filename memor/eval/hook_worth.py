"""Measure the PostToolUse hook against real sessions on this machine.

The figures published for this feature -- 61.8% when it fires, 17.1% across
all large Bash output, 19.1% coverage -- came from a throwaway script. A number
nobody can re-derive is a number nobody can check, and this project has already
published two figures that turned out to describe the wrong population: a
"96.5% saving" that was really its own test fixtures, and a "38.6% on 569 Bash
results" whose denominator was 70% Read output the hook never receives.

So the measurement lives here, runs on demand, and reports the denominator it
used rather than only the ratio.

The headline counts Bash results only, so its figures stay comparable with the
ones already published. Grep (content mode) and MCP results, which the hook
now rewrites losslessly, are measured separately and never folded into it.
Read and Glob are not measured at all: the hook declines them by design, and
counting them would understate it for declining work it was built to decline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Below this a payload is not worth compressing, matching the hook's own floor.
MIN_CHARS = 2_000

#: Sessions are scanned newest-first; enough to be representative without
#: reading a gigabyte of transcript for a number the user asked for now.
DEFAULT_SESSION_LIMIT = 60


@dataclass
class HookMeasurement:
    """What the hook did to real Bash output, and what it declined."""

    payloads: int = 0
    tokens: int = 0
    compressed: int = 0
    compressed_before: int = 0
    compressed_after: int = 0
    #: Declined because the payload is source code an agent may edit against.
    guarded: int = 0
    guarded_tokens: int = 0
    #: Declined for any other reason: nothing compressible, a failed command.
    passed_through: int = 0
    sessions: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    #: Lossless rewrites, by kind ("grep", "mcp"): payloads, tokens, rewritten,
    #: before, after. Kept apart from the Bash totals above.
    lossless: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def saved(self) -> int:
        return max(0, self.compressed_before - self.compressed_after)

    @property
    def rate_when_firing(self) -> float:
        """Saving on the payloads the hook actually rewrote."""
        if self.compressed_before <= 0:
            return 0.0
        return self.saved / self.compressed_before * 100

    @property
    def rate_overall(self) -> float:
        """Saving across all large Bash output, including what was declined."""
        if self.tokens <= 0:
            return 0.0
        return self.saved / self.tokens * 100

    @property
    def coverage(self) -> float:
        if self.payloads <= 0:
            return 0.0
        return self.compressed / self.payloads * 100


def _recent_sessions(session_dir: Path, limit: int) -> list[Path]:
    """The most recently modified transcripts.

    Ordering by path instead of mtime picks an arbitrary subset that happens to
    sort last, which is not the same as the most recent work and is not stable
    across two ways of listing the same directory. Two measurements of this
    feature disagreed by 139 payloads for exactly that reason -- one had
    included subagent transcripts, the other had not -- so the selection is
    made explicit rather than inherited from a glob.
    """
    files = list(session_dir.rglob("*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    return files[-limit:]


def _tool_results(session_dir: Path, limit: int):
    """Yield (tool name, tool input, raw result content) from recent sessions.

    A tool_result carries only a tool_use_id, so the tool name has to be
    recovered from the assistant's earlier tool_use block. Skipping that join
    is exactly how Read output ended up in a figure about Bash.
    """
    for path in _recent_sessions(session_dir, limit):
        calls: dict[str, tuple[str, dict]] = {}
        try:
            lines = path.read_text(errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            content = (record.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    calls[block.get("id")] = (block.get("name") or "",
                                              block.get("input") or {})
                elif block.get("type") == "tool_result":
                    name, tool_input = calls.get(block.get("tool_use_id"), ("", {}))
                    yield name, tool_input, block.get("content")


def _flatten(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return " ".join(b.get("text", "") for b in raw if isinstance(b, dict))
    return ""


def _bash_results(session_dir: Path, limit: int) -> list[str]:
    """Large Bash tool results from the most recent sessions."""
    found: list[str] = []
    for name, _, raw in _tool_results(session_dir, limit):
        if name != "Bash":
            continue
        text = _flatten(raw)
        if text and len(text) >= MIN_CHARS:
            found.append(text)
    return found


def _lossless_requests(session_dir: Path, limit: int) -> list[tuple[str, dict]]:
    """Grep (content mode) and MCP results, shaped as the hook receives them.

    Rebuilt from the transcript, which stores what the model saw rather than
    the tool's structured output, so this is a replay, not a recording.
    """
    found: list[tuple[str, dict]] = []
    for name, tool_input, raw in _tool_results(session_dir, limit):
        if name == "Grep" and tool_input.get("output_mode") == "content":
            text = _flatten(raw)
            if len(text) >= MIN_CHARS:
                found.append(("grep", {"tool_name": "Grep", "tool_response": {
                    "mode": "content", "content": text}}))
        elif name.startswith("mcp__") and len(_flatten(raw)) >= MIN_CHARS:
            found.append(("mcp", {"tool_name": name, "tool_response": raw}))
    return found


def measure(session_dir: Path | None = None,
            limit: int = DEFAULT_SESSION_LIMIT) -> HookMeasurement:
    """Run the real hook over real Bash output and total up what happened."""
    from memor.compress.detect import looks_like_source
    from memor.posttool_compress import build_response
    from memor.tokencount import count_tokens

    if session_dir is None:
        session_dir = Path.home() / ".claude" / "projects"

    result = HookMeasurement()
    if not session_dir.exists():
        return result

    result.sessions = len(_recent_sessions(session_dir, limit))

    for text in _bash_results(session_dir, limit):
        tokens = count_tokens(text)
        result.payloads += 1
        result.tokens += tokens

        # The real hook, not a reimplementation of its rules.
        response = build_response({
            "tool_name": "Bash",
            "tool_response": {"stdout": text, "stderr": "",
                              "interrupted": False, "isImage": False},
        })
        if response:
            shown = response["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
            result.compressed += 1
            result.compressed_before += tokens
            result.compressed_after += count_tokens(shown)
            from memor.compress.detect import detect_content_type
            kind = detect_content_type(text)
            result.by_type[kind] = result.by_type.get(kind, 0) + 1
        elif looks_like_source(text):
            result.guarded += 1
            result.guarded_tokens += tokens
        else:
            result.passed_through += 1

    for kind, request in _lossless_requests(session_dir, limit):
        bucket = result.lossless.setdefault(
            kind, {"payloads": 0, "tokens": 0, "rewritten": 0, "before": 0, "after": 0})
        tokens = count_tokens(_flatten(request["tool_response"].get("content")
                                       if kind == "grep" else request["tool_response"]))
        bucket["payloads"] += 1
        bucket["tokens"] += tokens
        response = build_response(request)
        if response:
            shown = response["hookSpecificOutput"]["updatedToolOutput"]
            shown = shown.get("content") if kind == "grep" else shown
            bucket["rewritten"] += 1
            bucket["before"] += tokens
            bucket["after"] += count_tokens(_flatten(shown))

    return result


def format_report(m: HookMeasurement) -> list[str]:
    lines = ["memor tool-output compression — measured on your own sessions",
             "=" * 62]
    if m.payloads == 0:
        lines.append("No large Bash results found in recent sessions.")
        lines.append("")
        lines.append("Nothing to measure yet: the hook only sees Bash output,")
        lines.append(f"and only output over {MIN_CHARS:,} characters.")
        return lines

    lines.append(f"scanned {m.sessions} sessions, {m.payloads} large Bash results "
                 f"({m.tokens:,} tokens)")
    lines.append("")
    lines.append(f"compressed        {m.compressed:>5}   "
                 f"{m.compressed_before:>9,} -> {m.compressed_after:,}")
    lines.append(f"held by source guard {m.guarded:>2}   {m.guarded_tokens:>9,} tokens")
    lines.append(f"nothing to compress  {m.passed_through:>2}")
    lines.append("")

    if m.by_type:
        lines.append("Compressor that fired:")
        for name, count in sorted(m.by_type.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:>5}  {name}")
        lines.append("")

    lines.append("=" * 62)
    lines.append(f"WHEN IT FIRES:  {m.rate_when_firing:.1f}% saved")
    lines.append(f"ACROSS ALL BASH OUTPUT: {m.rate_overall:.1f}% "
                 f"(coverage {m.coverage:.1f}%)")
    lines.extend(_lossless_lines(m))
    lines.append("")
    lines.append("Read and Glob results are excluded, not counted as missed")
    lines.append("coverage: the hook declines them by design, because a mutilated")
    lines.append("file read is something an agent will edit against.")
    return lines


def _lossless_lines(m: HookMeasurement) -> list[str]:
    """Grep and MCP, reported apart from Bash: nothing in them is ever dropped."""
    if not m.lossless:
        return []
    lines = ["", "LOSSLESS REWRITES (content kept byte-for-byte; reported apart from Bash)"]
    labels = {"grep": "Grep, content mode", "mcp": "MCP tool results"}
    for kind in ("grep", "mcp"):
        b = m.lossless.get(kind)
        if not b:
            continue
        saved = b["before"] - b["after"]
        rate = saved / b["tokens"] * 100 if b["tokens"] else 0.0
        lines.append(f"  {labels[kind]:<20} {b['payloads']:>4} large, {b['rewritten']:>4} "
                     f"rewritten   saved {saved:,} of {b['tokens']:,} tokens ({rate:.1f}%)")
    lines.append("  Replayed from transcripts, which store what the model saw.")
    return lines


def report(session_dir: Path | None = None,
           limit: int = DEFAULT_SESSION_LIMIT) -> list[str]:
    return format_report(measure(session_dir, limit))
