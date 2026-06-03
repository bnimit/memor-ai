"""Memorable Inspector — browse, query, and evaluate your memory store."""
from __future__ import annotations
import json, time
from pathlib import Path
import streamlit as st
from memorable.store.sqlite_store import SqliteStore
from memorable.retrieve.retriever import Retriever
from memorable.types import Scope

st.set_page_config(page_title="Memorable Inspector", layout="wide")

# --- Sidebar: DB selection ---
st.sidebar.title("Memorable Inspector")
db_files = sorted(Path(".").glob("*.db"))
db_path = st.sidebar.selectbox("Database", [str(f) for f in db_files] if db_files else ["memorable.db"])

@st.cache_resource
def load_store(path):
    from memorable.embed.local import LocalEmbedder
    e = LocalEmbedder()
    s = SqliteStore(path, dim=e.dim)
    return s, e

try:
    store, embedder = load_store(db_path)
except Exception as ex:
    st.error(f"Could not open {db_path}: {ex}")
    st.stop()

# --- Stats ---
total = store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
active = store.db.execute("SELECT COUNT(*) FROM artifacts WHERE active=1").fetchone()[0]
edges = store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
kinds = store.db.execute("SELECT kind, COUNT(*) FROM artifacts GROUP BY kind").fetchall()
sessions = store.db.execute("SELECT COUNT(DISTINCT json_extract(meta, '$.session_id')) FROM artifacts").fetchone()[0]

st.sidebar.markdown(f"""
**{total}** artifacts ({active} active) | **{edges}** edges | **{sessions}** sessions
""")
for k, c in kinds:
    st.sidebar.markdown(f"- `{k}`: {c}")

# --- Tabs ---
tab_query, tab_browse, tab_eval, tab_edges = st.tabs(["Query", "Browse", "Eval", "Edges"])

# ============================================================
# TAB 1: Query (retrieval inspector)
# ============================================================
with tab_query:
    st.header("Retrieval Inspector")
    col_q, col_opts = st.columns([3, 1])
    with col_q:
        query_text = st.text_input("Query", placeholder="what did we decide about auth?")
    with col_opts:
        projects = [r[0] for r in store.db.execute("SELECT DISTINCT project FROM artifacts").fetchall()]
        project = st.selectbox("Scope (project)", ["all"] + projects)
        k = st.slider("k (results)", 1, 30, 8)

    col_sim, col_rec, col_edge = st.columns(3)
    with col_sim:
        use_edges = st.checkbox("Edge expansion", value=True)
    with col_rec:
        recency_w = st.slider("Recency weight", 0.0, 1.0, 0.2, 0.05)

    if query_text:
        scope = Scope(project=project if project != "all" else None)
        r = Retriever(store, embedder, k=k, recency_weight=recency_w, edge_expand=use_edges)
        t0 = time.perf_counter()
        trace = r.query(query_text, scope)
        wall_ms = (time.perf_counter() - t0) * 1000

        st.markdown(f"**{len(trace.hits)} hits** in **{wall_ms:.1f}ms** "
                    f"({sum(h.artifact.token_count for h in trace.hits)} tokens)")

        for i, h in enumerate(trace.hits):
            with st.expander(f"#{i+1}  [{h.score:.3f}]  {h.artifact.id}  —  {h.artifact.text[:80]}..."):
                c1, c2 = st.columns([1, 3])
                with c1:
                    st.markdown("**Score breakdown**")
                    for comp, val in h.components.items():
                        bar_len = int(val * 20)
                        st.markdown(f"`{comp:8s}` {'█' * bar_len}{'░' * (20 - bar_len)} {val:.3f}")
                    st.markdown(f"""
- **kind:** `{h.artifact.kind}`
- **project:** `{h.artifact.project}`
- **session:** `{h.artifact.meta.get('session_id', '?')}`
- **tokens:** {h.artifact.token_count}
""")
                with c2:
                    st.markdown("**Full text**")
                    st.code(h.artifact.text[:2000], language=None)

# ============================================================
# TAB 2: Browse artifacts
# ============================================================
with tab_browse:
    st.header("Artifact Browser")
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        browse_project = st.selectbox("Project", ["all"] + projects, key="browse_proj")
    with col_f2:
        kind_opts = [r[0] for r in store.db.execute("SELECT DISTINCT kind FROM artifacts").fetchall()]
        browse_kind = st.selectbox("Kind", ["all"] + kind_opts, key="browse_kind")
    with col_f3:
        browse_search = st.text_input("Text search", placeholder="filter by text...", key="browse_search")

    where = ["1=1"]
    params = []
    if browse_project != "all":
        where.append("project = ?"); params.append(browse_project)
    if browse_kind != "all":
        where.append("kind = ?"); params.append(browse_kind)
    if browse_search:
        where.append("text LIKE ?"); params.append(f"%{browse_search}%")

    count = store.db.execute(f"SELECT COUNT(*) FROM artifacts WHERE {' AND '.join(where)}", params).fetchone()[0]
    st.markdown(f"**{count}** matching artifacts")

    page_size = 25
    page = st.number_input("Page", 1, max(1, (count + page_size - 1) // page_size), 1, key="browse_page")
    offset = (page - 1) * page_size

    rows = store.db.execute(
        f"SELECT * FROM artifacts WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset]).fetchall()

    for r in rows:
        a = store._row_to_artifact(r)
        status = "🟢" if r["active"] else "🔴"
        with st.expander(f"{status} `{a.id}` — {a.text[:100]}..."):
            st.markdown(f"**kind:** `{a.kind}` | **project:** `{a.project}` | "
                        f"**tokens:** {a.token_count} | **session:** `{a.meta.get('session_id', '?')}`")
            if r["superseded_by"]:
                st.warning(f"Superseded by: `{r['superseded_by']}`")
            st.code(a.text[:3000], language=None)

# ============================================================
# TAB 3: Eval results
# ============================================================
with tab_eval:
    st.header("Eval Runs")

    try:
        eval_rows = store.db.execute("SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 20").fetchall()
    except Exception:
        eval_rows = []

    if not eval_rows:
        st.info("No eval runs yet. Run `memorable eval <cases.json> --db <this db>` to generate results.")
    else:
        for er in eval_rows:
            config = json.loads(er["config"])
            metrics = json.loads(er["metrics"])
            with st.expander(f"Run #{er['id']} — k={config.get('k','?')}"):
                st.json(config)
                cols = st.columns(len(metrics))
                for col, (strat, m) in zip(cols, metrics.items()):
                    with col:
                        st.markdown(f"### {strat}")
                        st.metric("recall@k", f"{m.get('recall@k', 0):.3f}")
                        st.metric("nDCG@k", f"{m.get('ndcg@k', 0):.3f}")
                        st.metric("tokens sent", f"{m.get('tokens_sent', 0):.0f}")
                        savings = m.get("token_savings_vs_full", 0)
                        st.metric("token savings", f"{savings:.1%}")
                        st.metric("p50 latency", f"{m.get('latency_ms_p50', 0):.1f}ms")
                        if "latency_ms_p95" in m:
                            st.metric("p95 latency", f"{m.get('latency_ms_p95', 0):.1f}ms")

    st.markdown("---")
    st.subheader("Run eval from here")
    eval_project = st.selectbox("Project", projects, key="eval_proj")
    eval_k = st.slider("k", 1, 20, 8, key="eval_k")
    if st.button("Build cases & run eval"):
        from memorable.eval.dataset import build_counterfactual_cases, EvalCase
        from memorable.eval.runner import run_suite
        with st.spinner("Building counterfactual cases..."):
            rows_a = store.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'",
                                      (eval_project,)).fetchall()
            arts = [store._row_to_artifact(r) for r in rows_a]
            cases = build_counterfactual_cases(arts, project=eval_project)
        if not cases:
            st.warning("No eval cases could be built — need at least 2 sessions with overlapping vocabulary.")
        else:
            with st.spinner(f"Running eval on {len(cases)} cases..."):
                summary = run_suite(cases, store=store, embedder=embedder, k=eval_k)
                store.save_eval_run({"k": eval_k, "project": eval_project, "n_cases": len(cases)}, summary)
            st.success(f"Eval complete — {len(cases)} cases")
            cols = st.columns(len(summary))
            for col, (strat, m) in zip(cols, summary.items()):
                with col:
                    st.markdown(f"### {strat}")
                    st.metric("recall@k", f"{m.get('recall@k', 0):.3f}")
                    st.metric("nDCG@k", f"{m.get('ndcg@k', 0):.3f}")
                    st.metric("tokens sent", f"{m.get('tokens_sent', 0):.0f}")
                    st.metric("token savings", f"{m.get('token_savings_vs_full', 0):.1%}")
                    st.metric("p50 latency", f"{m.get('latency_ms_p50', 0):.1f}ms")

# ============================================================
# TAB 4: Edges / relationships
# ============================================================
with tab_edges:
    st.header("Edges & Relationships")
    edge_count = store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    if edge_count == 0:
        st.info("No edges yet. Run `memorable distill --project <project> --db <db>` to generate "
                "provenance and supersede edges from distillation.")
    else:
        by_type = store.db.execute("SELECT type, COUNT(*) FROM edges GROUP BY type").fetchall()
        for t, c in by_type:
            st.markdown(f"- `{t}`: **{c}** edges")

        st.markdown("---")
        st.subheader("Explore from artifact")
        explore_id = st.text_input("Artifact ID", placeholder="e.g. mem:s1:abc123")
        if explore_id:
            outgoing = store.db.execute(
                "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.dst_id = a.id WHERE e.src_id = ?",
                (explore_id,)).fetchall()
            incoming = store.db.execute(
                "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.src_id = a.id WHERE e.dst_id = ?",
                (explore_id,)).fetchall()
            if outgoing:
                st.markdown("**Outgoing edges (this → ...)**")
                for t, aid, txt in outgoing:
                    st.markdown(f"  —`{t}`→ `{aid}`: {txt[:100]}")
            if incoming:
                st.markdown("**Incoming edges (... → this)**")
                for t, aid, txt in incoming:
                    st.markdown(f"  `{aid}` —`{t}`→ this: {txt[:100]}")
            if not outgoing and not incoming:
                st.info("No edges connected to this artifact.")
