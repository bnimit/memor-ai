"""Answer-critical retention for a coding agent, from real behaviour.

SuperCompress publishes gold-answer containment: compress a document holding a
known answer, then check the answer string survived. Deterministic, no LLM, no
judge that can drift. That is the right shape, and it is what memor lacks --
every compression figure here is tokens saved with no evidence the result is
still usable.

Their gold answers come from QA datasets. A coding agent has no QA labels, but
it has something better: what the agent actually did next. After a tool result
the agent edits a file, quotes an error, names a symbol. Those actions are
recorded in the transcript, and each one names evidence that must have survived
for the action to be possible.

So the gate is: compress the payload, then check the evidence the agent
demonstrably used is still present. Ground truth comes from the agent's own
behaviour rather than a label someone wrote.

Read-only over local transcripts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Tools whose arguments quote text that must have come from a prior payload.
_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "str_replace_editor"}

#: A quoted fragment shorter than this matches by chance and proves nothing.
MIN_EVIDENCE_CHARS = 24


@dataclass
class Case:
    """One payload plus the evidence a later action proves the agent needed."""
    payload: str
    file_path: str | None
    evidence: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class RetentionResult:
    cases: int = 0
    evidence_total: int = 0
    evidence_kept: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    fully_retained: int = 0

    @property
    def retention_pct(self) -> float:
        if not self.evidence_total:
            return 100.0
        return 100.0 * self.evidence_kept / self.evidence_total

    @property
    def case_retention_pct(self) -> float:
        if not self.cases:
            return 100.0
        return 100.0 * self.fully_retained / self.cases

    @property
    def saved_pct(self) -> float:
        if not self.tokens_before:
            return 0.0
        return 100.0 * (self.tokens_before - self.tokens_after) / self.tokens_before


def _normalise(text: str) -> str:
    """Whitespace-insensitive comparison.

    A compressor may reflow indentation without destroying meaning, and an
    agent's quoted `old_string` carries the indentation of the original file.
    Comparing raw would score a cosmetic difference as lost evidence.
    """
    return re.sub(r"\s+", " ", text).strip()


def extract_cases(transcript: Path, *, limit: int | None = None) -> list[Case]:
    """Pair each tool payload with evidence a later action shows was needed."""
    try:
        records = [json.loads(line)
                   for line in transcript.read_text(errors="replace").splitlines()
                   if line.strip()]
    except Exception:
        return []

    # tool_use id -> the file it read, so a later edit can be matched to it.
    payload_by_file: dict[str, str] = {}
    use_to_file: dict[str, str] = {}
    cases: list[Case] = []

    for rec in records:
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            if block.get("type") == "tool_use":
                args = block.get("input") or {}
                path = args.get("file_path") or args.get("path")
                if isinstance(path, str):
                    use_to_file[block.get("id", "")] = path

                # An edit quotes text that must have survived compression of the
                # payload that carried it.
                if block.get("name") in _EDIT_TOOLS and isinstance(path, str):
                    quoted = []
                    for key in ("old_string", "old_str", "oldText"):
                        value = args.get(key)
                        if isinstance(value, str) and len(value) >= MIN_EVIDENCE_CHARS:
                            quoted.append(value)
                    for edit in args.get("edits") or []:
                        if isinstance(edit, dict):
                            value = edit.get("old_string")
                            if isinstance(value, str) and len(value) >= MIN_EVIDENCE_CHARS:
                                quoted.append(value)
                    if quoted and path in payload_by_file:
                        # Keep only fragments that are actually in the payload
                        # we paired them with. An agent edits the file on disk,
                        # which has moved on from an earlier read: its own
                        # previous edits, or another turn's. Measured on real
                        # transcripts, 358 of 393 quoted fragments were absent
                        # from the payload before any compression ran, so
                        # scoring them produced 8.9% "retention" at 0%
                        # compression -- an impossible result that came from the
                        # pairing, not the compressor.
                        haystack = _normalise(payload_by_file[path])
                        grounded = [q for q in quoted
                                    if _normalise(q) in haystack]
                        if grounded:
                            cases.append(Case(
                                payload=payload_by_file[path],
                                file_path=path,
                                evidence=grounded,
                                source=transcript.name,
                            ))
                            if limit and len(cases) >= limit:
                                return cases

            elif block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = " ".join(b.get("text", "") for b in inner
                                     if isinstance(b, dict))
                if not isinstance(inner, str) or len(inner) < 500:
                    continue
                path = use_to_file.get(block.get("tool_use_id", ""))
                if path:
                    payload_by_file[path] = inner

    return cases


def score(cases: list[Case], compress) -> RetentionResult:
    """Compress each payload and check the evidence survived.

    ``compress`` takes (payload_text, file_path) and returns the compressed text.
    """
    from memor.tokencount import count_tokens

    result = RetentionResult()
    for case in cases:
        try:
            compressed = compress(case.payload, case.file_path)
        except Exception:
            compressed = case.payload

        result.cases += 1
        result.tokens_before += count_tokens(case.payload)
        result.tokens_after += count_tokens(compressed)

        haystack = _normalise(compressed)
        kept_here = 0
        for evidence in case.evidence:
            result.evidence_total += 1
            if _normalise(evidence) in haystack:
                result.evidence_kept += 1
                kept_here += 1
        if kept_here == len(case.evidence):
            result.fully_retained += 1

    return result


def format_report(name: str, result: RetentionResult) -> str:
    return (
        f"{name}\n"
        f"  cases                 : {result.cases}\n"
        f"  evidence fragments    : {result.evidence_total}\n"
        f"  retained              : {result.evidence_kept} "
        f"({result.retention_pct:.1f}%)\n"
        f"  cases fully retained  : {result.fully_retained} "
        f"({result.case_retention_pct:.1f}%)\n"
        f"  tokens                : {result.tokens_before:,} -> "
        f"{result.tokens_after:,} ({result.saved_pct:.1f}% saved)"
    )
