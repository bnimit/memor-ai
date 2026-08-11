"""Claude encodes "Gesture App" and "Gesture-App" to the same directory name.

The decoder only ever tried rejoining tokens with a dash, so
`-Users-nimit-Documents-Projects-Gesture-App` decoded to
`/Users/nimit/Documents/Projects/Gesture/App` and the project became "App".

The consequence was invisible and total: on one real day that project received
131 of 203 prompts, every recall returned no_hits, and the store held zero
artifacts for it -- ingestion had been filing them under a different name the
whole time. The dashboard showed recall failing while nothing was wrong with
recall.

Order matters as much as coverage here: a literal dash is the commoner case, so
"seo-team" must not be captured by a hypothetical "seo team" directory.
"""
import pytest

from memor.project import decode_claude_dir, resolve_project_from_claude_dir


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A filesystem with the ambiguous cases side by side."""
    (tmp_path / "Projects" / "Gesture App").mkdir(parents=True)
    (tmp_path / "Projects" / "App Duplicator").mkdir(parents=True)
    (tmp_path / "Projects" / "seo-team").mkdir(parents=True)
    (tmp_path / "Projects" / "Memorable").mkdir(parents=True)
    return tmp_path


def _encode(path) -> str:
    """Mimic Claude's encoding: every separator becomes a dash."""
    return str(path).replace("/", "-")


class TestSpacesInDirectoryNames:
    def test_a_space_is_resolved_against_the_filesystem(self, tree):
        encoded = _encode(tree / "Projects" / "Gesture App")
        assert decode_claude_dir(encoded) == str(tree / "Projects" / "Gesture App")

    def test_the_project_name_keeps_its_space(self, tree):
        encoded = _encode(tree / "Projects" / "Gesture App")
        assert resolve_project_from_claude_dir(encoded) == "Gesture App"

    def test_a_multi_word_name_resolves(self, tree):
        encoded = _encode(tree / "Projects" / "App Duplicator")
        assert resolve_project_from_claude_dir(encoded) == "App Duplicator"

    def test_a_literal_dash_still_wins_over_a_space(self, tree):
        """Dash is the commoner case and must be tried first."""
        encoded = _encode(tree / "Projects" / "seo-team")
        assert resolve_project_from_claude_dir(encoded) == "seo-team"

    def test_a_plain_name_is_unaffected(self, tree):
        encoded = _encode(tree / "Projects" / "Memorable")
        assert resolve_project_from_claude_dir(encoded) == "Memorable"

    def test_a_path_that_does_not_exist_falls_back(self, tree):
        """No filesystem match must not raise; the naive split is the fallback."""
        got = decode_claude_dir("-nonexistent-path-somewhere")
        assert got.startswith("/")

    def test_a_dash_directory_and_a_space_directory_can_coexist(self, tmp_path):
        """Both spellings present: the dash one must not be shadowed."""
        (tmp_path / "Projects" / "my-app").mkdir(parents=True)
        (tmp_path / "Projects" / "my app").mkdir(parents=True)
        encoded = _encode(tmp_path / "Projects" / "my-app")
        # Dash is tried first, so the dash directory wins the ambiguous encoding.
        assert resolve_project_from_claude_dir(encoded) == "my-app"
