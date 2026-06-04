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


def test_decode_claude_dir_simple():
    assert decode_claude_dir("-Users-nimit-Documents-Projects-plirin") == "/Users/nimit/Documents/Projects/plirin"


def test_decode_claude_dir_nested():
    assert decode_claude_dir("-Users-nimit-Documents-Eukarya-reearth-flow") == "/Users/nimit/Documents/Eukarya/reearth-flow"


def test_resolve_from_claude_dir(tmp_path):
    repo = tmp_path / "reearth-flow"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert resolve_project(str(repo)) == "reearth-flow"
