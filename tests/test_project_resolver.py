from memor.project import resolve_project, decode_claude_dir


def test_resolve_project_with_git_root(tmp_path):
    repo = tmp_path / "my-project"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "src" / "lib"
    sub.mkdir(parents=True)
    assert resolve_project(str(sub)) == "my-project"


def test_resolve_project_at_git_root(tmp_path):
    repo = tmp_path / "foo-bar"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert resolve_project(str(repo)) == "foo-bar"


def test_resolve_project_no_git_fallback(tmp_path):
    d = tmp_path / "some-dir"
    d.mkdir()
    assert resolve_project(str(d)) == "some-dir"


def test_resolve_project_home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_project(str(tmp_path)) == tmp_path.name


def test_decode_claude_dir_simple(tmp_path):
    repo = tmp_path / "plirin"
    repo.mkdir()
    encoded = str(repo).replace("/", "-")
    assert decode_claude_dir(encoded) == str(repo)


def test_decode_claude_dir_nested(tmp_path):
    """Decoding resolves dash ambiguity against the real filesystem.

    So a test that names a path only one machine has will pass there and fail
    everywhere else, which is what happened the first time CI ran this suite.
    """
    repo = tmp_path / "Eukarya" / "reearth-flow"
    repo.mkdir(parents=True)
    encoded = str(repo).replace("/", "-")
    assert decode_claude_dir(encoded) == str(repo)


def test_resolve_from_claude_dir(tmp_path):
    repo = tmp_path / "reearth-flow"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert resolve_project(str(repo)) == "reearth-flow"
