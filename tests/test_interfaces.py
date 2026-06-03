from memorable.interfaces import Embedder, LLM, MemoryStore
from memorable.embed.fake import FakeEmbedder

def test_protocols_exist():
    assert hasattr(Embedder, "embed")
    assert hasattr(LLM, "complete")
    for m in ("add_artifacts", "add_edge", "search", "neighbors"):
        assert hasattr(MemoryStore, m)

def test_fake_embedder_satisfies_protocol():
    e = FakeEmbedder(dim=16)
    assert isinstance(e, Embedder)
