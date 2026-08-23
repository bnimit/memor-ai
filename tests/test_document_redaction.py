"""Secrets must be redacted on every ingest path, not most of them.

``redact_text`` runs inside the Claude, jcode, Goose and Kimi parsers, so a key
pasted into a session never reaches the database. ``parse_document`` did not
call it, which meant ``memor ingest-doc`` was the one way to get an unredacted
secret into the store -- and it is the path most likely to be pointed at a
``.env.example``, a runbook, or deployment notes.

The gap was found while auditing SECURITY.md against the code rather than by a
failure, which is the argument for pinning it: nothing downstream would have
noticed, because an unredacted secret looks exactly like a working memory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SECRETS = {
    "openai": "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
    "postgres": "postgres://admin:hunter2@db.internal:5432/prod",
}


@pytest.mark.parametrize("label,secret", sorted(SECRETS.items()))
def test_ingested_documents_are_redacted(tmp_path, label, secret):
    from memor.ingest.documents import parse_document

    doc = tmp_path / "runbook.md"
    doc.write_text(
        f"# Deployment\n\nExport the key before running the migration:\n\n"
        f"    export API_KEY={secret}\n\nThen run `make deploy`.\n"
    )

    artifacts = parse_document(doc, project="acc")
    body = "\n".join(a.text for a in artifacts)

    assert secret not in body, f"{label} secret survived ingest"
    assert "[REDACTED]" in body
    # The surrounding prose is what makes the memory useful; redaction replaces
    # the secret in place rather than dropping the chunk.
    assert "make deploy" in body


def test_redaction_preserves_document_structure(tmp_path):
    """Chunking still splits on headings after redaction."""
    from memor.ingest.documents import parse_document

    doc = tmp_path / "notes.md"
    doc.write_text(
        "# One\n\nplain prose here\n\n"
        f"# Two\n\ntoken {SECRETS['github']} inline\n"
    )

    artifacts = parse_document(doc, project="acc")

    assert len(artifacts) == 2
    assert "plain prose here" in artifacts[0].text
    assert SECRETS["github"] not in artifacts[1].text


def test_a_clean_document_is_unchanged(tmp_path):
    """Redaction must not alter documents that contain no secrets.

    Asserts on content rather than exact bytes: chunking inserts a newline when
    it rejoins a heading with its body, which predates redaction and is not
    what this test is about.
    """
    from memor.ingest.documents import parse_document

    doc = tmp_path / "design.md"
    doc.write_text("# Design\n\nWe chose Postgres for transactional DDL.\n")

    artifacts = parse_document(doc, project="acc")

    assert len(artifacts) == 1
    assert "[REDACTED]" not in artifacts[0].text
    assert "# Design" in artifacts[0].text
    assert "We chose Postgres for transactional DDL." in artifacts[0].text


def test_every_command_writes_to_the_real_store():
    """A command that writes somewhere else is a command nobody's memory sees.

    Ten commands defaulted to a bare ``memor.db``, resolved against the working
    directory, while the daemon and dashboard read ``~/.memor/memor.db``.
    Running one from a repo created a stray database beside the source and
    reported success: the data was ingested, just not where anything reads it.

    Found while documenting ``ingest-doc``, and it explains why the redaction
    gap in that path went unnoticed -- its output was never visible.
    """
    import inspect
    from pathlib import Path

    from memor.cli import app

    expected = str(Path.home() / ".memor" / "memor.db")
    stray = {}
    for command in app.registered_commands:
        params = inspect.signature(command.callback).parameters
        if "db" not in params:
            continue
        default = params["db"].default
        resolved = str(getattr(default, "default", default))
        if resolved != expected:
            stray[command.name] = resolved

    assert not stray, f"commands writing outside the store: {stray}"
