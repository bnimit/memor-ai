class LocalEmbedder:
    """sentence-transformers embedder. Default bge-small-en-v1.5 (dim 384)."""
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_embedding_dimension()
    def embed(self, texts: list[str]) -> list[list[float]]:
        embs = self._model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [e.tolist() for e in embs]
