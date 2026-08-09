"""Measure the PostToolUse hook against real sessions on this machine.

The figures published for this feature -- 61.8% when it fires, 17.1% across
all large Bash output, 19.1% coverage -- came from a throwaway script. A number
nobody can re-derive is a number nobody can check, and this project has already
published two figures that turned out to describe the wrong population: a
"96.5% saving" that was really its own test fixtures, and a "38.6% on 569 Bash
results" whose denominator was 70% Read output the hook never receives.

So the measurement lives here, runs on demand, and reports the denominator it
used rather than only the ratio.

Only Bash results count. The hook matches on Bash alone, so including Read,
Grep or Glob would measure a population it cannot act on and understate it for
declining work it was designed to decline.
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


def _bash_results(session_dir: Path, limit: int) -> list[str]:
    """Large Bash tool results from the most recent sessions.

    A tool_result carries only a tool_use_id, so the tool name has to be
    recovered from the assistant's earlier tool_use block. Skipping that join
    is exactly how Read output ended up in a figure about Bash.
    """
    files = _recent_sessions(session_dir, limit)
    found: list[str] = []
    for path in files:
        names: dict[str, str] = {}
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
                    names[block.get("id")] = block.get("name")
                elif block.get("type") == "tool_result":
                    if names.get(block.get("tool_use_id")) != "Bash":
                        continue
                    raw = block.get("content")
                    if isinstance(raw, str):
                        text = raw
                    elif isinstance(raw, list):
                        text = " ".join(b.get("text", "") for b in raw
                                        if isinstance(b, dict))
                    else:
                        continue
                    if text and len(text) >= MIN_CHARS:
                        found.append(text)
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
    lines.append("")
    lines.append("Read, Grep and Glob results are excluded, not counted as missed")
    lines.append("coverage: the hook declines them by design, because a mutilated")
    lines.append("file read is something an agent will edit against.")
    return lines


def report(session_dir: Path | None = None,
           limit: int = DEFAULT_SESSION_LIMIT) -> list[str]:
    return format_report(measure(session_dir, limit))
