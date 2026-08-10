"""LongMemEval retrieval benchmark.

Every eval in this repo before this one routed through an LLM judge. That made
the numbers move for reasons unrelated to retrieval: on the same project with
the same config, the counterfactual win rate went 63.8% -> 8.6% when the harness
was corrected to call production `recall()`. A judge measures the judge as much
as the system.

LongMemEval ships ground truth. Each question names the sessions holding its
evidence (`answer_session_ids`), so retrieval is scoreable by set membership,
with no model in the loop, no API key, and no cost. The number cannot drift
because a prompt was reworded.

Two metrics are reported, because the lenient one flatters:

  any-hit  -- at least one evidence session retrieved. This is the paper's
              session-level recall and what published baselines report.
  all-gold -- every evidence session retrieved. Multi-session and temporal
              questions need all of them to be answerable, so any-hit
              overstates how many questions the agent could actually answer.

Abstention instances are excluded, as the benchmark instructs: they refer to
non-existent events and have no ground-truth location.

Data is not vendored (the S split is 277 MB). Fetch it with:

    mkdir -p ~/.memor/benchmarks && cd ~/.memor/benchmarks
    curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
"""
from __future__ import annotations

import collections
import json
import pathlib
import random
import statistics
import tempfile
import time
import uuid
from dataclasses import dataclass, field

DEFAULT_DATA = pathlib.Path.home() / ".memor" / "benchmarks" / "longmemeval_s_cleaned.json"


@dataclass
class LongMemEvalResult:
    n: int
    k: int
    any_hit: float
    all_gold: float
    latency_p50_ms: float
    by_type: dict[str, dict] = field(default_factory=dict)


def load_cases(path: pathlib.Path, n: int, seed: int = 11) -> list[dict]:
    """Stratified sample so every memory ability is represented.

    A head slice is not neutral: the file is grouped by type, so `[:30]` scores
    one ability and reports it as an overall number.
    """
    data = json.loads(path.read_text())
    pool = [q for q in data if not q["question_id"].endswith("_abs")]
    by_type: dict[str, list] = collections.defaultdict(list)
    for q in pool:
        by_type[q["question_type"]].append(q)
    rng = random.Random(seed)
    for v in by_type.values():
        rng.shuffle(v)
    cases: list[dict] = []
    i = 0
    while len(cases) < n:
        added = False
        for t in sorted(by_type):
            if i < len(by_type[t]) and len(cases) < n:
                cases.append(by_type[t][i])
                added = True
        if not added:
            break
        i += 1
    return cases


def run_case(case: dict, *, embedder, k: int) -> tuple[bool, bool, float]:
    """Index one question's history into a throwaway store and retrieve."""
    from memor.recall import recall
    from memor.store.sqlite_store import SqliteStore
    from memor.types import Artifact

    db = str(pathlib.Path(tempfile.mkdtemp()) / "lme.db")
    store = SqliteStore(db, dim=embedder.dim)

    arts, texts = [], []
    now = time.time()
    for sid, sess in zip(case["haystack_session_ids"], case["haystack_sessions"]):
        for turn in sess:
            text = f"{turn['role']}: {turn['content']}"
            arts.append(Artifact(
                id=str(uuid.uuid4()), kind="session_chunk", project="lme",
                source="lme", text=text, token_count=len(text) // 4,
                created_at=now, meta={"session_id": sid}))
            texts.append(text)
    store.add_artifacts(arts, embedder.embed(texts))
    id2sid = {a.id: a.meta["session_id"] for a in arts}

    t0 = time.time()
    res = recall(case["question"], "lme", db, embedder=embedder, k=k,
                 threshold=0.0, min_similarity=-1.0, max_tokens=100_000)
    elapsed = (time.time() - t0) * 1000

    got = {id2sid.get(h) for h in res.hit_ids} - {None}
    gold = set(case["answer_session_ids"])
    return bool(got & gold), gold.issubset(got), elapsed


def run(n: int = 36, k: int = 8, data_path: pathlib.Path | None = None,
        embedder=None) -> LongMemEvalResult:
    from memor.embed.local import LocalEmbedder

    path = data_path or DEFAULT_DATA
    if not path.exists():
        raise FileNotFoundError(
            f"LongMemEval data not found at {path}. Download it with:\n"
            "  mkdir -p ~/.memor/benchmarks && cd ~/.memor/benchmarks && curl -LO "
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
            "resolve/main/longmemeval_s_cleaned.json")

    embedder = embedder or LocalEmbedder()
    cases = load_cases(path, n)
    stats: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    lat: list[float] = []

    for case in cases:
        any_hit, all_gold, ms = run_case(case, embedder=embedder, k=k)
        lat.append(ms)
        s = stats[case["question_type"]]
        s[0] += any_hit
        s[1] += all_gold
        s[2] += 1

    tot_any = sum(s[0] for s in stats.values())
    tot_all = sum(s[1] for s in stats.values())
    tot_n = sum(s[2] for s in stats.values()) or 1
    return LongMemEvalResult(
        n=tot_n, k=k,
        any_hit=100 * tot_any / tot_n,
        all_gold=100 * tot_all / tot_n,
        latency_p50_ms=statistics.median(lat) if lat else 0.0,
        by_type={t: {"any_hit": 100 * s[0] / s[2], "all_gold": 100 * s[1] / s[2],
                     "n": s[2]} for t, s in sorted(stats.items())},
    )


def format_result(r: LongMemEvalResult) -> str:
    lines = [
        f"LongMemEval_S, session-level retrieval, k={r.k} turns, n={r.n}",
        f"  any-hit  (>=1 evidence session): {r.any_hit:.1f}%",
        f"  all-gold (every evidence sess) : {r.all_gold:.1f}%",
        f"  retrieval latency p50          : {r.latency_p50_ms:.0f}ms",
        "",
        f"  {'question type':28} {'any-hit':>8} {'all-gold':>9}   n",
    ]
    for t, v in r.by_type.items():
        lines.append(f"  {t:28} {v['any_hit']:7.1f}% {v['all_gold']:8.1f}%  {v['n']:>3}")
    return "\n".join(lines)
