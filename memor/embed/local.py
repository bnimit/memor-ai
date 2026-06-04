import numpy as np


class LocalEmbedder:
    """ONNX-based embedder. Default bge-small-en-v1.5 (dim 384).

    Uses onnxruntime + tokenizers instead of sentence-transformers/PyTorch,
    reducing install footprint from ~2.3GB to ~80MB.
    """
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(model_name, "onnx/model.onnx")
        tok_path = hf_hub_download(model_name, "tokenizer.json")

        self._session = ort.InferenceSession(model_path)
        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_padding()
        self._tokenizer.enable_truncation(max_length=512)
        self.dim = self._session.get_outputs()[0].shape[-1]

    def embed(self, texts: list[str]) -> list[list[float]]:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        outputs = self._session.run(None, {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        })

        # Mean pooling + L2 normalize
        token_emb = outputs[0]
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        pooled = (token_emb * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
        return (pooled / norms).tolist()
