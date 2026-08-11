"""What a coding-agent request is made of, and what that caps compression at.

Written because the question "why is our compression only 7%" kept being
answered with theories about the compressor, when the answer is arithmetic
about the request. Anyone re-asking it should be able to run this and see the
ceiling directly.

The analysis has to price images correctly, and that is the trap. A base64
payload tokenises as an enormous string -- a 161 KB screenshot counts as 113,367
tokens if you feed the encoded text to a tokenizer -- but providers bill an
image by its dimensions, roughly width*height/750, which is on the order of a
thousand tokens. Counting the base64 put images at 31% of a session and made
them look like the biggest prize available. They are closer to 1%.

The cross-check that catches it: the largest request this proxy ever billed was
326,756 tokens. Any per-session estimate that implies far more than the provider
ever charged for is measuring something the provider does not.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Anthropic bills roughly width*height/750 tokens per image. Without decoding
#: the image we cannot know its dimensions, so estimate from the decoded byte
#: size: a screenshot compresses to well under a byte per pixel, and this
#: constant is calibrated to land in the low thousands for a typical capture.
#: Deliberately an over-estimate: if images were ever a real cost, this should
#: be the number that says so.
_IMAGE_TOKENS_PER_KB = 8

#: Categories in the order they matter for compression policy.
COMPRESSIBLE = "tool_result payloads"
GENERATED_CODE = "tool_use arguments"
CONVERSATION = "conversation text"
IMAGES = "images"
OTHER = "other"


@dataclass
class Anatomy:
    tokens: Counter = field(default_factory=Counter)
    images: int = 0
    messages: int = 0

    @property
    def total(self) -> int:
        return sum(self.tokens.values())

    def share(self, bucket: str) -> float:
        if not self.total:
            return 0.0
        return 100.0 * self.tokens[bucket] / self.total


def analyse(messages: list[dict], *, count_tokens=None) -> Anatomy:
    """Bucket a request's tokens by what the bytes are."""
    if count_tokens is None:
        from memor.tokencount import count_tokens as _ct
        count_tokens = _ct

    out = Anatomy()
    for message in messages:
        out.messages += 1
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            out.tokens[CONVERSATION] += count_tokens(content)
            continue
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")

            if kind == "text":
                out.tokens[CONVERSATION] += count_tokens(block.get("text", ""))
            elif kind == "thinking":
                out.tokens[CONVERSATION] += count_tokens(str(block.get("thinking", "")))
            elif kind == "tool_use":
                out.tokens[GENERATED_CODE] += count_tokens(
                    json.dumps(block.get("input", {})))
            elif kind == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = " ".join(b.get("text", "") for b in inner
                                     if isinstance(b, dict))
                out.tokens[COMPRESSIBLE] += count_tokens(str(inner or ""))
            elif kind == "image":
                out.images += 1
                data = (block.get("source") or {}).get("data") or ""
                # Never tokenise the base64. Estimate from decoded size.
                decoded_kb = len(data) * 0.75 / 1024
                out.tokens[IMAGES] += int(decoded_kb * _IMAGE_TOKENS_PER_KB)
            else:
                out.tokens[OTHER] += count_tokens(json.dumps(block))
            _ = role
    return out


def ceiling_pct(anatomy: Anatomy, *, rate_on_compressible: float) -> float:
    """Best achievable saving on a whole request, given a rate on tool output.

    ``rate_on_compressible`` is the fraction removed from tool results, the only
    category memor may rewrite. Generated code is what the agent is producing and
    eliding it corrupts edits; conversation text cannot be rewritten without
    re-forming the provider's cached prefix.
    """
    return anatomy.share(COMPRESSIBLE) * rate_on_compressible


def load_messages(transcript: Path) -> list[dict]:
    messages: list[dict] = []
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except Exception:
        return messages
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        msg = rec.get("message")
        if isinstance(msg, dict):
            messages.append({"role": msg.get("role") or rec["type"],
                             "content": msg.get("content", "")})
    return messages


def format_report(anatomy: Anatomy, *, rate: float = 0.433) -> str:
    lines = [
        f"Request anatomy — {anatomy.messages:,} messages, {anatomy.total:,} tokens",
        "",
    ]
    for bucket, tokens in anatomy.tokens.most_common():
        note = ""
        if bucket == COMPRESSIBLE:
            note = "  <- the only slice memor may rewrite"
        elif bucket == GENERATED_CODE:
            note = "  <- code being written; eliding it corrupts edits"
        elif bucket == CONVERSATION:
            note = "  <- rewriting re-forms the cached prefix"
        lines.append(f"  {bucket:24} {tokens:>12,}  ({anatomy.share(bucket):5.1f}%){note}")
    if anatomy.images:
        lines.append(f"  ({anatomy.images} image blocks, billed by dimensions "
                     f"rather than base64 length)")
    lines += [
        "",
        f"  At {100*rate:.1f}% on tool output, the ceiling for a whole request is "
        f"{ceiling_pct(anatomy, rate_on_compressible=rate):.1f}%.",
    ]
    return "\n".join(lines)
