"""Trim output tokens, but never on a turn that is writing or fixing code.

Output is 43% of spend on this machine's real traffic (68,541 output tokens
against 461,385 input, and output bills at 5x). It is also the half no
compressor touches, because it does not exist yet when the request is sent. The
only lever is asking the model to write less.

That lever is dangerous, so the shape of it here is narrower than the obvious
version:

Effort routing is not implemented at all. The natural trigger -- "this turn is
just resuming after a tool result" -- covers 93% of assistant turns on real
transcripts, because 92% of user-typed records ARE tool results. Among those,
28% follow a tool result carrying an error signal. Dialling thinking down there
targets precisely the turn where an agent reads a failing test and has to work
out why. A mechanism that fires on nearly every turn is not routing.

Verbosity steering is implemented, and suppressed whenever the turn could
plausibly produce code:

  - the conversation has used a write tool (Edit/Write/MultiEdit/apply_patch)
  - the latest tool result carries an error signal
  - the request exposes write tools at all and the prompt asks for a change

Measured on the same transcripts, 68% of output characters are tool inputs --
the code itself -- and 42% of prose sits inside code-writing turns, where it is
the reasoning that produced the edit. Neither is safe to compress. What is left
is ceremony in turns that touch no code, roughly 20% of output.

The instruction is appended to the END of the system prompt so the cached
prefix still hits: rewriting the front of the system prompt would convert cheap
cache reads into full-price writes and cost more than it saves.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: Appended verbatim. Deliberately says nothing about code, length of diffs, or
#: level of detail in explanations -- only about ceremony. An instruction like
#: "be brief" reaches the code too.
STEER_NOTE = (
    "\n\nResponse style: skip preamble and sign-off. Do not restate code or "
    "file contents that are already in context. When you have made a change, "
    "say what changed and why, without repeating the diff. Never abbreviate, "
    "elide, or summarise code you are writing: correctness comes before brevity."
)

#: Tools whose presence means this conversation edits code.
WRITE_TOOLS = frozenset({
    "Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch",
    "str_replace_editor", "create_file", "edit_file",
})

#: A tool result carrying one of these is a failure the model must reason about.
ERROR_SIGNAL = re.compile(
    r"\b(error|failed|failure|traceback|exception|assertionerror|fatal|"
    r"panic|denied|refused|not found|undefined|segfault|timeout|"
    r"conflict|syntaxerror|typeerror|cannot)\b", re.I)

#: Verbs that mean the user is asking for a change rather than an explanation.
CHANGE_INTENT = re.compile(
    r"\b(fix|implement|add|remove|refactor|rename|migrate|write|create|"
    r"update|change|patch|build|delete|revert|bump|wire|hook up)\b", re.I)

ENV_FLAG = "MEMOR_OUTPUT_SHAPER"
ENV_HOLDOUT = "MEMOR_OUTPUT_HOLDOUT"


@dataclass
class ShapeDecision:
    shaped: bool
    reason: str


def is_enabled() -> bool:
    """Off unless explicitly switched on. This changes what the model writes."""
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def holdout_fraction() -> float:
    """Share of conversations left unshaped, so the saving can be measured.

    Without a control arm the saving is counterfactual -- we never see what the
    model would have written -- and any number reported is an estimate dressed
    as a measurement. A holdout also gives the safety comparison something to
    compare against: edit sizes and retry rates, shaped against unshaped.
    """
    raw = os.environ.get(ENV_HOLDOUT, "0.1").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.1
    return min(max(value, 0.0), 1.0)


def in_holdout(conversation_key: str, fraction: float | None = None) -> bool:
    """Stable per-conversation assignment.

    Hashed rather than random so a conversation stays in one arm for its whole
    life. Flipping mid-conversation would contaminate both arms and make the
    comparison meaningless.
    """
    frac = holdout_fraction() if fraction is None else fraction
    if frac <= 0:
        return False
    if frac >= 1:
        return True
    import hashlib

    digest = hashlib.sha256((conversation_key or "").encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return bucket < frac


def _messages(provider: str, body: dict) -> list:
    return body.get("messages") or []


def _declares_write_tools(body: dict) -> bool:
    for tool in body.get("tools") or []:
        name = tool.get("name") or (tool.get("function") or {}).get("name")
        if name in WRITE_TOOLS:
            return True
    return False


def _used_write_tool(messages: list) -> bool:
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in WRITE_TOOLS:
                return True
    return False


def _latest_tool_result_text(messages: list) -> str:
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    parts += [b.get("text", "") for b in inner if isinstance(b, dict)]
        if parts:
            return " ".join(parts)[:8000]
    return ""


def _latest_user_text(messages: list) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content[:4000]
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return " ".join(texts)[:4000]
    return ""


def decide(provider: str, body: dict, *, conversation_key: str = "") -> ShapeDecision:
    """Whether this request may be shaped, and why.

    Every gate fails closed: anything unrecognised is left unshaped. The cost of
    declining is a few wasted tokens; the cost of a wrong shape is a truncated
    edit in shipped code.
    """
    if not is_enabled():
        return ShapeDecision(False, "disabled")

    if in_holdout(conversation_key):
        return ShapeDecision(False, "holdout")

    messages = _messages(provider, body)
    if not messages:
        return ShapeDecision(False, "no_messages")

    if _used_write_tool(messages):
        return ShapeDecision(False, "conversation_writes_code")

    result_text = _latest_tool_result_text(messages)
    if result_text and ERROR_SIGNAL.search(result_text):
        return ShapeDecision(False, "error_in_tool_result")

    if _declares_write_tools(body) and CHANGE_INTENT.search(_latest_user_text(messages)):
        return ShapeDecision(False, "change_requested_with_write_tools")

    return ShapeDecision(True, "shaped")


def apply(provider: str, body: dict, *, conversation_key: str = "") -> tuple[dict, ShapeDecision]:
    """Append the style note when the gates allow it. Never mutates the input."""
    decision = decide(provider, body, conversation_key=conversation_key)
    if not decision.shaped:
        return body, decision

    shaped = dict(body)
    system = shaped.get("system")

    if isinstance(system, str):
        shaped["system"] = system + STEER_NOTE
        return shaped, decision

    if isinstance(system, list):
        # Anthropic's block form. Append to the last text block rather than
        # adding one, so the cached prefix is untouched.
        blocks = [dict(b) if isinstance(b, dict) else b for b in system]
        for block in reversed(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = block.get("text", "") + STEER_NOTE
                shaped["system"] = blocks
                return shaped, decision
        return body, ShapeDecision(False, "no_text_block_in_system")

    if system is None and provider != "anthropic":
        # OpenAI-compatible: the system prompt is the first message.
        messages = list(shaped.get("messages") or [])
        for idx, msg in enumerate(messages):
            if msg.get("role") == "system" and isinstance(msg.get("content"), str):
                updated = dict(msg)
                updated["content"] = msg["content"] + STEER_NOTE
                messages[idx] = updated
                shaped["messages"] = messages
                return shaped, decision
        return body, ShapeDecision(False, "no_system_message")

    return body, ShapeDecision(False, "unrecognised_system_shape")
