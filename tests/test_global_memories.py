"""Tests for cross-project global memories (#15)."""
import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact, Scope
from memor.recall import recall


def _make_store(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    return s, e, db_path


def _make_art(id, project, text, created=100.0, kind="memory", mem_type="decision"):
    return Artifact(id=id, kind=kind, project=project, source="distill",
                    text=text, token_count=len(text.split()), created_at=created,
                    meta={"mem_type": mem_type, "session_id": "s1"})


GLOBAL_PROJECT = "_global"


# --- Scope.matches includes _global memories ---

def test_scope_matches_global_memories():
    """A scope filtering for project='myproj' should also match _global artifacts."""
    scope = Scope(project="myproj")
    global_art = _make_art("g1", GLOBAL_PROJECT, "always use type hints")
    assert scope.matches(global_art)


def test_scope_matches_same_project():
    """Normal project matching still works."""
    scope = Scope(project="myproj")
    art = _make_art("a1", "myproj", "use argon2")
    assert scope.matches(art)


def test_scope_rejects_other_project():
    """Scope should still reject artifacts from a different non-global project."""
    scope = Scope(project="myproj")
    other_art = _make_art("a1", "otherproj", "use argon2")
    assert not scope.matches(other_art)


def test_scope_none_matches_everything():
    """A scope with project=None matches all projects including _global."""
    scope = Scope(project=None)
    assert scope.matches(_make_art("a1", "myproj", "x"))
    assert scope.matches(_make_art("g1", GLOBAL_PROJECT, "x"))


# --- Store search includes _global results ---

def test_search_includes_global_memories(tmp_path):
    """Dense search for project='p' should also return _global memories."""
    s, e, _ = _make_store(tmp_path)
    project_art = _make_art("a1", "myproj", "use argon2 for password hashing")
    global_art = _make_art("g1", GLOBAL_PROJECT, "always use type hints in python code")
    s.add_artifacts([project_art, global_art], e.embed([project_art.text, global_art.text]))

    q = e.embed(["type hints python"])[0]
    hits = s.search(q, Scope(project="myproj"), k=5)
    ids = [a.id for a, _ in hits]
    assert "g1" in ids


def test_search_lexical_includes_global_memories(tmp_path):
    """Lexical search for project='p' should also return _global memories."""
    s, e, _ = _make_store(tmp_path)
    project_art = _make_art("a1", "myproj", "use argon2 for password hashing")
    global_art = _make_art("g1", GLOBAL_PROJECT, "always use type hints in python code")
    s.add_artifacts([project_art, global_art], e.embed([project_art.text, global_art.text]))

    hits = s.search_lexical("type hints python", Scope(project="myproj"), k=5)
    ids = [a.id for a, _ in hits]
    assert "g1" in ids


def test_search_does_not_include_other_projects(tmp_path):
    """Searching for project='myproj' should NOT return memories from 'otherproj'."""
    s, e, _ = _make_store(tmp_path)
    project_art = _make_art("a1", "myproj", "use argon2 for hashing")
    other_art = _make_art("a2", "otherproj", "use argon2 for hashing")
    s.add_artifacts([project_art, other_art], e.embed([project_art.text, other_art.text]))

    q = e.embed(["argon2 hashing"])[0]
    hits = s.search(q, Scope(project="myproj"), k=5)
    ids = [a.id for a, _ in hits]
    assert "a1" in ids
    assert "a2" not in ids


# --- recall() includes global memories in formatted output ---

def test_recall_includes_global_memories(tmp_path):
    """The recall function should surface _global memories alongside project ones."""
    s, e, db_path = _make_store(tmp_path)
    project_art = _make_art("a1", "myproj", "use argon2 for password hashing in auth module")
    global_art = _make_art("g1", GLOBAL_PROJECT, "always prefer composition over inheritance in python")
    s.add_artifacts([project_art, global_art], e.embed([project_art.text, global_art.text]))

    result = recall("composition over inheritance", "myproj", db_path,
                    embedder=e, k=8, threshold=0.0, min_similarity=-2.0)
    assert result.hits_count > 0
    assert "g1" in (result.hit_ids or [])


# --- Auto-promotion: detect cross-project patterns ---

def test_find_promotion_candidates(tmp_path):
    """Memories appearing in 3+ projects should be identified as promotion candidates."""
    from memor.global_memories import find_promotion_candidates

    s, e, _ = _make_store(tmp_path)
    for i, proj in enumerate(["proj_a", "proj_b", "proj_c", "proj_d"]):
        art = _make_art(f"m{i}", proj, "always use type hints in python code",
                        created=100.0 + i)
        s.add_artifacts([art], e.embed([art.text]))

    candidates = find_promotion_candidates(s, e, min_projects=3, sim_threshold=0.85)
    assert len(candidates) >= 1
    texts = [c["text"] for c in candidates]
    assert any("type hints" in t for t in texts)


def test_find_promotion_candidates_below_threshold(tmp_path):
    """Memories in fewer than min_projects should not be candidates."""
    from memor.global_memories import find_promotion_candidates

    s, e, _ = _make_store(tmp_path)
    for i, proj in enumerate(["proj_a", "proj_b"]):
        art = _make_art(f"m{i}", proj, "always use type hints in python code",
                        created=100.0 + i)
        s.add_artifacts([art], e.embed([art.text]))

    candidates = find_promotion_candidates(s, e, min_projects=3, sim_threshold=0.85)
    assert len(candidates) == 0


def test_promote_to_global(tmp_path):
    """Promoting a pattern to global creates a _global memory and deactivates source dupes."""
    from memor.global_memories import promote_to_global

    s, e, _ = _make_store(tmp_path)
    source_ids = []
    for i, proj in enumerate(["proj_a", "proj_b", "proj_c"]):
        art = _make_art(f"m{i}", proj, "always use type hints in python code",
                        created=100.0 + i)
        s.add_artifacts([art], e.embed([art.text]))
        source_ids.append(art.id)

    global_id = promote_to_global(s, e, "always use type hints in python code", source_ids)
    assert global_id is not None

    global_art = s.db.execute(
        "SELECT * FROM artifacts WHERE id=?", (global_id,)
    ).fetchone()
    assert global_art["project"] == GLOBAL_PROJECT
    assert global_art["active"] == 1

    for sid in source_ids:
        row = s.db.execute("SELECT active FROM artifacts WHERE id=?", (sid,)).fetchone()
        assert row["active"] == 0


def test_promote_skips_if_global_exists(tmp_path):
    """If a similar global memory already exists, promotion should return None."""
    from memor.global_memories import promote_to_global

    s, e, _ = _make_store(tmp_path)
    existing = _make_art("g_existing", GLOBAL_PROJECT, "always use type hints in python code")
    s.add_artifacts([existing], e.embed([existing.text]))

    global_id = promote_to_global(s, e, "always use type hints in python code", ["m1"])
    assert global_id is None
