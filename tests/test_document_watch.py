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


def test_a_watched_note_is_actually_recallable(tmp_path):
    """Stored is not the same as retrievable, and only the second one matters.

    Every other test here proves a note reaches the artifacts table. This is
    the acceptance path: a question asked against the project must come back
    with the note's own text, through the same recall() the hook calls.
    """
    from memor.daemon import run_poll_cycle
    from memor.embed.local import LocalEmbedder
    from memor.recall import recall

    _write(tmp_path / "notes", "incident.md",
           "# Incident: payment webhook storm\n\n"
           "The root cause was a retry loop with no ceiling in billing/client.py,\n"
           "which turned a downstream outage into a self-inflicted denial of service.\n")

    db = str(tmp_path / "m.db")
    embedder = LocalEmbedder()
    store = SqliteStore(db, dim=embedder.dim)
    run_poll_cycle({}, store, embedder, projects_dir=tmp_path / "nonexistent",
                   document_dirs=[tmp_path / "notes"])
    store.db.commit()

    project = store.db.execute(
        "SELECT DISTINCT project FROM artifacts WHERE kind='note'").fetchone()[0]
    result = recall("what caused the payment webhook storm",
                    project=project, db_path=db, threshold=0.15)

    assert result.hits_count > 0, "a watched note must be retrievable, not just stored"
    assert "retry loop" in (result.formatted_context or "")


def test_maintenance_never_retires_a_note_for_being_unread(tmp_path):
    """Notes are not distilled memories and must not decay like them.

    get_stale_memories and decay_quality both retire artifacts that have not
    been recalled recently. A runbook nobody asked about for a month is still
    the runbook; deactivating it would delete the user's own writing from the
    store without anyone touching the file.
    """
    import time

    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    _write(tmp_path / "notes", "runbook.md", _NOTE)
    _ingest(store, embedder, scan_document_files(tmp_path / "notes")[0])

    # Backdate well past every staleness threshold in the codebase.
    old = time.time() - 400 * 86400
    store.db.execute("UPDATE artifacts SET created_at=? WHERE kind='note'", (old,))
    store.db.commit()

    assert store.get_stale_memories(days=30) == []
    store.deactivate_stale(days=30)
    store.decay_quality(stale_days=14)

    live = store.db.execute(
        "SELECT COUNT(*) FROM artifacts WHERE kind='note' AND active=1").fetchone()[0]
    assert live == 2, "an unread note is still the user's note"


def test_the_distiller_does_not_treat_notes_as_sessions(tmp_path):
    """distill_new_sessions groups by meta.session_id, which a note lacks.

    Without the kind filter every note in the store would land in one bogus
    "?" session and be distilled into a memory summarising unrelated files.
    """
    from memor.daemon import distill_new_sessions

    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    _write(tmp_path / "notes", "runbook.md", _NOTE)
    _ingest(store, embedder, scan_document_files(tmp_path / "notes")[0])

    distill_new_sessions(store, embedder, None, set())

    memories = store.db.execute(
        "SELECT COUNT(*) FROM artifacts WHERE kind='memory'").fetchone()[0]
    assert memories == 0, "notes must not be distilled as if they were transcripts"


def test_a_credentials_file_is_never_ingested(tmp_path):
    """Redaction cannot save this one, so the file must not be read at all.

    Every pattern in redact.py matches a *structured* secret: an sk- key, a
    JWT, a PEM block. A page of 2FA recovery codes is bare digits. Run against
    a real backup-codes file found on this machine, redact_text applied zero
    redactions -- it would have gone into the store verbatim, and no regex can
    fix that without destroying every note that contains numbers.

    Watching a folder is what makes this urgent: ingest-doc was aimed at one
    file by a human who could see what it was.
    """
    root = tmp_path / "notes"
    _write(root, "Backup-codes-acct.txt",
           "SAVE YOUR BACKUP CODES\n\n1. 6480 4732\t\t6. 3960 6307\n2. 2412 6763\n")
    _write(root, "architecture-review.md", _NOTE)

    found = {d.path.name for d in scan_document_files(root)}
    assert found == {"architecture-review.md"}


def test_secret_filename_matching_is_broad_but_not_greedy(tmp_path):
    """Refusing by name only works if the list covers how people name things."""
    from memor.ingest.document_watch import looks_like_secret_file

    for name in ("backup-codes.txt", "recovery_codes.md", "2fa-setup.md",
                 "my-passwords.txt", ".env", "totp-seeds.txt",
                 "prod-credentials.md", "id_rsa"):
        assert looks_like_secret_file(Path(name)), name

    for name in ("architecture-review.md", "incident-2026-08.md",
                 "onboarding.md", "runbook.md", "decisions.md"):
        assert not looks_like_secret_file(Path(name)), name


def test_a_nested_repo_is_a_boundary(tmp_path):
    """Watching a folder above a pile of clones must not ingest all of them.

    ~/Documents/Eukarya on this machine holds 3 loose notes above 14 cloned
    repos. Without this boundary, watching it ingests 3,194 files instead of 3
    -- the "duplicate the repo into memory" failure the manual-only design was
    right to fear, arriving through the back door.

    A repo the user works in already reaches memory through its transcripts,
    and its docs are files the agent can open directly.
    """
    # Deliberately NOT under a SKIP_DIRS name like vendor/ -- that would pass
    # for the wrong reason and hide a missing boundary.
    root = tmp_path / "notes"
    _write(root, "decisions.md", _NOTE)
    _write(root, "cloned-repo/README.md", "# cloned\n\nnot my note\n")
    _write(root, "cloned-repo/docs/guide.md", "# guide\n\nalso not mine\n")
    (root / "cloned-repo" / ".git").mkdir(parents=True)

    found = {d.path.name for d in scan_document_files(root)}
    assert found == {"decisions.md"}


def test_the_watched_root_may_itself_be_a_repo(tmp_path):
    """Pointing at a repo's own docs/ is a choice, not an accident.

    Excluding the root would make `docs watch` silently ingest nothing, which
    is a worse failure than ingesting what was asked for.
    """
    root = tmp_path / "myrepo"
    _write(root, "notes.md", _NOTE)
    (root / ".git").mkdir(parents=True)
    assert [d.path.name for d in scan_document_files(root)] == ["notes.md"]
