"""Run an agent at a real task, with and without memor, and score the work.

Every other harness in this package scores retrieval or compression in
isolation: how much smaller a payload got, how often the right memory came
back. None of them runs an agent at a task and checks whether it succeeded, so
memor can say what it saves and not whether the saving costs the user anything.

This one does. For each task it builds a worktree of this repository at a
commit whose source has been put back to before a bug was fixed, hands a fresh
headless agent the symptom, and afterwards restores the commit's test files and
runs them. The arms differ in exactly one thing: whether the compression hook
is registered in the settings file the run is given. ``--setting-sources ''``
detaches the run from the developer's own ``~/.claude``, so nothing else can
leak in.

Two properties make the comparison mean anything, and both are checked rather
than assumed:

* **The agent's edits decide the outcome.** Tests run from the worktree, whose
  ``memor`` package shadows the installed one.
* **The hook is not the code under test.** It runs from this checkout, so a
  task that breaks ``memor`` cannot disable the thing being measured. The
  failure that would otherwise hide here is the one this project already
  lived through: a hook that silently did nothing for seven weeks, which would
  read as a flawless null result.

Billed cost comes from the CLI's own ``total_cost_usd``. Tokens are recorded
too but are not the headline: a token served from the provider's cache costs
about a tenth of a fresh one, so a token count flatters any compressor.

Run on demand. It spends real money.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = Path(__file__).resolve().parent / "tasks"
WORK = Path(os.environ.get("MEMOR_EVAL_WORK", "/tmp/memor-eval"))

#: Hook binary the treatment arm registers. Resolved from PATH so the arm
#: file names the same command a user's install would.
HOOK_BIN = "memor-posttool-compress"

#: A run that has not finished by here is not going to produce a usable data
#: point, and an agent in a loop is the most expensive way to learn nothing.
DEFAULT_MAX_TURNS = 40
DEFAULT_MODEL = "claude-sonnet-5-5"

#: What the task agent may do, named rather than waived. A headless run needs
#: its permissions settled up front, and the obvious way to do that -- waiving
#: them entirely -- would hand an unattended agent a shell it does not need.
#: These cover reading the repository, editing its source, and running its
#: tests, which is the whole job. Both arms get exactly this list, so the
#: permission surface is not part of what is being compared.
ALLOWED_TOOLS = [
    "Read", "Edit", "MultiEdit", "Write", "Grep", "Glob", "TodoWrite",
    "Bash(python -m pytest:*)",
    "Bash(python:*)",
    "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)",
    "Bash(grep:*)", "Bash(rg:*)", "Bash(find:*)", "Bash(wc:*)",
]


def _run(cmd, cwd: Path, timeout: int = 3600, env: dict | None = None):
    full_env = dict(os.environ)
    full_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                          capture_output=True, text=True, timeout=timeout,
                          env=full_env)


# --------------------------------------------------------------------------
# arms


def arm_settings(arm: str, path: Path) -> Path:
    """Write the settings file that is the entire difference between arms."""
    settings: dict = {}
    if arm == "memor":
        sys.path.insert(0, str(REPO))
        from memor.cli import _ci_pattern
        from memor.posttool_compress import (
            COMPRESSIBLE_TOOLS, LOSSLESS_TOOL_NAMES, MCP_TOOL_PATTERN,
        )

        hook = shutil.which(HOOK_BIN)
        if not hook:
            raise SystemExit(
                f"{HOOK_BIN} is not on PATH; install this checkout first "
                f"(pip install -e .) so the treatment arm has a hook to run.")
        settings = {"hooks": {"PostToolUse": [{
            "matcher": _ci_pattern(COMPRESSIBLE_TOOLS | LOSSLESS_TOOL_NAMES,
                                   extra=(MCP_TOOL_PATTERN,)),
            "hooks": [{"type": "command", "command": hook, "timeout": 10}],
        }]}}
    path.write_text(json.dumps(settings, indent=2))
    return path


# --------------------------------------------------------------------------
# one run


@dataclass
class RunResult:
    task: str
    arm: str
    rep: int
    ok: bool = False
    #: Did every fail_to_pass test pass, with no pass_to_pass test regressing?
    resolved: bool = False
    f2p_passed: int = 0
    f2p_total: int = 0
    p2p_passed: int = 0
    p2p_total: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None
    wall_seconds: float | None = None
    session_id: str | None = None
    usage: dict = field(default_factory=dict)
    #: What the hook did during this run, read back from memor's own ledger.
    hook_firings: int = 0
    hook_tokens_before: int = 0
    hook_tokens_after: int = 0
    error: str | None = None


def build_worktree(task: dict, dest: Path) -> None:
    """A checkout of the fix commit whose source is back at its parent."""
    if dest.exists():
        _run(f"git worktree remove --force {dest}", REPO)
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _run(f"git worktree add --detach --force {dest} {task['commit']}", REPO)
    if r.returncode:
        raise RuntimeError(f"worktree: {r.stderr[:300]}")
    r = _run(f"git checkout {task['commit']}~1 -- memor", dest)
    if r.returncode:
        raise RuntimeError(f"revert: {r.stderr[:300]}")


def ledger_rows_for(session_id: str) -> tuple[int, int, int]:
    """(firings, tokens_before, tokens_after) the hook recorded for this run.

    Read from the user's own ledger, which is where the hook writes. Keyed on
    the session id so a concurrent run cannot be counted here.
    """
    import sqlite3

    db = Path.home() / ".memor" / "memor.db"
    if not db.exists() or not session_id:
        return (0, 0, 0)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_before),0), "
            "COALESCE(SUM(tokens_after),0) FROM proxy_savings "
            "WHERE provider='hook' AND session_id=?", (session_id,)).fetchone()
        con.close()
        return (int(row[0]), int(row[1]), int(row[2]))
    except sqlite3.Error:
        return (0, 0, 0)


def grade(task: dict, work: Path) -> tuple[int, int, int, int]:
    """Restore the commit's tests, run them, and count what passes.

    The tests are restored first, so an agent that edited or deleted them
    gains nothing. Scoring a suite the agent could rewrite would measure its
    willingness to delete a test.
    """
    _run(f"git checkout {task['commit']} -- tests", work)
    report = work / ".grade.json"
    files = " ".join(task["test_files"])
    cmd = (f"{sys.executable} -m pytest {files} -q -o addopts='' -p no:warnings "
           f"--tb=no -rN --json-report --json-report-file={report}")
    _run(cmd, work)
    if not report.exists():
        return (0, len(task["fail_to_pass"]), 0, len(task["pass_to_pass"]))
    data = json.loads(report.read_text())
    outcomes = {t["nodeid"]: t["outcome"] for t in data.get("tests", [])}
    f2p = sum(1 for n in task["fail_to_pass"] if outcomes.get(n) == "passed")
    p2p = sum(1 for n in task["pass_to_pass"] if outcomes.get(n) == "passed")
    return (f2p, len(task["fail_to_pass"]), p2p, len(task["pass_to_pass"]))


def run_one(task: dict, arm: str, rep: int, *, model: str,
            max_turns: int, timeout: int) -> RunResult:
    out = RunResult(task=task["id"], arm=arm, rep=rep)
    work = WORK / "runs" / f"{task['id']}-{arm}-{rep}"
    try:
        build_worktree(task, work)
    except RuntimeError as exc:
        out.error = str(exc)
        return out

    settings = arm_settings(arm, WORK / "arms" / f"{arm}.json")
    cmd = [
        "claude", "-p", task["prompt"],
        "--output-format", "json",
        "--model", model,
        "--max-turns", str(max_turns),
        "--permission-mode", "acceptEdits",
        "--allowed-tools", *ALLOWED_TOOLS,
        "--setting-sources", "",
        "--settings", str(settings),
    ]
    started = time.monotonic()
    try:
        proc = _run(cmd, work, timeout=timeout)
    except subprocess.TimeoutExpired:
        out.error = f"agent exceeded {timeout}s"
        return out
    out.wall_seconds = round(time.monotonic() - started, 1)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out.error = f"unparseable CLI output: {proc.stdout[:200]} {proc.stderr[:200]}"
        return out
    out.cost_usd = payload.get("total_cost_usd")
    out.num_turns = payload.get("num_turns")
    out.session_id = payload.get("session_id")
    out.usage = payload.get("usage") or {}
    out.ok = True

    out.f2p_passed, out.f2p_total, out.p2p_passed, out.p2p_total = grade(task, work)
    out.resolved = (out.f2p_passed == out.f2p_total
                    and out.p2p_passed == out.p2p_total
                    and out.f2p_total > 0)
    out.hook_firings, out.hook_tokens_before, out.hook_tokens_after = (
        ledger_rows_for(out.session_id or ""))
    return out


# --------------------------------------------------------------------------
# campaign


def load_tasks(ids: list[str] | None = None) -> list[dict]:
    tasks = [json.loads(p.read_text()) for p in sorted(TASKS_DIR.glob("*.json"))]
    if ids:
        wanted = set(ids)
        tasks = [t for t in tasks if t["id"] in wanted]
    return tasks


def check_arms(results: list[RunResult]) -> list[str]:
    """Complaints about the arms themselves, not about the agent.

    A treatment run where the hook never fired, or a control run where it did,
    is not a data point about compression; it is a broken experiment, and it
    has to be loud.
    """
    problems = []
    for r in results:
        if not r.ok:
            continue
        if r.arm == "memor" and r.hook_firings == 0:
            problems.append(f"{r.task} memor rep{r.rep}: hook never fired")
        if r.arm == "bare" and r.hook_firings:
            problems.append(
                f"{r.task} bare rep{r.rep}: hook fired {r.hook_firings}x in control")
    return problems


def summarize(results: list[RunResult]) -> dict:
    by_arm: dict[str, dict] = {}
    for arm in ("bare", "memor"):
        runs = [r for r in results if r.arm == arm and r.ok]
        costs = [r.cost_usd for r in runs if r.cost_usd is not None]
        by_arm[arm] = {
            "runs": len(runs),
            "resolved": sum(1 for r in runs if r.resolved),
            "cost_usd": round(sum(costs), 4),
            "mean_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
            "mean_turns": (round(sum(r.num_turns or 0 for r in runs) / len(runs), 1)
                           if runs else None),
            "mean_wall_seconds": (round(sum(r.wall_seconds or 0 for r in runs) / len(runs), 1)
                                  if runs else None),
            "hook_firings": sum(r.hook_firings for r in runs),
            "hook_tokens_saved": sum(r.hook_tokens_before - r.hook_tokens_after
                                     for r in runs),
        }
    # Paired on the task: the same task in both arms, so a task that is simply
    # expensive does not read as an arm difference.
    paired = []
    for task in sorted({r.task for r in results}):
        for rep in sorted({r.rep for r in results if r.task == task}):
            a = next((r for r in results if r.task == task and r.rep == rep
                      and r.arm == "bare" and r.ok and r.cost_usd is not None), None)
            b = next((r for r in results if r.task == task and r.rep == rep
                      and r.arm == "memor" and r.ok and r.cost_usd is not None), None)
            if a and b:
                paired.append({"task": task, "rep": rep, "bare": a.cost_usd,
                               "memor": b.cost_usd,
                               "delta_pct": round((b.cost_usd - a.cost_usd)
                                                  / a.cost_usd * 100, 1)})
    return {"by_arm": by_arm, "paired_cost": paired,
            "arm_problems": check_arms(results)}


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--arms", nargs="*", default=["bare", "memor"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    tasks = load_tasks(args.tasks)
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 2
    (WORK / "arms").mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else WORK / f"results-{int(time.time())}.jsonl"
    results: list[RunResult] = []

    total = len(tasks) * len(args.arms) * args.reps
    done = 0
    for rep in range(1, args.reps + 1):
        for task in tasks:
            for arm in args.arms:
                done += 1
                print(f"[{done}/{total}] {task['id']} {arm} rep{rep} ... ",
                      end="", flush=True)
                result = run_one(task, arm, rep, model=args.model,
                                 max_turns=args.max_turns, timeout=args.timeout)
                results.append(result)
                with out_path.open("a") as fh:
                    fh.write(json.dumps(asdict(result)) + "\n")
                if result.error:
                    print(f"ERROR {result.error[:80]}")
                else:
                    print(f"{'resolved' if result.resolved else 'unresolved'} "
                          f"${result.cost_usd:.3f} {result.num_turns}t "
                          f"hook={result.hook_firings}")

    summary = summarize(results)
    print("\n" + json.dumps(summary, indent=2))
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(
        {"model": args.model, "reps": args.reps,
         "tasks": [t["id"] for t in tasks], "summary": summary}, indent=2))
    print(f"\nruns  -> {out_path}\nsummary -> {summary_path}")
    for problem in summary["arm_problems"]:
        print(f"ARM PROBLEM: {problem}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
