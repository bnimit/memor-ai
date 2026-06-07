"""Query complexity scoring and budget routing.

Scores queries by word count, identifier density, path references, and
question structure. Routes to budget tiers so trivial prompts skip recall,
simple follow-ups get a light budget, and complex queries get full retrieval.
"""
from __future__ import annotations

import enum
import re

_IDENTIFIER_RE = re.compile(
    r"[A-Z][a-z]+[A-Z]"           # camelCase/PascalCase
    r"|[a-z]+_[a-z]+"             # snake_case
    r"|[A-Z]{2,}"                 # SCREAMING_CASE (min 2 chars)
    r"|\b\w+\.\w+\.\w+"          # dotted.path.ref
    r"|\w+::\w+"                  # C++ scope
)

_PATH_RE = re.compile(
    r"[a-zA-Z0-9_\-]+/"           # path components with /
    r"|\.\w{1,5}\b"               # file extensions (.py, .ts, .yml)
)

_QUESTION_RE = re.compile(r"\?\s*$")

_ERROR_RE = re.compile(
    r"(Error|Exception|Traceback|FAIL|panic|segfault|undefined|NaN|null|nil)\b",
    re.I,
)

_TRIVIAL_PATTERNS = frozenset({
    "yes", "no", "ok", "okay", "sure", "thanks", "thank you", "ty",
    "looks good", "lgtm", "continue", "go ahead", "do it", "proceed",
    "correct", "right", "yep", "yup", "nope", "agreed", "sounds good",
    "perfect", "great", "nice", "cool", "done", "got it", "k",
    "yes please", "no thanks",
})


class Tier(enum.Enum):
    SKIP = (0, 0)
    LIGHT = (4, 500)
    FULL = (8, 1500)

    def __init__(self, k: int, max_tokens: int):
        self.k = k
        self.max_tokens = max_tokens


def score_query(query: str) -> float:
    """Score query complexity on [0, 1]. Higher = more context needed."""
    text = query.strip()
    if not text:
        return 0.0

    normalized = text.rstrip("?!.,").strip().lower()
    if normalized in _TRIVIAL_PATTERNS:
        return 0.0

    words = text.split()
    n = len(words)

    word_score = min(1.0, n * n / (n * n + 36))

    id_matches = len(_IDENTIFIER_RE.findall(text))
    id_score = min(id_matches / 3.0, 1.0)

    path_matches = len(_PATH_RE.findall(text))
    path_score = min(path_matches / 2.0, 1.0)

    question_score = 0.1 if _QUESTION_RE.search(text) else 0.0
    error_score = 0.15 if _ERROR_RE.search(text) else 0.0

    raw = (
        0.45 * word_score
        + 0.20 * id_score
        + 0.20 * path_score
        + question_score
        + error_score
    )
    return min(raw, 1.0)


def route_query(query: str) -> Tier:
    """Route a query to a retrieval budget tier based on its complexity."""
    s = score_query(query)
    if s < 0.05:
        return Tier.SKIP
    if s < 0.45:
        return Tier.LIGHT
    return Tier.FULL
