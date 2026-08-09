"""Token counting, with the encoder loaded only when it is actually used.

``tiktoken`` costs ~50ms to import, which is irrelevant to a long-lived daemon
and significant to a hook: the PostToolUse compressor runs once per Bash call,
and paying 50ms to decide that a two-line command output is too short to
compress is a bad trade the user makes hundreds of times a day.

Importing lazily means the cost is paid only on the calls that reach a
compressor, and never on the ones that bail out early.
"""
from __future__ import annotations

_enc = None


def _encoder():
    global _enc
    if _enc is None:
        import tiktoken

        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoder().encode(text))
