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

#: Source chunks kept per memory. A memory distilled from 30 chunks does not
#: need all 30 on screen to show that it has provenance, and at 400 nodes in a
#: 900x520 frame there is 34px of space per node against a ~150px label.
FANOUT_PER_MEMORY = 3


def build_provenance_graph(store, project: str, limit: int = 60) -> dict:
    """Nodes and edges for one project's distillation lineage.

    Scoped to a project because the graph is only legible at that scale, and
    capped because a browser cannot lay out 12,138 edges usefully.
    """
    # Pick the memories first, then pull in only their edges. Selecting the most
    # recent *edges* instead produced a field of near-orphans: 401 nodes joined
    # by 237 links, most of them a lone pair, which is a scatter plot rather than
    # a lineage. Ordering by how much each memory actually connects gives the
    # same budget something to show.
    seeds = store.db.execute(
        """
        SELECT a.id, COUNT(e.dst_id) AS degree
        FROM artifacts a
        LEFT JOIN edges e ON e.src_id = a.id
        WHERE a.project = ? AND a.kind = 'memory'
        GROUP BY a.id
        HAVING degree > 0
        ORDER BY degree DESC, a.created_at DESC
        LIMIT ?
        """,
        (project, limit),
    ).fetchall()
    seed_ids = [r["id"] for r in seeds]
    seed_set = set(seed_ids)
    if not seed_ids:
        return {"project": project, "nodes": [], "edges": [],
                "stats": _stats(store, project)}

    qs = ",".join("?" * len(seed_ids))
    rows = store.db.execute(
        f"""SELECT src_id, dst_id, type FROM edges
            WHERE src_id IN ({qs}) OR dst_id IN ({qs})
            ORDER BY CASE type WHEN 'derived_from' THEN 0 ELSE 1 END""",
        seed_ids + seed_ids,
    ).fetchall()

    # Each seed keeps a few of its source chunks. A memory distilled from 30
    # chunks would otherwise drag all 30 in, and the point is to show that a
    # memory has provenance, not to enumerate it.
    wanted: set[str] = set(seed_ids)
    edges: list[dict] = []
    per_seed: dict[str, int] = {}
    node_budget = min(MAX_NODES, max(limit * 3, limit + 10))
    for r in rows:
        src, dst = r["src_id"], r["dst_id"]
        # A link between two chosen memories is the story and is always kept.
        # One that reaches outside the selection is not: supersedes edges form
        # long revision chains -- 329 of them against 24 derived_from links on
        # one real project -- and following them pulls the whole history back
        # in, which is what made a 24-memory request render 292 nodes.
        if src in seed_set and dst in seed_set:
            wanted.add(src)
            wanted.add(dst)
            edges.append({"source": src, "target": dst, "type": r["type"]})
            continue
        if per_seed.get(src, 0) >= FANOUT_PER_MEMORY:
            continue
        if len(wanted) >= node_budget and dst not in wanted:
            continue
        per_seed[src] = per_seed.get(src, 0) + 1
        wanted.add(src)
        wanted.add(dst)
        edges.append({"source": src, "target": dst, "type": r["type"]})

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
            "label": _label(r["preview"]),
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


#: Openings that identify instruction boilerplate rather than content.
BORING = ("you are ", "this is a", "this is an", "your task", "implement one",
          "read the brief", "i'll ", "i will ", "let me ", "okay", "sure")


def _label(preview: str | None) -> str:
    """A few readable words for the node itself, not the tooltip.

    An abstract dot forces the reader to hover before the picture means
    anything. Distilled text often opens with markdown or a role prefix, so
    strip those rather than render "##" as the label.
    """
    text = (preview or "").strip()
    if not text:
        return ""
    for prefix in ("user:", "assistant:", "## ", "# ", "**"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()

    # Distilled text often opens with instruction boilerplate ("You are doing
    # a...", "This is a scoped..."), which labels several nodes identically and
    # says nothing about what the memory holds. Skip to the first line that
    # carries content rather than render the preamble.
    lowered = text.lower()
    if lowered.startswith(BORING):
        # Section headings ("## Context") are structure, not content, so keep
        # walking past them to the first line that actually says something.
        HEADINGS = {"context", "summary", "goal", "verdict", "background",
                    "what was requested", "task", "findings", "notes"}
        for line in text.split("\n")[1:]:
            candidate = line.strip(" #*-`")
            if len(candidate) <= 12:
                continue
            if candidate.lower().rstrip(":").strip() in HEADINGS:
                continue
            if candidate.lower().startswith(BORING):
                continue
            text = candidate
            break
    text = text.replace("`", "").replace("*", "").replace("\n", " ")
    words = text.split()
    if not words:
        return ""
    # Four words is enough when the text opens with its subject, but a node
    # whose only text begins with boilerplate needs more of it before the label
    # distinguishes anything: "You are doing a" labelled several nodes
    # identically. Fall back to filling the width instead.
    take = 4
    if " ".join(words[:4]).lower().startswith(BORING):
        take = 9
    out = " ".join(words[:take])
    return out[:38] + ("…" if len(out) > 38 or len(words) > take else "")
