"""Prove-recall campaign: offline thrift A/B + matched ATT snapshot + decision."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from memor.recall_policy import DEFAULT_PROFILE, PROFILE_ENV, STRICT_PROFILE

# G1: strict should use at most this fraction of default tokens (when default hit).
THRIFT_RATIO = 0.85


def memory_share(hit_kinds: list[str]) -> float:
    if not hit_kinds:
        return 0.0
    return sum(1 for k in hit_kinds if k == "memory") / len(hit_kinds)


def offline_thrift_ok(
    *,
    default_mean_tokens: float,
    strict_mean_tokens: float,
    default_memory_share: float,
    strict_memory_share: float,
    n_compared: int,
    min_n: int = 10,
) -> tuple[bool, str]:
    """G1: strict is thriftier and at least as memory-heavy."""
    if n_compared < min_n:
        return False, f"need ≥{min_n} comparable queries, have {n_compared}"
    if default_mean_tokens <= 0:
        return False, "default arm injected no tokens"
    ratio = strict_mean_tokens / default_mean_tokens
    if ratio > THRIFT_RATIO:
        return False, (
            f"strict tokens {strict_mean_tokens:.0f} are "
            f"{ratio:.0%} of default {default_mean_tokens:.0f} (need ≤{THRIFT_RATIO:.0%})"
        )
    if strict_memory_share + 1e-9 < default_memory_share:
        return False, (
            f"strict memory share {strict_memory_share:.2f} "
            f"< default {default_memory_share:.2f}"
        )
    return True, (
        f"strict tokens {ratio:.0%} of default; "
        f"memory share {strict_memory_share:.2f} ≥ {default_memory_share:.2f}"
    )


def gate_g0_meter(att_overall: dict) -> tuple[bool, str]:
    matched = att_overall.get("matched") or {}
    rate = matched.get("match_rate") or att_overall.get("match_rate") or 0
    pairs = matched.get("n_pairs") or 0
    if pairs >= 50 and rate >= 0.60:
        return True, f"pairs={pairs} match_rate={rate:.0%} verdict={att_overall.get('verdict')}"
    return False, f"pairs={pairs} match_rate={rate} (need ≥50 and ≥60%)"


def decide_prove_recall(
    *,
    g0: bool,
    g1: bool,
    g2_due: bool = False,
    g2_verdict: str | None = None,
) -> dict:
    """Return decision code + rationale (design decision tree)."""
    if not g0:
        return {"decision": "fail_meter", "rationale": "G0 matched-ATT meter unhealthy"}
    if not g1:
        return {"decision": "fail_policy", "rationale": "G1 offline thrift/memory-share failed"}
    if not g2_due:
        return {
            "decision": "needs_forward_window",
            "rationale": (
                "G0+G1 passed. Stamp a baseline, run with "
                "MEMOR_RECALL_PROFILE=strict for ≥7 days, then re-run prove-recall."
            ),
        }
    if g2_verdict == "saves":
        return {"decision": "pass_roi", "rationale": "Forward matched ATT is saves"}
    if g2_verdict == "costs":
        return {"decision": "revert_strict", "rationale": "Forward matched ATT is costs"}
    return {
        "decision": "hold",
        "rationale": f"Forward matched ATT is {g2_verdict or 'no_effect'}; do not market ROI",
    }


def _kinds_from_formatted(formatted: str) -> list[str]:
    """Parse ``### 1. [decision]`` / ``### 1. [memory]`` style headers."""
    import re
    return re.findall(r"^###\s+\d+\.\s+\[([^\]]+)\]", formatted or "", flags=re.M)


def run_offline_ab(
    *,
    queries: list[dict],
    recall_fn: Callable[..., Any],
    db_path: str,
    embedder,
    threshold: float = 0.15,
) -> dict:
    """Re-run recall under default vs strict for each {project, query} row."""
    default_tokens: list[int] = []
    strict_tokens: list[int] = []
    default_shares: list[float] = []
    strict_shares: list[float] = []
    compared = 0

    saved_profile = os.environ.get(PROFILE_ENV)
    saved_super = os.environ.get("MEMOR_SUPERSESSION")
    try:
        for row in queries:
            project = row["project"]
            query = row["query"]
            os.environ[PROFILE_ENV] = DEFAULT_PROFILE
            if "MEMOR_SUPERSESSION" in os.environ:
                os.environ.pop("MEMOR_SUPERSESSION", None)
            d = recall_fn(query, project, db_path, embedder=embedder, threshold=threshold)

            os.environ[PROFILE_ENV] = STRICT_PROFILE
            os.environ["MEMOR_SUPERSESSION"] = "1"
            s = recall_fn(query, project, db_path, embedder=embedder, threshold=threshold)

            d_tok = int(getattr(d, "tokens_injected", 0) or 0)
            if d_tok <= 0 and int(getattr(d, "hits_count", 0) or 0) <= 0:
                continue
            compared += 1
            default_tokens.append(d_tok)
            strict_tokens.append(int(getattr(s, "tokens_injected", 0) or 0))
            d_kinds = _kinds_from_formatted(getattr(d, "formatted_context", "") or "")
            s_kinds = _kinds_from_formatted(getattr(s, "formatted_context", "") or "")
            # Headers use mem_type (decision/lesson/...) — count non-chunk as memory-ish.
            def _share(kinds: list[str]) -> float:
                if not kinds:
                    return 0.0
                return sum(
                    1 for k in kinds if k not in ("session_chunk", "snippet", "chunk")
                ) / len(kinds)

            default_shares.append(_share(d_kinds))
            strict_shares.append(_share(s_kinds))
    finally:
        if saved_profile is None:
            os.environ.pop(PROFILE_ENV, None)
        else:
            os.environ[PROFILE_ENV] = saved_profile
        if saved_super is None:
            os.environ.pop("MEMOR_SUPERSESSION", None)
        else:
            os.environ["MEMOR_SUPERSESSION"] = saved_super

    d_mean = sum(default_tokens) / len(default_tokens) if default_tokens else 0.0
    s_mean = sum(strict_tokens) / len(strict_tokens) if strict_tokens else 0.0
    d_share = sum(default_shares) / len(default_shares) if default_shares else 0.0
    s_share = sum(strict_shares) / len(strict_shares) if strict_shares else 0.0

    ok, reason = offline_thrift_ok(
        default_mean_tokens=d_mean,
        strict_mean_tokens=s_mean,
        default_memory_share=d_share,
        strict_memory_share=s_share,
        n_compared=compared,
    )
    return {
        "n_queries": len(queries),
        "n_compared": compared,
        "default_mean_tokens": round(d_mean, 1),
        "strict_mean_tokens": round(s_mean, 1),
        "default_memory_share": round(d_share, 3),
        "strict_memory_share": round(s_share, 3),
        "thrift_ratio": round(s_mean / d_mean, 3) if d_mean else None,
        "g1_pass": ok,
        "g1_reason": reason,
    }


def load_recent_queries(store, *, limit: int = 40, project: str | None = None) -> list[dict]:
    clauses = ["hits_count > 0", "query_preview IS NOT NULL", "length(query_preview) > 5"]
    params: list[Any] = []
    if project:
        clauses.append("project = ?")
        params.append(project)
    where = " AND ".join(clauses)
    rows = store.db.execute(
        f"SELECT project, query_preview AS query FROM recall_log "
        f"WHERE {where} ORDER BY timestamp DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    # Dedupe identical previews
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = (r["project"], r["query"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"project": r["project"], "query": r["query"]})
    return out


def run_prove_recall(
    *,
    db_path: str,
    embedder=None,
    offline_n: int = 40,
    project: str | None = None,
    stamp: bool = False,
    skip_offline: bool = False,
    out_path: Path | None = None,
) -> dict:
    """Execute G0 + G1 and write evidence JSON."""
    from memor.episodes import scan_episodes, summarize
    from memor.recall import recall as recall_fn
    from memor.store.sqlite_store import SqliteStore, read_dim

    if embedder is None:
        from memor.embed.local import LocalEmbedder
        embedder = LocalEmbedder()

    # G0 — matched ATT on Claude episodes
    att = summarize(scan_episodes())
    g0, g0_reason = gate_g0_meter(att["overall"])

    # G1 — offline A/B
    offline: dict
    if skip_offline:
        offline = {"g1_pass": False, "g1_reason": "skipped", "n_compared": 0}
        g1 = False
    else:
        store = SqliteStore(db_path, dim=read_dim(db_path, embedder.dim))
        queries = load_recent_queries(store, limit=offline_n, project=project)
        offline = run_offline_ab(
            queries=queries,
            recall_fn=recall_fn,
            db_path=db_path,
            embedder=embedder,
            threshold=0.15,
        )
        g1 = bool(offline.get("g1_pass"))

    stamped = None
    if stamp:
        from memor.recall_baseline import stamp_baseline
        stamped = stamp_baseline()

    decision = decide_prove_recall(g0=g0, g1=g1, g2_due=False)

    evidence = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "campaign": "prove-recall",
        "claim_scope": att.get("claim_scope") or "claude_code_episodes",
        "g0_meter": {"pass": g0, "reason": g0_reason, "overall": {
            "verdict": att["overall"].get("verdict"),
            "matched_att_pct": att["overall"].get("matched_att_pct"),
            "match_rate": att["overall"].get("match_rate"),
            "n_pairs": (att["overall"].get("matched") or {}).get("n_pairs"),
            "mde_pct": (att["overall"].get("matched") or {}).get("mde_pct"),
        }},
        "g1_offline_ab": offline,
        "g2_forward": {
            "due": False,
            "stamped_at": stamped,
            "instructions": (
                "export MEMOR_RECALL_PROFILE=strict; memor service restart; "
                "after ≥7 days of Claude use, re-run memor prove-recall"
            ),
        },
        "g3_counterfactual": {"status": "not_run"},
        **decision,
        "confound": att.get("confound"),
    }

    if out_path is None:
        out_path = Path("docs/eval") / f"{evidence['date']}-prove-recall.json"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2) + "\n")
    evidence["evidence_path"] = str(out_path)
    return evidence
