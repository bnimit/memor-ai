"""Watched document folders, ingested by the daemon rather than by hand.

``ingest-doc`` imported one file once and never looked again, so documents were
the only source that required the user to remember. On the development machine
that produced 33,367 session chunks and zero notes -- the feature existed and
was never used, which is the failure mode a manual step reliably produces.

The objection to automating it was stale duplication: a document copied into
memory can outlive the file it came from. These tests lead with that case,
because it is the one that decides whether watching is safe.
"""
from __future__ import annotations

from pathlib import Path

from memor.embed.fake import FakeEmbedder
from memor.ingest.document_watch import (
    DOCUMENT_SUFFIXES,
    MAX_BYTES,
    content_fingerprint,
    document_state_key,
    parse_document_file,
    scan_document_files,
    stale_chunk_ids,
)
from memor.store.sqlite_store import SqliteStore

_NOTE = """# Deploy runbook

The staging cluster is drained before a release.

# Rollback

Roll back with the previous image tag, never by reverting the commit.
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _ingest(store, embedder, doc):
    arts = parse_document_file(doc)
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))
    return arts


def test_an_edited_note_does_not_leave_its_old_claims_recallable(tmp_path):
    """The whole argument against watching, tested.

    A rewritten section produces a new content-hashed id, so the old chunk is
    simply orphaned rather than overwritten. Left alone it would keep answering
    questions from a draft the user deleted -- a stale duplicate of exactly the
    kind the manual-only design feared, which re-running ingest-doc by hand
    would not have cleaned up either.
    """
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    path = _write(tmp_path / "notes", "runbook.md", _NOTE)

    first = _ingest(store, embedder, scan_document_files(tmp_path / "notes")[0])
    assert len(first) == 2

    path.write_text(_NOTE.replace(
        "never by reverting the commit.", "by reverting the commit."))
    second = _ingest(store, embedder, scan_document_files(tmp_path / "notes")[0])

    dead = stale_chunk_ids(store, path, {a.id for a in second})
    assert len(dead) == 1, "the superseded rollback chunk must be identified"
    store.deactivate(dead[0], superseded_by=second[0].id)

    live = [r["text"] for r in store.db.execute(
        "SELECT text FROM artifacts WHERE kind='note' AND active=1")]
    assert any("by reverting the commit." in t for t in live)
    assert not any("never by reverting" in t for t in live)


def test_an_unchanged_file_reingests_to_the_same_rows(tmp_path):
    """Content-hashed ids are what make re-reading cheap enough to automate."""
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    _write(tmp_path / "notes", "runbook.md", _NOTE)

    doc = scan_document_files(tmp_path / "notes")[0]
    ids_a = {a.id for a in _ingest(store, embedder, doc)}
    ids_b = {a.id for a in _ingest(store, embedder, doc)}

    assert ids_a == ids_b
    assert store.db.execute(
        "SELECT COUNT(*) FROM artifacts WHERE kind='note'").fetchone()[0] == 2
    assert stale_chunk_ids(store, doc.path, ids_b) == []


def test_a_note_is_dated_by_its_file_not_the_epoch(tmp_path):
    """created_at defaulted to 0.0, the worst prior in a recency-weighted store."""
    _write(tmp_path / "notes", "runbook.md", _NOTE)
    doc = scan_document_files(tmp_path / "notes")[0]
    arts = parse_document_file(doc)
    assert all(a.created_at == doc.mtime for a in arts)
    assert all(a.created_at > 0 for a in arts)


def test_source_code_and_dependencies_are_not_documents(tmp_path):
    """Indexing a repo would duplicate files the agent can already open."""
    root = tmp_path / "notes"
    _write(root, "keep.md", _NOTE)
    _write(root, "app.py", "def main():\n    return 1\n")
    _write(root, "node_modules/pkg/README.md", "# vendored\n\nnot mine\n")
    _write(root, ".git/COMMIT_EDITMSG", "wip\n")

    found = {d.path.name for d in scan_document_files(root)}
    assert found == {"keep.md"}


def test_an_oversized_file_is_not_a_note(tmp_path):
    """Past a megabyte it is a log or a dump, not something anyone wrote."""
    root = tmp_path / "notes"
    _write(root, "huge.md", "# big\n\n" + ("x" * (MAX_BYTES + 10)))
    _write(root, "small.md", _NOTE)
    assert {d.path.name for d in scan_document_files(root)} == {"small.md"}


def test_an_empty_file_is_skipped(tmp_path):
    root = tmp_path / "notes"
    _write(root, "empty.md", "")
    assert scan_document_files(root) == []


def test_scanning_a_missing_directory_is_not_an_error(tmp_path):
    """A watched folder can be deleted or live on an unmounted volume."""
    assert scan_document_files(tmp_path / "gone") == []


def test_nested_notes_share_one_project(tmp_path):
    """A vault with subfolders is one project, not one per directory."""
    root = tmp_path / "vault"
    _write(root, "a.md", _NOTE)
    _write(root, "deep/nested/b.md", _NOTE)
    projects = {d.project for d in scan_document_files(root)}
    assert len(projects) == 1


def test_secrets_are_redacted_before_a_note_reaches_the_store(tmp_path):
    """Runbooks are the documents most likely to carry a live credential."""
    secret = "sk-ant-api03-" + "B" * 60
    _write(tmp_path / "notes", "deploy.md",
           f"# Deploy\n\nExport the key {secret} before running the script.\n")
    arts = parse_document_file(scan_document_files(tmp_path / "notes")[0])
    assert arts and all(secret not in a.text for a in arts)


def test_state_key_is_stable_and_namespaced(tmp_path):
    """The daemon keys its ingested-file map on this; a collision skips a file."""
    p = tmp_path / "n.md"
    assert document_state_key(p) == f"doc:{p}"
    assert document_state_key(p).startswith("doc:")


def test_fingerprint_changes_only_with_content(tmp_path):
    p = _write(tmp_path, "n.md", _NOTE)
    before = content_fingerprint(p)
    p.write_text(_NOTE)
    assert content_fingerprint(p) == before
    p.write_text(_NOTE + "\n# More\n\nAnother section.\n")
    assert content_fingerprint(p) != before


def test_watched_documents_reach_the_daemon_registry(tmp_path):
    """A parser nothing calls is not a feature."""
    from memor.ingest.sources import scan_all_sources

    _write(tmp_path / "notes", "runbook.md", _NOTE)
    units = scan_all_sources(claude_projects_dir=None,
                             document_dirs=[tmp_path / "notes"])
    assert [u.agent for u in units] == ["document"]
    assert units[0].parse()


def test_documents_are_off_until_a_folder_is_watched(tmp_path):
    """Nothing is auto-discovered: an empty config ingests no documents."""
    from memor.ingest.sources import scan_all_sources

    assert scan_all_sources(claude_projects_dir=None, document_dirs=[]) == []
    assert scan_all_sources(claude_projects_dir=None, document_dirs=None) == []


def test_markdown_and_plain_text_are_both_accepted(tmp_path):
    root = tmp_path / "notes"
    for name in ("a.md", "b.markdown", "c.txt", "d.rst"):
        _write(root, name, _NOTE)
    assert len(scan_document_files(root)) == len(DOCUMENT_SUFFIXES)
