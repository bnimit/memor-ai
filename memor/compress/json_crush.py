from __future__ import annotations
import json
import re

#: Arrays at or below this length are kept whole.
_SAMPLE_THRESHOLD = 10
#: Guard against pathological nesting.
_MAX_DEPTH = 12

#: String values longer than this are truncated. Below it, a value is either
#: an identifier, a status, or a short message -- all worth keeping whole.
_LONG_VALUE_CHARS = 400

#: How much of a long value survives. The head carries the subject and the
#: tail usually carries the conclusion, so both ends are kept.
_VALUE_HEAD = 200
_VALUE_TAIL = 80

# Items mentioning failure are kept regardless of position — they are the reason
# anyone reads the payload.
_ERROR_PATTERN = re.compile(r'(error|fail|exception|fatal|critical)', re.IGNORECASE)

#: Keys whose values are never truncated regardless of length. A diff or a
#: traceback is the payload, not padding around it.
_VERBATIM_KEYS = frozenset({
    "diff", "diff_hunk", "patch", "traceback", "stack", "stacktrace",
    "error", "errors", "message", "reason",
})


def compress_json(text: str) -> str:
    """Compress JSON by keeping representative samples from large arrays.

    Large arrays are rarely at the top level in practice — API responses look
    like ``{"items": [...]}`` or ``{"data": {"results": [...]}}``. Sampling only
    the root meant the common shape compressed by 0%, so this walks the whole
    structure and samples every oversized array it finds.

    Long string values are truncated too. Array sampling alone left real
    payloads untouched: on a live corpus 39% of the string mass sat in values
    over 200 characters — a pull request body, a review comment — inside
    objects with no oversized array anywhere.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text

    # ensure_ascii would re-encode every non-ASCII character as a six-byte
    # \uXXXX escape. On a payload with a handful of accented characters that
    # alone made the "compressed" output 42 tokens *larger* than the input.
    out = json.dumps(_sample_nested(data, 0), separators=(',', ':'),
                     ensure_ascii=False)
    # Never hand back something longer than what arrived.
    return out if len(out) < len(text) else text


def _sample_nested(node, depth: int, key: str | None = None):
    """Recursively sample oversized arrays anywhere in the structure."""
    if depth > _MAX_DEPTH:
        return node
    if isinstance(node, list):
        sampled = [_sample_nested(item, depth + 1, key) for item in node]
        return _sample_array(sampled)
    if isinstance(node, dict):
        return {k: _sample_nested(v, depth + 1, k) for k, v in node.items()}
    if isinstance(node, str):
        return _shorten(node, key)
    return node


def _shorten(value: str, key: str | None) -> str:
    """Truncate a long string value, keeping both ends and saying so."""
    if len(value) <= _LONG_VALUE_CHARS:
        return value
    if key is not None and key.lower() in _VERBATIM_KEYS:
        return value
    dropped = len(value) - _VALUE_HEAD - _VALUE_TAIL
    return (f"{value[:_VALUE_HEAD]}"
            f"…[memor: {dropped} chars omitted]…"
            f"{value[-_VALUE_TAIL:]}")


def _sample_array(items: list) -> list:
    """Keep head, tail, and anything error-shaped; note what was dropped."""
    if len(items) <= _SAMPLE_THRESHOLD:
        return items

    kept = list(items[:3])
    for item in items[3:-2]:
        try:
            blob = json.dumps(item)
        except (TypeError, ValueError):
            continue
        if _ERROR_PATTERN.search(blob):
            kept.append(item)
    kept.extend(items[-2:])

    # Keep the payload parseable JSON: trail a metadata object, never a comment.
    kept.append({"_memor_note": f"kept {len(kept)} of {len(items)} items"})
    return kept
