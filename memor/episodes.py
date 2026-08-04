"""Episode-level accounting — what memor's recall actually costs and saves.

memor has always measured itself per *prompt*: "injected 412 tokens". That is
the wrong ledger. Injection costs tokens once; the exploration it prevents costs
far more, because every tool result re-enters the context on every following
step. A memory layer can make each prompt bigger and the session cheaper.

An **episode** is one unit of work: a genuine user prompt, through every
assistant step and tool call it triggers, up to the next genuine user prompt.
That is the smallest span over which "did memory save effort" is answerable.

Two details make this measurable at all:

* Tool *results* are recorded as ``type: "user"`` records, so a naive turn
  parser treats every mid-loop step as a new turn. Only records carrying real
  user text open an episode here.
* memor's recall is marked directly in the transcript by a
  ``hook_additional_context`` attachment containing ``## Recalled Memories``,
  emitted immediately after the prompt. That beats correlating timestamps.

The comparison is observational, not an experiment — see ``CONFOUND_NOTE``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

RECALL_MARKER = "## Recalled Memories"

CONFOUND_NOTE = (
    "Observational, not randomised: recall fires only when it finds a good "
    "match, and it matches on familiar work — which may be cheaper anyway. "
    "Read a positive result as an upper bound on the true effect."
)


@dataclass
class Episode:
    session_id: str = ""
    project: str = ""
    started_at: float = 0.0
    prompt_chars: int = 0
    had_recall: bool = False
    recall_chars: int = 0
    assistant_steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    tool_names: list[str] = field(default_factory=list)

    @property
    def context_tokens(self) -> int:
        """Everything the model had to read across the episode."""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    @property
    def total_tokens(self) -> int:
        return self.context_tokens + self.output_tokens

    @property
    def cost_units(self) -> float:
        """Billed tokens weighted by what each kind actually costs.

        Raw token totals are dominated by cache reads, which are the cheapest
        thing in the request — summing them unweighted makes an episode look
        expensive when it was mostly cache hits. These are Anthropic's relative
        rates (cache read 0.1x input, cache write 1.25x, output 5x), so the
        number tracks spend rather than volume.

        Crucially this counts ``cache_creation`` at full weight, so the cost of
        re-forming the prompt cache after a rewrite shows up here — the one
        thing memor's own ledger cannot see.
        """
        return (
            self.input_tokens
            + self.cache_creation_tokens * 1.25
            + self.cache_read_tokens * 0.10
            + self.output_tokens * 5.0
        )


def _epoch(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def is_user_prompt(rec: dict) -> bool:
    """True only for real human input, not tool results wearing a user label."""
    if rec.get("type") != "user":
        return False
    content = rec.get("message", {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        # A tool_result block anywhere means this is a mid-loop record.
        if "tool_result" in kinds:
            return False
        return "text" in kinds
    return False


def _prompt_text(rec: dict) -> str:
    content = rec.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def memor_recall_chars(rec: dict) -> int:
    """Length of memor's injected context, or 0 if this is not a memor recall."""
    if rec.get("type") != "attachment":
        return 0
    att = rec.get("attachment") or {}
    if att.get("type") != "hook_additional_context":
        return 0
    content = att.get("content")
    text = content if isinstance(content, str) else json.dumps(content)
    if RECALL_MARKER not in text:
        return 0
    return len(text)


def parse_episodes(path: Path, *, project: str = "", session_id: str = "") -> list[Episode]:
    """Split one transcript into episodes."""
    episodes: list[Episode] = []
    current: Episode | None = None
    sid = session_id or path.stem

    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        if is_user_prompt(rec):
            current = Episode(
                session_id=sid,
                project=project,
                started_at=_epoch(rec.get("timestamp")),
                prompt_chars=len(_prompt_text(rec)),
            )
            episodes.append(current)
            continue

        if current is None:
            continue

        chars = memor_recall_chars(rec)
        if chars:
            current.had_recall = True
            current.recall_chars += chars
            continue

        if rec.get("type") != "assistant":
            continue

        msg = rec.get("message", {})
        usage = msg.get("usage") or {}
        if usage:
            current.assistant_steps += 1
            current.input_tokens += usage.get("input_tokens", 0)
            current.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
            current.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
            current.output_tokens += usage.get("output_tokens", 0)

        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    current.tool_calls += 1
                    current.tool_names.append(block.get("name", "unknown"))

    return episodes


def scan_episodes(projects_dir: Path | None = None) -> list[Episode]:
    """Parse every Claude Code transcript into episodes."""
    root = projects_dir or (Path.home() / ".claude" / "projects")
    if not root.exists():
        return []
    from memor.project import resolve_project_from_claude_dir

    out: list[Episode] = []
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        try:
            project = resolve_project_from_claude_dir(project_dir.name)
        except Exception:
            project = project_dir.name
        for transcript in sorted(project_dir.glob("*.jsonl")):
            try:
                out.extend(parse_episodes(transcript, project=project))
            except Exception:
                continue
    return out


def _stats(episodes: list[Episode]) -> dict:
    if not episodes:
        return {"n": 0}
    return {
        "n": len(episodes),
        "median_tool_calls": median(e.tool_calls for e in episodes),
        "median_total_tokens": median(e.total_tokens for e in episodes),
        "median_steps": median(e.assistant_steps for e in episodes),
        "median_prompt_chars": median(e.prompt_chars for e in episodes),
        "mean_tool_calls": round(sum(e.tool_calls for e in episodes) / len(episodes), 2),
        "mean_total_tokens": round(sum(e.total_tokens for e in episodes) / len(episodes)),
    }


def summarize(episodes: list[Episode], *, min_per_arm: int = 20) -> dict:
    """Compare episodes with recall against episodes without, per project.

    ``min_per_arm`` guards against reporting a difference computed from a
    handful of episodes. Projects below it are counted but not scored.
    """
    usable = [e for e in episodes if e.assistant_steps > 0]
    with_r = [e for e in usable if e.had_recall]
    without = [e for e in usable if not e.had_recall]

    by_project: dict[str, dict] = {}
    for project in sorted({e.project for e in usable}):
        p_with = [e for e in with_r if e.project == project]
        p_without = [e for e in without if e.project == project]
        entry = {
            "with_recall": _stats(p_with),
            "without_recall": _stats(p_without),
            "scored": len(p_with) >= min_per_arm and len(p_without) >= min_per_arm,
        }
        if entry["scored"]:
            entry["tool_call_delta_pct"] = _delta_pct(
                p_without, p_with, lambda e: e.tool_calls
            )
            entry["token_delta_pct"] = _delta_pct(
                p_without, p_with, lambda e: e.total_tokens
            )
        by_project[project] = entry

    overall = {
        "episodes": len(episodes),
        "usable": len(usable),
        "with_recall": _stats(with_r),
        "without_recall": _stats(without),
        "scored": len(with_r) >= min_per_arm and len(without) >= min_per_arm,
        "median_injected_chars": (
            median(e.recall_chars for e in with_r) if with_r else 0
        ),
        "min_per_arm": min_per_arm,
    }
    if overall["scored"]:
        overall["tool_call_delta_pct"] = _delta_pct_mean(
            without, with_r, lambda e: e.tool_calls
        )
        overall["token_delta_pct"] = _delta_pct(without, with_r, lambda e: e.total_tokens)

    strata = stratified_deltas(with_r, without)
    overall["verdict"] = verdict(overall, strata)
    return {
        "overall": overall,
        "by_project": by_project,
        "strata": strata,
        "confound": CONFOUND_NOTE,
    }


#: Episode complexity bands. A 30-tool episode costs more than a 1-tool episode
#: for reasons unrelated to compression, so before/after is compared within
#: bands rather than in aggregate.
_COMPLEXITY_BANDS = ((0, 1), (1, 4), (4, 12), (12, 10**6))


def compare_at(episodes: list[Episode], boundary: float, *, min_per_cell: int = 15) -> dict:
    """Compare per-episode cost before and after a change, from billed tokens.

    ``boundary`` is a unix timestamp — when the change was switched on. This
    reads the provider's own usage numbers rather than memor's ledger, so it
    measures spend rather than what the compressor believed it saved, and it
    includes cache re-formation cost.

    Compared within complexity bands: if the week after a change happens to
    contain harder work, an aggregate would attribute that to the change.
    """
    before = [e for e in episodes if e.assistant_steps > 0 and e.started_at < boundary]
    after = [e for e in episodes if e.assistant_steps > 0 and e.started_at >= boundary]

    bands = []
    for lo, hi in _COMPLEXITY_BANDS:
        b = [e for e in before if lo <= e.tool_calls < hi]
        a = [e for e in after if lo <= e.tool_calls < hi]
        cell = {
            "tool_calls": f"{lo}-{'+' if hi > 10**5 else hi}",
            "n_before": len(b),
            "n_after": len(a),
            "scored": len(b) >= min_per_cell and len(a) >= min_per_cell,
            "cost_delta_pct": None,
            "median_cost_before": median(e.cost_units for e in b) if b else 0,
            "median_cost_after": median(e.cost_units for e in a) if a else 0,
        }
        if cell["scored"]:
            base = cell["median_cost_before"]
            if base:
                cell["cost_delta_pct"] = round(
                    (base - cell["median_cost_after"]) / base * 100, 1
                )
        bands.append(cell)

    scored = [c for c in bands if c["scored"] and c["cost_delta_pct"] is not None]
    overall = None
    if before and after:
        mb = median(e.cost_units for e in before)
        if mb:
            overall = round((mb - median(e.cost_units for e in after)) / mb * 100, 1)

    if len(scored) < 2:
        verdict = "insufficient_data"
    elif len({c["cost_delta_pct"] > 0 for c in scored}) > 1:
        verdict = "no_effect"
    elif overall is None or abs(overall) < EFFECT_THRESHOLD_PCT:
        verdict = "no_effect"
    else:
        verdict = "cheaper" if overall > 0 else "dearer"

    return {
        "boundary": boundary,
        "n_before": len(before),
        "n_after": len(after),
        "cost_delta_pct": overall,
        "bands": bands,
        "verdict": verdict,
        "confound": (
            "Observational: traffic differs week to week. Compared within "
            "complexity bands, but only a flag that flips daily would control "
            "for drift properly."
        ),
    }


COST_VERDICT_TEXT = {
    "insufficient_data": "not enough episodes on both sides yet",
    "no_effect": "no measurable change in cost",
    "cheaper": "episodes cost less after the change",
    "dearer": "episodes cost MORE after the change",
}


def format_comparison(result: dict) -> list[str]:
    lines = ["memor — cost per episode, before vs after", "=" * 58]
    lines.append(f"episodes before={result['n_before']:,}  after={result['n_after']:,}")
    lines.append("")
    lines.append(f"{'tool calls':<14}{'n before':>10}{'n after':>9}{'cost delta':>13}")
    for c in result["bands"]:
        delta = (
            f"{c['cost_delta_pct']:+.1f}%" if c["cost_delta_pct"] is not None
            else "(too few)"
        )
        lines.append(
            f"  {c['tool_calls']:<12}{c['n_before']:>10,}{c['n_after']:>9,}{delta:>13}"
        )
    lines.append("")
    lines.append("=" * 58)
    lines.append(f"VERDICT: {COST_VERDICT_TEXT[result['verdict']]}")
    if result["cost_delta_pct"] is not None:
        lines.append(
            f"  aggregate: {result['cost_delta_pct']:+.1f}% "
            "(positive = cheaper after)"
        )
    if result["verdict"] == "no_effect":
        lines.append("  Direction is not consistent across complexity bands.")
    lines.append(f"  {result['confound']}")
    lines.append(
        "  Cost-weighted from the provider's own usage numbers, so cache"
    )
    lines.append("  re-formation is included rather than invisible.")
    return lines


VERDICT_TEXT = {
    "insufficient_data": "not enough episodes yet to say",
    "no_effect": "no measurable effect",
    "saves": "recall reduces work",
    "costs": "recall increases work",
}


def format_report(summary: dict) -> list[str]:
    o = summary["overall"]
    lines = ["memor recall — episode-level accounting", "=" * 58]
    lines.append(
        f"episodes={o['episodes']:,}  usable={o['usable']:,}  "
        f"with_recall={o['with_recall'].get('n', 0):,}  "
        f"without={o['without_recall'].get('n', 0):,}"
    )
    if not o["scored"]:
        lines.append("")
        lines.append(f"VERDICT: {VERDICT_TEXT['insufficient_data']} "
                     f"(need {o['min_per_arm']} per arm)")
        return lines

    a, b = o["with_recall"], o["without_recall"]
    lines.append("")
    lines.append(f"{'':<16}{'n':>7}{'tools(med)':>12}{'tools(mean)':>13}{'total tok':>12}")
    for label, d in (("with recall", a), ("without recall", b)):
        lines.append(
            f"{label:<16}{d['n']:>7}{d['median_tool_calls']:>12}"
            f"{d['mean_tool_calls']:>13}{d['median_total_tokens']:>12,}"
        )
    lines.append("")
    lines.append(f"median context injected per recall: ~{o['median_injected_chars']:,} chars")
    lines.append("")
    lines.append("Tool-call delta by prompt length (positive = recall did less work):")
    for cell in summary["strata"]:
        if not cell["scored"]:
            lines.append(
                f"  {cell['prompt_chars']:<10} n={cell['n_with']}/{cell['n_without']}"
                "   (too few to score)"
            )
            continue
        lines.append(
            f"  {cell['prompt_chars']:<10} n={cell['n_with']}/{cell['n_without']}"
            f"   {cell['tool_call_delta_pct']:+.1f}%"
        )
    lines.append("")
    lines.append("=" * 58)
    lines.append(f"VERDICT: {VERDICT_TEXT[o['verdict']]}")
    if o["verdict"] == "no_effect":
        lines.append(
            "  The aggregate difference does not survive stratification — the sign"
        )
        lines.append(
            "  flips across prompt-length bands, so it reflects which prompts get"
        )
        lines.append("  recall rather than what recall does.")
    lines.append(f"  {summary['confound']}")
    return lines


#: Prompt-length strata. Recall fires more on longer prompts, and longer prompts
#: often carry pasted context that changes how much exploration is needed — so
#: an aggregate difference can be entirely composition.
_PROMPT_STRATA = ((0, 60), (60, 150), (150, 400), (400, 10**9))

#: A median delta smaller than this is not distinguishable from noise here.
EFFECT_THRESHOLD_PCT = 10.0


def stratified_deltas(
    with_recall: list[Episode], without: list[Episode], *, min_per_cell: int = 20
) -> list[dict]:
    """Tool-call delta within each prompt-length band."""
    out: list[dict] = []
    for lo, hi in _PROMPT_STRATA:
        a = [e for e in with_recall if lo <= e.prompt_chars < hi]
        b = [e for e in without if lo <= e.prompt_chars < hi]
        cell = {
            "prompt_chars": f"{lo}-{'+' if hi >= 10**9 else hi}",
            "n_with": len(a),
            "n_without": len(b),
            "scored": len(a) >= min_per_cell and len(b) >= min_per_cell,
            "tool_call_delta_pct": None,
        }
        if cell["scored"]:
            # Mean, not median: tool calls are small integers, so a median
            # shifting by one reads as a 100-200% swing.
            cell["tool_call_delta_pct"] = _delta_pct_mean(b, a, lambda e: e.tool_calls)
        out.append(cell)
    return out


def verdict(overall: dict, strata: list[dict]) -> str:
    """Classify the result conservatively.

    An aggregate difference is only called an effect when it survives
    stratification — if the sign flips across prompt-length bands, the aggregate
    is composition, not causation. Returns one of:
    ``insufficient_data`` | ``no_effect`` | ``saves`` | ``costs``.
    """
    if not overall.get("scored"):
        return "insufficient_data"
    scored = [c for c in strata if c["scored"] and c["tool_call_delta_pct"] is not None]
    if len(scored) < 2:
        return "insufficient_data"

    signs = {c["tool_call_delta_pct"] > 0 for c in scored}
    if len(signs) > 1:
        return "no_effect"  # direction is not stable — aggregate is composition

    delta = overall.get("tool_call_delta_pct")
    if delta is None or abs(delta) < EFFECT_THRESHOLD_PCT:
        return "no_effect"
    return "saves" if delta > 0 else "costs"


def _delta_pct_mean(baseline: list[Episode], treated: list[Episode], key) -> float | None:
    """Percent reduction by mean — for small-integer counts like tool calls."""
    if not baseline or not treated:
        return None
    base = sum(key(e) for e in baseline) / len(baseline)
    treat = sum(key(e) for e in treated) / len(treated)
    if base == 0:
        return None
    return round((base - treat) / base * 100, 1)


def _delta_pct(baseline: list[Episode], treated: list[Episode], key) -> float | None:
    """Percent reduction from baseline to treated, by median. None if undefined."""
    if not baseline or not treated:
        return None
    base = median(key(e) for e in baseline)
    treat = median(key(e) for e in treated)
    if base == 0:
        return None
    return round((base - treat) / base * 100, 1)
