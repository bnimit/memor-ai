"""Randomized recall holdout, so recall's effect can be measured, not inferred.

``memor recall-worth`` compares episodes that got recall with episodes that did
not, and says itself why that is weak: recall fires only when it finds a good
match, and it matches on familiar work, which may be cheaper anyway. Its best
result is an upper bound. The ledger meanwhile counts recall purely as a cost,
the tokens it injects, and never the exploration it prevents.

A holdout removes the selection. Among prompts where recall *found* something
to inject, a random share have it withheld. Both arms are prompts recall
matched, so they differ only in whether the memories reached the model, and the
gap in what the rest of the episode cost is recall's effect, net of what it
injected.

**Randomize per prompt.** Recall acts once per prompt and its effect lands on
the tool calls that follow, so the prompt is the unit. A withheld prompt is
assigned by hashing its session and text, which keeps a prompt the proxy sees
on several requests in one arm.

**Only eligible prompts are assigned.** A recall that found nothing injects
nothing in either arm; counting it would dilute both with identical episodes.

Withheld recalls are written to their own table, never to ``recall_log``. That
table feeds hit rates, the injection ledger, the feedback grader and the
cross-tool matrix, all of which describe memories that reached a model. A
withheld recall did not, and a grader judging it "unused" would be right for a
reason that says nothing about the memory.
"""
from __future__ import annotations

import hashlib
import os

ENV_VAR = "MEMOR_RECALL_HOLDOUT_FRACTION"
CONFIG_KEY = "recall_holdout_fraction"

#: Arms as written to ``recall_experiment``. Rows exist only while the
#: experiment runs, so there is no "experiment off" value to keep apart.
ARM_TREATMENT = "recall"
ARM_CONTROL = "withheld"

#: Salt so a payload and a prompt that hash alike are not assigned alike: the
#: compression holdout uses the same scheme, and the two experiments must stay
#: independent for each to be read on its own.
_SALT = "memor-recall-holdout\x00"


def _clamp(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value != value or value <= 0:  # NaN or non-positive
        return 0.0
    return min(value, 1.0)


def holdout_setting() -> tuple[float, str]:
    """The share of eligible prompts to withhold recall from, and where it came from.

    Read from ``~/.memor/config.json`` (set by ``memor recall-holdout``), which
    every memor process reads whatever started it. The environment variable
    overrides it when set, for flipping one run without editing state, but
    cannot be the only switch: the hook sidecar inherits the agent's
    environment, an app launched from the Dock never sees a shell profile, and
    the launchd/systemd proxy sees neither, so an env-only experiment can run
    on one path and silently not on another.

    Returns ``(fraction, source)`` with source ``env`` | ``config`` | ``off``.
    Off by default: an experiment that runs without the operator knowing is one
    that quietly takes their memory away.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is not None and raw.strip():
        return _clamp(raw.strip()), "env"
    try:
        from memor.config import load_config

        value = _clamp(load_config().get(CONFIG_KEY, 0.0))
    except Exception:
        value = 0.0
    return value, ("config" if value > 0 else "off")


def holdout_fraction() -> float:
    return holdout_setting()[0]


def is_enabled() -> bool:
    return holdout_fraction() > 0


def experiment_key(session_id: str, conversation_key: str, query: str) -> str:
    """The identity a prompt is assigned by.

    The hook knows a session id; the proxy often knows only a conversation key
    derived from the opening message. Either scopes the prompt to its
    conversation, so the same words in two sessions are two draws.
    """
    return f"{session_id or conversation_key}\x00{query.strip()}"


def assign_arm(key: str, *, fraction: float | None = None) -> str:
    """Deterministically assign one prompt to an arm."""
    share = holdout_fraction() if fraction is None else fraction
    if share <= 0:
        return ARM_TREATMENT
    if share >= 1:
        return ARM_CONTROL
    digest = hashlib.sha256((_SALT + key).encode("utf-8", "replace")).digest()
    bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return ARM_CONTROL if bucket < share else ARM_TREATMENT
