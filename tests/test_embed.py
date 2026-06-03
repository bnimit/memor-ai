from memorable.embed.fake import FakeEmbedder

def test_fake_embedder_deterministic_and_dim():
    e = FakeEmbedder(dim=16)
    v1 = e.embed(["auth refresh loop"])[0]
    v2 = e.embed(["auth refresh loop"])[0]
    assert len(v1) == 16 and v1 == v2          # deterministic
    assert e.embed(["different text"])[0] != v1 # content-sensitive
    import math
    assert abs(math.sqrt(sum(x*x for x in v1)) - 1.0) < 1e-6  # normalized
