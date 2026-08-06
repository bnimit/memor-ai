"""A git worktree belongs to its repository, not to itself.

resolve_project() walks up to the first `.git` and used that directory's name.
In a linked worktree `.git` is a *file* reading "gitdir: <main>/.git/worktrees/
<name>", so a worktree was filed as its own project: work done in
`repo-feature-x` never surfaced while working in `repo`, and sibling worktrees
could not see each other. One repository splintered into as many memory buckets
as it had checkouts.
"""
from __future__ import annotations

import subprocess

import pytest

from memor.project import resolve_project


def _git(*args, cwd):
    return subprocess.run(("git",) + args, cwd=cwd,
                          capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "myrepo"
    r.mkdir()
    _git("init", "-q", cwd=r)
    _git("config", "user.email", "t@example.com", cwd=r)
    _git("config", "user.name", "t", cwd=r)
    (r / "f.txt").write_text("hello")
    _git("add", ".", cwd=r)
    _git("commit", "-qm", "init", cwd=r)
    return r


def test_worktree_resolves_to_the_repository(repo, tmp_path):
    wt = tmp_path / "myrepo-feature-x"
    res = _git("worktree", "add", "-q", str(wt), "-b", "feature-x", cwd=repo)
    if res.returncode != 0:
        pytest.skip(f"git worktree unavailable: {res.stderr[:80]}")

    assert (wt / ".git").is_file(), "fixture did not create a linked worktree"
    assert resolve_project(str(wt)) == "myrepo"
    assert resolve_project(str(repo)) == "myrepo"


def test_a_subdirectory_of_a_worktree_also_resolves(repo, tmp_path):
    wt = tmp_path / "myrepo-feature-y"
    res = _git("worktree", "add", "-q", str(wt), "-b", "feature-y", cwd=repo)
    if res.returncode != 0:
        pytest.skip("git worktree unavailable")
    sub = wt / "src" / "deep"
    sub.mkdir(parents=True)
    assert resolve_project(str(sub)) == "myrepo"


def test_an_ordinary_repository_is_unchanged(repo):
    assert resolve_project(str(repo)) == "myrepo"


def test_a_plain_directory_still_uses_its_own_name(tmp_path):
    d = tmp_path / "not-a-repo"
    d.mkdir()
    assert resolve_project(str(d)) == "not-a-repo"


def test_a_malformed_git_file_falls_back_rather_than_raising(tmp_path):
    """A wrong-but-stable name beats an exception on the recall path."""
    d = tmp_path / "weird"
    d.mkdir()
    (d / ".git").write_text("this is not a gitdir pointer")
    assert resolve_project(str(d)) == "weird"


def test_a_gitdir_pointer_without_worktrees_falls_back(tmp_path):
    """Submodules also use a .git file, and are not worktrees."""
    d = tmp_path / "submodule"
    d.mkdir()
    (d / ".git").write_text("gitdir: ../.git/modules/submodule")
    assert resolve_project(str(d)) == "submodule"
