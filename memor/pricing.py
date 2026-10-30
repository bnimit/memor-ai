"""What a saved token is worth, per model.

The dashboard used to price every saved token at one flat rate the user could
edit ($3 per million by default). Rates differ more than a hundredfold across
the models memor serves, so a flat rate overstates savings on Haiku and
understates them on Fable. Each ledger row now records its model, and this
table prices it.

Two rates bound the value of a token kept out of a prompt:

* **input**: the list price of an uncached input token. This is what the
  token would have cost the first time it was sent, and the figure the
  dashboard reports.
* **cache_read**: what it costs when the provider serves it from the prompt
  cache. A compressed tool result is resent on every later turn of the
  session, and most of those resends are cache reads, so this is the floor.

Neither is an invoice. They are list prices for the first-party API: Bedrock,
Vertex and Foundry can charge differently, and a subscription plan is not
billed per token at all. Prices change; ``model_prices`` in
``~/.memor/config.json`` overrides or extends the table without a release::

    {"model_prices": {"gpt-5.5": {"input": 1.25, "cache_read": 0.125}}}

Models the table does not know are left unpriced rather than guessed at; the
dashboard prices them at the user's flat rate and says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CONFIG_KEY = "model_prices"

#: List prices in USD per million tokens: (input, cache read). First-party
#: Claude API, as of October 2026. Matched by prefix, longest first, so a dated
#: id ("claude-sonnet-4-5-20250929") finds its family and "claude-opus-4"
#: does not swallow "claude-opus-4-5".
_LIST_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 0.25),
    "claude-mythos-5-1": (10.0, 0.25),
    "claude-fable-5": (10.0, 1.0),
    "claude-mythos-5": (10.0, 1.0),
    "claude-opus-5-5": (4.0, 0.20),
    "claude-opus-5": (5.0, 0.50),
    "claude-opus-4-8": (5.0, 0.50),
    "claude-opus-4-7": (5.0, 0.50),
    "claude-opus-4-6": (5.0, 0.50),
    "claude-opus-4-5": (5.0, 0.50),
    "claude-opus-4-1": (15.0, 1.50),
    "claude-opus-4": (15.0, 1.50),
    "claude-sonnet-5-5": (2.0, 0.20),
    "claude-sonnet-5": (2.0, 0.20),
    "claude-sonnet-4-6": (3.0, 0.30),
    "claude-sonnet-4-5": (3.0, 0.30),
    "claude-sonnet-4": (3.0, 0.30),
    # The rate for prompts of 100K tokens or fewer; longer prompts cost 5x.
    "claude-haiku-5-5": (0.10, 0.01),
    "claude-haiku-4-5": (1.0, 0.10),
}

#: Routing prefixes that name where a model is served, not which model it is:
#: "anthropic/claude-..." (gateways), "us.anthropic.claude-..." (Bedrock).
_ROUTE_PREFIX = re.compile(r"^(?:[a-z0-9_-]+/)*(?:(?:us|eu|apac|global)\.)?(?:anthropic\.)?")
#: Trailing qualifiers: "[1m]" context suffixes, Bedrock "-v1:0" versions.
_SUFFIX = re.compile(r"(?:\[[^\]]*\]|-v\d+(?::\d+)?)$")


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens for one model."""

    input_per_m: float
    cache_read_per_m: float
    #: ``list`` for the built-in table, ``config`` for a user override.
    source: str

    def as_dict(self) -> dict:
        return {
            "input_per_m": self.input_per_m,
            "cache_read_per_m": self.cache_read_per_m,
            "source": self.source,
        }


def normalize_model(model) -> str:
    """The bare model id, without routing prefixes or qualifiers."""
    if not isinstance(model, str):
        return ""
    name = model.strip().lower()
    name = _ROUTE_PREFIX.sub("", name, count=1)
    while True:
        stripped = _SUFFIX.sub("", name)
        if stripped == name:
            return name
        name = stripped


def _config_prices() -> dict[str, tuple[float, float]]:
    try:
        from memor.config import load_config

        raw = load_config().get(CONFIG_KEY) or {}
    except Exception:
        return {}
    prices: dict[str, tuple[float, float]] = {}
    if not isinstance(raw, dict):
        return prices
    for prefix, entry in raw.items():
        if not isinstance(prefix, str) or not isinstance(entry, dict):
            continue
        try:
            rate = float(entry["input"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            floor = float(entry.get("cache_read", rate))
        except (TypeError, ValueError):
            floor = rate
        if rate >= 0 and floor >= 0:
            prices[normalize_model(prefix)] = (rate, floor)
    return prices


def _match(name: str, table: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    for prefix in sorted(table, key=len, reverse=True):
        if prefix and (name == prefix or name.startswith(prefix + "-")
                       or name.startswith(prefix + "@")):
            return table[prefix]
    return None


def price_for(model, *, overrides: dict[str, tuple[float, float]] | None = None) -> ModelPrice | None:
    """The price of ``model``, or None when it is not known.

    A user override wins over the built-in table, so a stale list price can be
    corrected without waiting for a release.
    """
    name = normalize_model(model)
    if not name:
        return None
    config = _config_prices() if overrides is None else overrides
    hit = _match(name, config)
    if hit is not None:
        return ModelPrice(hit[0], hit[1], "config")
    hit = _match(name, _LIST_PRICES)
    if hit is not None:
        return ModelPrice(hit[0], hit[1], "list")
    return None


class Pricer:
    """Prices many rows with one config read and a memo per model."""

    def __init__(self) -> None:
        self._overrides = _config_prices()
        self._memo: dict[str, ModelPrice | None] = {}

    def __call__(self, model) -> ModelPrice | None:
        key = model if isinstance(model, str) else ""
        if key not in self._memo:
            self._memo[key] = price_for(key, overrides=self._overrides)
        return self._memo[key]
