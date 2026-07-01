# tests/test_retriever_keys.py
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope

def _mem(s, e, mid, value, keys):
    a = Artifact(id=mid, kind="memory", project="p", source="distill",
                 text=value, token_count=3, created_at=1000.0,
                 meta={"mem_type": "fact", "fact": keys[0][1]})
    s.add_artifacts([a], e.embed([value]))
    s.add_keys(mid, keys, e.embed([kt for _, kt in keys]))

def test_query_matches_via_question_key_returns_value(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    _mem(s, e, "m1", "The auth system uses signed session cookies.",
         [("fact", "auth uses session cookies"), ("question", "how does login work")])
    r = Retriever(s, e, k=3, edge_expand=False, use_keys=True)
    tr = r.query("how does login work", Scope(project="p"))
    assert tr.hits and tr.hits[0].artifact.id == "m1"
    assert tr.hits[0].artifact.text.startswith("The auth system")

def test_use_keys_false_unchanged(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    a = Artifact(id="x", kind="memory", project="p", source="t",
                 text="auth refresh token loop", token_count=4, created_at=1000.0, meta={})
    s.add_artifacts([a], e.embed([a.text]))
    r = Retriever(s, e, k=3, edge_expand=False, use_keys=False)
    tr = r.query("auth refresh", Scope(project="p"))
    assert tr.hits[0].artifact.id == "x"

def test_lexical_key_channel_surfaces_rare_identifier(tmp_path):
    """A memory whose key contains a rare identifier token surfaces via the
    fused lexical channel even when the token is so rare that a static embedder
    would assign it a zero-overlap vector (simulated here by using a FakeEmbedder
    that hash-collapses all tokens: the rare token has non-zero vec similarity but
    the BM25 hit should fuse via RRF into the final ranking).

    Specifically: we store two memories.  m_rare has a key containing the rare
    identifier 'xk9zqvplrare_tok9' which appears in the query.  m_other has a
    key with common unrelated tokens.  We verify that:
    1. store.search_keys_lexical exists and returns m_rare's id when queried.
    2. Retriever._query_keys (use_keys=True) includes m_rare in its hits.
    """
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)

    # m_rare: value text is generic; key contains the rare identifier
    _mem(s, e, "m_rare", "Uses internal tracing framework for observability.",
         [("fact", "xk9zqvplrare_tok9 is the tracing hook identifier")])

    # m_other: key contains common unrelated tokens that won't match the query
    _mem(s, e, "m_other", "Database connection pool is set to 10.",
         [("fact", "database connection pool size")])

    # Test 1: search_keys_lexical is wired and returns the rare-token memory
    assert hasattr(s, "search_keys_lexical"), (
        "SqliteStore must expose search_keys_lexical for the hybrid key path"
    )
    lex_hits = s.search_keys_lexical("xk9zqvplrare_tok9", Scope(project="p"), k=10)
    lex_ids = [mid for mid, _ in lex_hits]
    assert "m_rare" in lex_ids, (
        f"search_keys_lexical should return m_rare for rare token query; got {lex_ids}"
    )

    # Test 2: the fused key retriever surfaces m_rare in the top hits
    r = Retriever(s, e, k=4, edge_expand=False, use_keys=True)
    tr = r.query("xk9zqvplrare_tok9", Scope(project="p"))
    hit_ids = [h.artifact.id for h in tr.hits]
    assert "m_rare" in hit_ids, (
        f"_query_keys with fused lexical should include m_rare; got {hit_ids}"
    )
