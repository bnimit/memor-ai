import hashlib, math

class FakeEmbedder:
    """Deterministic hash-based embedder for tests. No model download."""
    def __init__(self, dim: int = 16):
        self.dim = dim
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(x*x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out
