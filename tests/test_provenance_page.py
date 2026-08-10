"""The provenance page: API shape and the layout function, executed for real.

The renderer is tested by running it under node rather than by reading it. That
is not belt-and-braces here: a previous dashboard panel shipped a locale bug
that rendered 2,343,748 as "23,43,748", and it was only caught by executing the
function. Layout is a pure function precisely so this is possible.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "memor" / "dashboard" / "static" / "index.html"


def _extract_script() -> str:
    html = INDEX.read_text()
    m = re.search(r"<script>([\s\S]*)</script>", html)
    assert m, "dashboard has no inline script"
    return m.group(1)


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if not exe:
        pytest.skip("node not available")
    return exe


def _run_js(node_exe: str, snippet: str) -> str:
    """Run a snippet with the dashboard's provenanceLayout in scope."""
    script = _extract_script()
    # The function is self-contained; pull it out rather than booting a DOM.
    m = re.search(r"function provenanceLayout\(graph, width, height\) \{[\s\S]*?\n  \}\n",
                  script)
    assert m, "provenanceLayout not found in the dashboard script"
    prog = m.group(0) + "\n" + snippet
    out = subprocess.run([node_exe, "-e", prog], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class TestLayout:
    def test_empty_graph_does_not_throw(self, node):
        out = _run_js(node, """
          const r = provenanceLayout({nodes: [], edges: []}, 900, 500);
          console.log(JSON.stringify({n: r.nodes.length, e: r.edges.length}));
        """)
        assert json.loads(out) == {"n": 0, "e": 0}

    def test_null_graph_does_not_throw(self, node):
        out = _run_js(node, """
          console.log(JSON.stringify(provenanceLayout(null, 900, 500).nodes.length));
        """)
        assert json.loads(out) == 0

    def test_kinds_are_separated_into_lanes(self, node):
        out = _run_js(node, """
          const g = {nodes: [
            {id:'c1', kind:'session_chunk', active:true},
            {id:'m1', kind:'memory', active:true},
            {id:'m2', kind:'memory', active:false}
          ], edges: []};
          const r = provenanceLayout(g, 900, 500);
          const byId = {}; r.nodes.forEach(n => byId[n.id] = n);
          console.log(JSON.stringify({
            chunk: byId.c1.lane, live: byId.m1.lane, retired: byId.m2.lane
          }));
        """)
        assert json.loads(out) == {"chunk": 0, "live": 1, "retired": 2}

    def test_an_edge_to_a_missing_node_is_dropped(self, node):
        """A line into empty space would imply a node that is not there."""
        out = _run_js(node, """
          const g = {nodes: [{id:'m1', kind:'memory', active:true}],
                     edges: [{source:'m1', target:'GONE', type:'derived_from'}]};
          console.log(JSON.stringify(provenanceLayout(g, 900, 500).edges.length));
        """)
        assert json.loads(out) == 0

    def test_nodes_stay_inside_the_canvas(self, node):
        out = _run_js(node, """
          const nodes = [];
          for (let i = 0; i < 40; i++) nodes.push({id:'c'+i, kind:'session_chunk', active:true});
          for (let i = 0; i < 10; i++) nodes.push({id:'m'+i, kind:'memory', active:true});
          const r = provenanceLayout({nodes, edges: []}, 900, 500);
          const bad = r.nodes.filter(n => n.x < 0 || n.x > 900 || n.y < 0 || n.y > 500);
          console.log(JSON.stringify(bad.length));
        """)
        assert json.loads(out) == 0

    def test_layout_is_deterministic(self, node):
        """A picture that jumps on every refresh cannot show growth over time."""
        out = _run_js(node, """
          const g = {nodes: [
            {id:'m1', kind:'memory', active:true},
            {id:'c1', kind:'session_chunk', active:true}
          ], edges: [{source:'m1', target:'c1', type:'derived_from'}]};
          const a = JSON.stringify(provenanceLayout(g, 900, 500));
          const b = JSON.stringify(provenanceLayout(g, 900, 500));
          console.log(JSON.stringify(a === b));
        """)
        assert json.loads(out) is True

    def test_memories_are_drawn_larger_than_chunks(self, node):
        out = _run_js(node, """
          const g = {nodes: [
            {id:'m1', kind:'memory', active:true},
            {id:'c1', kind:'session_chunk', active:true}
          ], edges: []};
          const r = provenanceLayout(g, 900, 500);
          const byId = {}; r.nodes.forEach(n => byId[n.id] = n);
          console.log(JSON.stringify(byId.m1.r > byId.c1.r));
        """)
        assert json.loads(out) is True


class TestApi:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from memor.dashboard.server import create_app
        return TestClient(create_app(str(tmp_path / "empty.db")))

    def test_empty_store_returns_an_empty_graph_not_an_error(self, client):
        r = client.get("/api/provenance")
        assert r.status_code == 200
        body = r.json()
        assert body["nodes"] == [] and body["edges"] == []

    def test_graph_reports_the_population_it_measured(self, client):
        body = client.get("/api/provenance").json()
        assert "stats" in body


class TestBuilder:
    @pytest.fixture
    def store(self, tmp_path):
        import time
        import uuid

        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore
        from memor.types import Artifact

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "p.db"), dim=emb.dim)
        chunks = [Artifact(id=f"c{i}", kind="session_chunk", project="proj",
                           source="t", text=f"raw transcript {i}", token_count=100,
                           created_at=time.time(), meta={}) for i in range(6)]
        mem = Artifact(id="m1", kind="memory", project="proj", source="t",
                       text="the distilled decision", token_count=40,
                       created_at=time.time(), meta={})
        st.add_artifacts(chunks + [mem], emb.embed([a.text for a in chunks + [mem]]))
        for c in chunks[:3]:
            st.add_edge("m1", c.id, "derived_from")
        return st

    def test_lineage_is_returned_for_the_project(self, store):
        from memor.dashboard.provenance import build_provenance_graph

        g = build_provenance_graph(store, "proj")
        assert g["edges"], "expected derived_from edges"
        assert all(e["type"] == "derived_from" for e in g["edges"])
        assert {n["id"] for n in g["nodes"]} >= {"m1", "c0"}

    def test_compression_ratio_is_chunks_per_memory(self, store):
        from memor.dashboard.provenance import build_provenance_graph

        stats = build_provenance_graph(store, "proj")["stats"]
        assert stats["chunks"] == 6 and stats["memories"] == 1
        assert stats["compression_ratio"] == 6.0

    def test_a_project_with_no_memories_does_not_divide_by_zero(self, tmp_path):
        import time

        from memor.dashboard.provenance import build_provenance_graph
        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore
        from memor.types import Artifact

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "q.db"), dim=emb.dim)
        a = Artifact(id="c0", kind="session_chunk", project="bare", source="t",
                     text="only raw", token_count=10, created_at=time.time(), meta={})
        st.add_artifacts([a], emb.embed([a.text]))
        assert build_provenance_graph(st, "bare")["stats"]["compression_ratio"] == 0.0

    def test_unknown_project_is_empty_not_an_error(self, store):
        from memor.dashboard.provenance import build_provenance_graph

        g = build_provenance_graph(store, "does-not-exist")
        assert g["nodes"] == [] and g["edges"] == []
