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

#: Filenames that announce they hold credentials. Redaction is pattern-based
#: and every pattern it knows is a *structured* secret -- an sk- key, a JWT, a
#: PEM block. A page of 2FA recovery codes is bare digits and sails straight
#: through: `redact_text` applied 0 redactions to a real backup-codes file on
#: this machine. No regex can fix that without shredding every note containing
#: numbers, so these are refused at the door instead.
#:
#: Matched against the lowercased filename, because the risk is the file's
#: purpose rather than any line inside it.
SECRET_NAME_HINTS = (
    "backup-code", "backup_code", "backupcode", "recovery-code",
    "recovery_code", "recoverycode", "2fa", "mfa", "totp",
    "password", "passwd", "credential", "secret", ".env",
    "private-key", "private_key", "id_rsa", "keystore",
)

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


def _nested_repo_roots(root: Path) -> set[Path]:
    """Git repositories checked out *below* a watched folder.

    Watching a directory that happens to sit above a pile of clones would drag
    every one of their docs/ trees into the store. ``~/Documents/Eukarya`` on
    this machine holds 3 loose notes above 14 cloned repos: recommending it
    would have ingested 3,194 files instead of 3, which is the "duplicate the
    repo into memory" failure the manual-only design was right to fear.

    A repo the user is working in already reaches memory through its
    transcripts, and its docs are files the agent can open. So a nested repo is
    a boundary: notes above it are watched, everything inside it is not.

    The watched root itself is exempt -- pointing at a repo's own docs/ is an
    explicit choice, and refusing it would make ``docs watch`` silently do
    nothing.
    """
    roots: set[Path] = set()
    try:
        for git in root.rglob(".git"):
            repo = git.parent
            if repo != root:
                roots.add(repo)
    except (OSError, PermissionError):
        pass
    return roots


def scan_document_files(root: Path) -> list[DocumentFile]:
    """Every ingestible document under ``root``, recursively."""
    out: list[DocumentFile] = []
    if not root.is_dir():
        return out
    nested = _nested_repo_roots(root)
    for path in sorted(root.rglob("*")):
        if any(r in path.parents for r in nested):
            continue
        if path.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if looks_like_secret_file(path):
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


def looks_like_secret_file(path: Path) -> bool:
    """True when a filename says the contents are credentials.

    Refusing by name is cruder than reading the file, and that is the point:
    the failure being prevented is a secret that redaction cannot recognise, so
    the decision has to be made before the bytes are parsed.
    """
    name = path.name.lower()
    return any(hint in name for hint in SECRET_NAME_HINTS)


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
