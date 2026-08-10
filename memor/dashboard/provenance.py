"""Provenance graph: how raw transcript becomes durable knowledge.

This draws the edges memor actually records — `derived_from` (a memory and the
session chunks it was distilled from) and `supersedes` (a memory replaced by a
later one) — rather than an inferred entity graph.

That distinction is deliberate. An entity graph over shared identifiers was
measured first and rejected: 1-hop expansion added a median of 12 artifacts for
8 slots, which is not retrieval but more ranking work, and rendering all 33,883
identifiers over 27,218 artifacts produces a hairball whose readable version is
a subset chosen for looks. Every edge drawn here was written by the distiller at
ingest time and can be traced back to the text that produced it.
"""
from __future__ import annotations

MAX_NODES = 400


def build_provenance_graph(store, project: str, limit: int = 60) -> dict:
    """Nodes and edges for one project's distillation lineage.

    Scoped to a project because the graph is only legible at that scale, and
    capped because a browser cannot lay out 12,138 edges usefully.
    """
    rows = store.db.execute(
        """
        SELECT e.src_id, e.dst_id, e.type
        FROM edges e
        JOIN artifacts a ON a.id = e.src_id
        WHERE a.project = ? AND a.active = 1
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (project, limit * 4),
    ).fetchall()

    wanted: set[str] = set()
    edges: list[dict] = []
    for r in rows:
        if len(wanted) >= MAX_NODES:
            break
        wanted.add(r["src_id"])
        wanted.add(r["dst_id"])
        edges.append({"source": r["src_id"], "target": r["dst_id"], "type": r["type"]})

    if not wanted:
        return {"project": project, "nodes": [], "edges": [], "stats": _stats(store, project)}

    qmarks = ",".join("?" * len(wanted))
    art_rows = store.db.execute(
        f"""SELECT id, kind, substr(text, 1, 160) AS preview, token_count,
                   created_at, active
            FROM artifacts WHERE id IN ({qmarks})""",
        list(wanted),
    ).fetchall()

    known = {r["id"] for r in art_rows}
    nodes = [
        {
            "id": r["id"],
            "kind": r["kind"],
            "preview": (r["preview"] or "").strip(),
            "tokens": r["token_count"],
            "created_at": r["created_at"],
            "active": bool(r["active"]),
        }
        for r in art_rows
    ]
    # An edge whose endpoint was pruned or deleted would render as a line into
    # nothing, so drop it rather than let the picture imply a node that is gone.
    edges = [e for e in edges if e["source"] in known and e["target"] in known]

    return {"project": project, "nodes": nodes, "edges": edges,
            "stats": _stats(store, project)}


def _stats(store, project: str) -> dict:
    """Headline counts, each naming the population it was measured over."""
    row = store.db.execute(
        """
        SELECT
          SUM(kind = 'session_chunk') AS chunks,
          SUM(kind = 'memory') AS memories,
          SUM(kind = 'memory' AND active = 0) AS retired
        FROM artifacts WHERE project = ?
        """,
        (project,),
    ).fetchone()

    edge_row = store.db.execute(
        """
        SELECT
          SUM(e.type = 'derived_from') AS derived,
          SUM(e.type = 'supersedes') AS superseded
        FROM edges e JOIN artifacts a ON a.id = e.src_id
        WHERE a.project = ?
        """,
        (project,),
    ).fetchone()

    chunks = row["chunks"] or 0
    memories = row["memories"] or 0
    return {
        "chunks": chunks,
        "memories": memories,
        "retired": row["retired"] or 0,
        "derived_from": (edge_row["derived"] or 0) if edge_row else 0,
        "supersedes": (edge_row["superseded"] or 0) if edge_row else 0,
        # The ratio is the story: how much raw transcript collapses into one
        # durable memory.
        "compression_ratio": round(chunks / memories, 1) if memories else 0.0,
    }


def list_projects_with_provenance(store, limit: int = 20) -> list[dict]:
    rows = store.db.execute(
        """
        SELECT a.project AS project, COUNT(*) AS edges
        FROM edges e JOIN artifacts a ON a.id = e.src_id
        WHERE a.active = 1
        GROUP BY a.project
        ORDER BY edges DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{"project": r["project"], "edges": r["edges"]} for r in rows]
