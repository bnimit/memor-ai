"""Watch folders of documents, the way the daemon watches session stores.

``memor ingest-doc`` existed but had to be pointed at a file by hand, once,
forever. A memory layer you have to remember to feed is not a memory layer: the
whole premise is that it captures what you would otherwise have to re-explain.
Transcripts got that treatment from the start; notes did not, and on this
machine the result was 33,367 session chunks and **zero** documents.

The argument for keeping it manual was that a design doc in the repo is a live
file the agent can already read, so copying it into memory creates a stale
duplicate of an authoritative source. That is a real risk and it is the reason
this module exists rather than a reason to do nothing:

* **Staleness is answered by re-reading, not by refusing to read.** Chunk ids
  are content-hashed, so an unchanged file re-ingests to the same rows and
  costs nothing. A chunk that no longer appears in the file is deactivated, so
  an edited document cannot leave its old claims behind in recall.
* **Duplication is answered by scope.** Nothing here is auto-discovered. The
  daemon watches exactly the directories listed in ``document_dirs``, which is
  empty by default. Pointing it at a repo's ``docs/`` is possible and usually
  wrong; pointing it at the notes that live *outside* any repo is the case that
  was never served.

Project scope comes from the directory's own git root where it has one, and
from its folder name otherwise, so a vault of notes files under its own name
rather than leaking into whichever repo was open at the time.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from memor.ingest.documents import parse_document
from memor.types import Artifact

#: Extensions treated as documents. Deliberately prose-only: source files are
#: what the agent reads directly and what the compressor's source guard exists
#: to protect, and indexing them here would duplicate the repo into memory.
DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})

#: Directory names never descended into. Ingesting a dependency tree or a build
#: output would swamp the store with text nobody wrote.
SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "target", "vendor", ".tox", "site-packages", ".obsidian",
})

#: Above this a file is not a note. The largest legitimate note on this machine
#: is a few tens of KB; past a megabyte it is a log, a dump, or generated.
MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class DocumentFile:
    path: Path
    project: str
    mtime: float


def _project_for(path: Path, root: Path) -> str:
    """Scope a document to a project.

    A note's git root is the honest owner when it has one: notes kept inside a
    repo belong to that repo. Without one the *watched* directory's name is
    used, never the file's own parent -- ``resolve_project`` falls back to the
    parent folder, which split a vault into one project per subdirectory and
    made ``vault/deep/nested/b.md`` unrecallable alongside ``vault/a.md``.
    """
    from memor.project import _find_git_root, _main_repo_root

    try:
        git_root = _find_git_root(path.parent.resolve())
    except Exception:
        git_root = None
    if git_root:
        try:
            return _main_repo_root(git_root).name
        except Exception:
            pass
    return root.name or "documents"


def scan_document_files(root: Path) -> list[DocumentFile]:
    """Every ingestible document under ``root``, recursively."""
    out: list[DocumentFile] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if not stat.st_size or stat.st_size > MAX_BYTES:
            continue
        out.append(DocumentFile(path=path, project=_project_for(path, root),
                                mtime=stat.st_mtime))
    return out


def document_state_key(path: Path) -> str:
    """State key for the daemon's ingested-file map."""
    return f"doc:{path}"


def parse_document_file(doc: DocumentFile) -> list[Artifact]:
    """Parse one document, stamping it with the file's own mtime.

    ``created_at`` is the file's modification time rather than 0.0 so recency
    ranking treats a note edited today as current. The default of 0.0 put every
    document at the epoch, which is the worst possible prior for a store that
    weights recency.
    """
    return parse_document(doc.path, project=doc.project, kind="note",
                          created_at=doc.mtime)


def stale_chunk_ids(store, path: Path, keep_ids: set[str]) -> list[str]:
    """Chunk ids previously ingested from ``path`` that are no longer in it.

    This is what makes watching safe. Without it an edited document keeps its
    deleted paragraphs alive in recall forever, which is precisely the stale
    duplicate the manual-only design was trying to avoid -- and re-running
    ``ingest-doc`` by hand would not have cleaned them either.
    """
    try:
        rows = store.db.execute(
            "SELECT id FROM artifacts WHERE kind='note' AND source=? AND active=1",
            (str(path),)).fetchall()
    except Exception:
        return []
    return [r["id"] for r in rows if r["id"] not in keep_ids]


def content_fingerprint(path: Path) -> str:
    """Hash of a document's bytes, for change detection that survives touch."""
    h = hashlib.sha1()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()[:16]
