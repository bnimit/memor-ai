from model2vec import StaticModel


class LocalEmbedder:
    """Local embedding via model2vec. Default potion-base-8M (dim 256).

    Static token embeddings — no transformer inference needed.
    ~62MB model download on first use, 500x faster than ONNX transformers.
    """
    def __init__(self, model_name: str = "minishlab/potion-base-8M"):
        self._model = StaticModel.from_pretrained(model_name)
        self.dim = self._model.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts)
        return vecs.tolist()
