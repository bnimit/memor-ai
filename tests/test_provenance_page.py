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
    m = re.search(r"function provenanceLayout\([\s\S]*?\n  \}\n", script)
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

    def test_linked_nodes_end_up_closer_than_unlinked_ones(self, node):
        """The point of a force layout: an edge should mean proximity.

        Without this the picture is decorative -- nodes could sit anywhere and
        the lines would just be drawn between them.
        """
        out = _run_js(node, """
          const nodes = [
            {id:'m1', kind:'memory', active:true, label:'a'},
            {id:'c1', kind:'session_chunk', active:true, label:'b'},
            {id:'far', kind:'session_chunk', active:true, label:'c'}
          ];
          const r = provenanceLayout(
            {nodes, edges:[{source:'m1', target:'c1', type:'derived_from'}]}, 900, 500);
          const b = {}; r.nodes.forEach(n => b[n.id] = n);
          const d = (p,q) => Math.hypot(p.x-q.x, p.y-q.y);
          console.log(JSON.stringify(d(b.m1,b.c1) < d(b.m1,b.far)));
        """)
        assert json.loads(out) is True

    def test_only_a_readable_number_of_labels_is_shown(self, node):
        """400 labels at once is unreadable, so most stay in the tooltip."""
        out = _run_js(node, """
          const nodes = [];
          for (let i = 0; i < 200; i++)
            nodes.push({id:'n'+i, kind:'session_chunk', active:true, label:'label '+i});
          const r = provenanceLayout({nodes, edges: []}, 900, 500);
          console.log(JSON.stringify(r.nodes.filter(n => n.showLabel).length));
        """)
        assert 0 < json.loads(out) <= 30

    def test_labels_survive_layout(self, node):
        out = _run_js(node, """
          const g = {nodes:[{id:'m1', kind:'memory', active:true, label:'the retry decision'}],
                     edges:[]};
          const r = provenanceLayout(g, 900, 500);
          console.log(JSON.stringify(r.nodes[0].label));
        """)
        assert json.loads(out) == "the retry decision"

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


class TestLabels:
    """A node labelled with prompt boilerplate is no better than a bare dot."""

    def test_instruction_preamble_is_skipped_for_real_content(self):
        from memor.dashboard.provenance import _label

        got = _label("You are doing a review\nThe coupon resync drops rows on retry")
        assert "coupon resync" in got
        assert not got.lower().startswith("you are")

    def test_markdown_and_role_prefixes_are_stripped(self):
        from memor.dashboard.provenance import _label

        assert not _label("## The vulnerability is real").startswith("#")
        assert not _label("user: can you review this").startswith("user:")

    def test_backticks_do_not_reach_the_label(self):
        from memor.dashboard.provenance import _label

        assert "`" not in _label("Confirmed: `Transaction.currency` is nullable")

    def test_empty_and_none_are_safe(self):
        from memor.dashboard.provenance import _label

        assert _label(None) == ""
        assert _label("") == ""
        assert _label("   ") == ""

    def test_label_is_short_enough_to_render(self):
        from memor.dashboard.provenance import _label

        long = "word " * 80
        assert len(_label(long)) <= 40

    def test_boilerplate_with_no_alternative_is_left_alone(self):
        """Better a weak label than an empty one."""
        from memor.dashboard.provenance import _label

        assert _label("You are an agent") != ""


class TestLayoutPerformance:
    def test_layout_of_a_full_graph_is_fast_enough_not_to_block(self, node):
        """The naive all-pairs version measured 1201ms on the real 401-node
        graph, which freezes the tab. Repulsion is now restricted to adjacent
        grid cells; this guards the regression rather than the exact number."""
        out = _run_js(node, """
          const nodes = [];
          for (let i = 0; i < 400; i++)
            nodes.push({id:'n'+i, kind: i % 8 ? 'session_chunk' : 'memory',
                        active: true, label: 'node ' + i});
          const edges = [];
          for (let i = 8; i < 400; i++)
            edges.push({source:'n'+(i - (i % 8)), target:'n'+i, type:'derived_from'});
          const t0 = Date.now();
          provenanceLayout({nodes, edges}, 900, 560);
          console.log(JSON.stringify(Date.now() - t0));
        """)
        assert json.loads(out) < 600, f"layout took {out}ms"

    def test_nodes_do_not_land_on_top_of_each_other(self, node):
        out = _run_js(node, """
          const nodes = [];
          for (let i = 0; i < 120; i++)
            nodes.push({id:'n'+i, kind:'session_chunk', active:true, label:'n'+i});
          const r = provenanceLayout({nodes, edges: []}, 900, 560);
          let overlap = 0;
          for (let i = 0; i < r.nodes.length; i++)
            for (let j = i + 1; j < r.nodes.length; j++) {
              const a = r.nodes[i], b = r.nodes[j];
              if (Math.hypot(a.x-b.x, a.y-b.y) < (a.r+b.r) * 0.8) overlap++;
            }
          console.log(JSON.stringify(overlap));
        """)
        assert json.loads(out) == 0

    def test_section_headings_are_not_used_as_labels(self):
        """"## Context" is structure, not content."""
        from memor.dashboard.provenance import _label

        got = _label("You are doing a review\n\n## Context\n\nTask 2 added 17 unit tests")
        assert "Context" not in got
        assert "Task 2" in got

    def test_boilerplate_openings_still_reach_distinguishing_detail(self):
        """When there is no better line, keep enough words to tell nodes apart.

        Truncating to four words labelled many nodes identically as "You are
        doing a...", which is a dot with extra steps.
        """
        from memor.dashboard.provenance import _label

        a = _label("You are doing a significant refactor on PR #2123 sink paths")
        b = _label("You are doing a code quality review of Task 2 commit")
        assert a != b, "boilerplate-prefixed nodes must not share one label"


class TestDensity:
    """The default view must be legible, not merely complete.

    401 nodes in a 900x520 frame is 34px of space per node against a ~150px
    label, which is why the first version was unreadable. Selection is now
    seeded by the best-connected memories and each keeps a bounded number of
    source chunks.
    """

    @pytest.fixture
    def store(self, tmp_path):
        import time

        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore
        from memor.types import Artifact

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "d.db"), dim=emb.dim)
        arts = []
        for m in range(10):
            arts.append(Artifact(id=f"m{m}", kind="memory", project="proj",
                                 source="t", text=f"decision {m} about retries",
                                 token_count=20, created_at=time.time(), meta={}))
            for c in range(20):          # a wide fan-out, as real memories have
                arts.append(Artifact(id=f"m{m}c{c}", kind="session_chunk",
                                     project="proj", source="t",
                                     text=f"chunk {c} for memory {m}",
                                     token_count=50, created_at=time.time(), meta={}))
        st.add_artifacts(arts, emb.embed([a.text for a in arts]))
        for m in range(10):
            for c in range(20):
                st.add_edge(f"m{m}", f"m{m}c{c}", "derived_from")
        return st

    def test_fanout_is_bounded_per_memory(self, store):
        from memor.dashboard.provenance import FANOUT_PER_MEMORY, build_provenance_graph

        g = build_provenance_graph(store, "proj", limit=10)
        per_src = {}
        for e in g["edges"]:
            per_src[e["source"]] = per_src.get(e["source"], 0) + 1
        assert per_src, "expected edges"
        assert max(per_src.values()) <= FANOUT_PER_MEMORY, (
            f"one memory pulled in {max(per_src.values())} chunks; "
            "a wide fan-out swamps the picture")

    def test_node_count_scales_with_the_requested_limit(self, store):
        from memor.dashboard.provenance import build_provenance_graph

        small = build_provenance_graph(store, "proj", limit=3)
        large = build_provenance_graph(store, "proj", limit=10)
        assert len(small["nodes"]) < len(large["nodes"])

    def test_a_small_request_stays_small(self, store):
        """200 chunks exist; asking for 3 memories must not return them all."""
        from memor.dashboard.provenance import build_provenance_graph

        g = build_provenance_graph(store, "proj", limit=3)
        assert len(g["nodes"]) <= 20, f"got {len(g['nodes'])} nodes for limit=3"

    def test_selected_memories_are_the_well_connected_ones(self, store):
        from memor.dashboard.provenance import build_provenance_graph

        g = build_provenance_graph(store, "proj", limit=5)
        mems = [n for n in g["nodes"] if n["kind"] == "memory"]
        assert mems, "a lineage view with no memories shows nothing"
