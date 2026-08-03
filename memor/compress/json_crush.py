from __future__ import annotations
import json
import re

#: Arrays at or below this length are kept whole.
_SAMPLE_THRESHOLD = 10
#: Guard against pathological nesting.
_MAX_DEPTH = 12

# Items mentioning failure are kept regardless of position — they are the reason
# anyone reads the payload.
_ERROR_PATTERN = re.compile(r'(error|fail|exception|fatal|critical)', re.IGNORECASE)


def compress_json(text: str) -> str:
    """Compress JSON by keeping representative samples from large arrays.

    Large arrays are rarely at the top level in practice — API responses look
    like ``{"items": [...]}`` or ``{"data": {"results": [...]}}``. Sampling only
    the root meant the common shape compressed by 0%, so this walks the whole
    structure and samples every oversized array it finds.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text

    return json.dumps(_sample_nested(data, 0), separators=(',', ':'))


def _sample_nested(node, depth: int):
    """Recursively sample oversized arrays anywhere in the structure."""
    if depth > _MAX_DEPTH:
        return node
    if isinstance(node, list):
        sampled = [_sample_nested(item, depth + 1) for item in node]
        return _sample_array(sampled)
    if isinstance(node, dict):
        return {k: _sample_nested(v, depth + 1) for k, v in node.items()}
    return node


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
