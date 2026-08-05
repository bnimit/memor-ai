"""Clean the retrieval pool: drop harness noise, collapse duplicates.

Both faults were measured on a real store of 26,297 active artifacts:

* **488 noise artifacts** — 150 copies of the interrupt marker, 40-53 copies
  each of subagent prompt headers. Ingested before the filter knew about them.
* **5,975 duplicate rows in 2,581 groups — 22.7% of the store.** Ingestion
  deduplicates within a session but never across them, so anything an agent
  emits every run accumulates one copy per session.

Duplicates are not merely wasted space, they consume the recall budget: a live
recall returned five memories of which three were the same text, so k=5 bought
three distinct things. The literature puts redundant context at 30-40% of
retrieved tokens in production RAG; this store is worse than that.

Nothing is deleted. Rows are deactivated, which keeps ids that ``recall_outcomes``
and the eval history refer to, and makes the whole pass reversible.
"""
from __future__ import annotations

import hashlib
import re

#: Whitespace differences are not meaningful differences between two copies of
#: the same emitted text.
_WS = re.compile(r"\s+")


def content_key(text: str) -> str:
    """Stable identity for a piece of content, ignoring whitespace noise."""
    normalized = _WS.sub(" ", (text or "").strip())
    return hashlib.blake2b(normalized.encode("utf-8", "replace"),
                           digest_size=16).hexdigest()


def find_noise(store) -> list[str]:
    """Ids of active artifacts the ingestion filter would now reject."""
    from memor.ingest.claude_code import _AGENT_BRIEF_RE, _HARNESS_NOISE_RE

    rows = store.db.execute(
        "SELECT id, text FROM artifacts WHERE active=1 AND kind='session_chunk'"
    ).fetchall()
    out = []
    for row in rows:
        text = row["text"] or ""
        if _HARNESS_NOISE_RE.match(text) or _AGENT_BRIEF_RE.match(text):
            out.append(row["id"])
    return out


def find_duplicates(store) -> list[str]:
    """Ids to retire, keeping the earliest copy of each distinct content.

    The earliest is kept rather than the newest so that recall history already
    pointing at an id stays pointing at a live artifact.
    """
    rows = store.db.execute(
        "SELECT id, project, text, created_at FROM artifacts "
        "WHERE active=1 ORDER BY created_at ASC, id ASC").fetchall()
    seen: set[tuple[str, str]] = set()
    losers: list[str] = []
    for row in rows:
        key = (row["project"] or "", content_key(row["text"]))
        if key in seen:
            losers.append(row["id"])
        else:
            seen.add(key)
    return losers


def deactivate(store, artifact_ids: list[str], *, batch: int = 500) -> int:
    """Retire artifacts without deleting them. Returns how many changed."""
    if not artifact_ids:
        return 0
    changed = 0
    for i in range(0, len(artifact_ids), batch):
        chunk = artifact_ids[i:i + batch]
        marks = ",".join("?" * len(chunk))
        cur = store.db.execute(
            f"UPDATE artifacts SET active=0 WHERE active=1 AND id IN ({marks})", chunk)
        changed += cur.rowcount or 0
    store.db.commit()
    return changed


def prune(store, *, dry_run: bool = True) -> dict:
    """Report, and optionally apply, everything this would retire."""
    noise = find_noise(store)
    duplicates = [i for i in find_duplicates(store) if i not in set(noise)]
    before = store.db.execute(
        "SELECT COUNT(*) c FROM artifacts WHERE active=1").fetchone()["c"]

    result = {
        "active_before": before,
        "noise": len(noise),
        "duplicates": len(duplicates),
        "total": len(noise) + len(duplicates),
        "dry_run": dry_run,
    }
    result["pct"] = round(100 * result["total"] / before, 1) if before else 0.0
    if not dry_run:
        result["retired"] = deactivate(store, noise + duplicates)
        result["active_after"] = store.db.execute(
            "SELECT COUNT(*) c FROM artifacts WHERE active=1").fetchone()["c"]
    return result
