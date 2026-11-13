"""Turn a real bug-fix commit in this repository into a gradable task.

A task needs a state where some test fails for a reason the agent can fix, and
a way to decide afterwards whether it did. A commit that fixed a bug and added
the test for it already contains both: check it out, put the source back to the
parent commit, and the new test is present and failing while the rest of the
suite still passes. That is the SWE-bench construction, applied to this
repository's own history rather than a public dataset, because running a
downloaded project's test suite is a different kind of risk.

The commit's own subject and body become the task prompt, so the agent is told
what a reporter would have said and never which lines to change.

Run on demand; writes task JSON for ``memor/eval/agent_outcome.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
#: Where worktrees are built. Outside the repository: a worktree inside it
#: would be collected by its own test suite.
WORK = Path(os.environ.get("MEMOR_EVAL_WORK", "/tmp/memor-eval")) / "mine"


def _run(cmd: str, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True,
                          text=True, timeout=timeout, env=env)


def worktree(commit: str, name: str) -> Path:
    dest = WORK / name
    if dest.exists():
        _run(f"git worktree remove --force {dest}", REPO)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _run(f"git worktree add --detach --force {dest} {commit}", REPO)
    if r.returncode:
        raise SystemExit(f"worktree failed for {commit}: {r.stderr[:300]}")
    return dest


def outcomes(path: Path, test_files: list[str]) -> dict[str, str]:
    """nodeid -> outcome for one pytest run, or ``{"__error__": ...}``.

    Collection errors matter as much as failures: a test file that imports a
    symbol the parent commit does not have cannot be scored test by test, and
    the task is not usable.
    """
    report = path / ".report.json"
    cmd = (f"{sys.executable} -m pytest {' '.join(test_files)} -q -o addopts='' "
           f"-p no:warnings --tb=no -rN "
           f"--json-report --json-report-file={report}")
    r = _run(cmd, path)
    if not report.exists():
        return {"__error__": (r.stdout[-1500:] + r.stderr[-500:]).strip()}
    data = json.loads(report.read_text())
    report.unlink()
    if data.get("collectors"):
        bad = [c for c in data["collectors"] if c.get("outcome") == "failed"]
        if bad:
            return {"__error__": "collection failed: "
                    + str(bad[0].get("longrepr", ""))[:400]}
    return {t["nodeid"]: t["outcome"] for t in data.get("tests", [])}


def mine(commit: str) -> dict | None:
    touched = _run(f"git show --name-only --format= {commit}", REPO).stdout.split()
    test_files = sorted(f for f in touched
                        if f.startswith("tests/") and f.endswith(".py"))
    src_files = sorted(f for f in touched
                       if f.startswith("memor/") and f.endswith(".py"))
    if not test_files or not src_files:
        print(f"  {commit}: needs both source and test changes")
        return None

    fixed = worktree(commit, "fixed")
    after = outcomes(fixed, test_files)
    if "__error__" in after:
        print(f"  {commit}: fixed state unusable -- {after['__error__'][:200]}")
        return None

    broken = worktree(commit, "broken")
    if _run(f"git checkout {commit}~1 -- memor", broken).returncode:
        print(f"  {commit}: could not restore the parent's source")
        return None
    before = outcomes(broken, test_files)
    if "__error__" in before:
        print(f"  {commit}: broken state unusable -- {before['__error__'][:200]}")
        return None

    fail_to_pass = sorted(n for n, o in before.items()
                          if o != "passed" and after.get(n) == "passed")
    pass_to_pass = sorted(n for n, o in before.items()
                          if o == "passed" and after.get(n) == "passed")
    subject = _run(f"git log -1 --format=%s {commit}", REPO).stdout.strip()
    body = _run(f"git log -1 --format=%b {commit}", REPO).stdout.strip()
    return {
        "id": f"memor-{commit[:7]}",
        "commit": commit,
        "subject": subject,
        "body": body,
        "test_files": test_files,
        "src_files": src_files,
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out_path = WORK.parent / "candidates.json"
    found = []
    for commit in argv:
        print(f"mining {commit}")
        task = mine(commit)
        if task and task["fail_to_pass"]:
            print(f"  usable: {len(task['fail_to_pass'])} fail->pass, "
                  f"{len(task['pass_to_pass'])} stay passing  |  {task['subject'][:60]}")
            found.append(task)
        elif task:
            print(f"  skip: reverting the source broke nothing -- {task['subject'][:50]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(found, indent=2))
    print(f"\n{len(found)} usable tasks -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
