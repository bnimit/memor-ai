import httpx


class APIEmbedder:
    """OpenAI-compatible embedding API."""
    def __init__(self, base_url: str = "https://api.openai.com/v1", api_key: str = "",
                 model: str = "text-embedding-3-small", dim: int = 1536):
        self.base_url, self.api_key, self.model, self.dim = base_url, api_key, model, dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts}, timeout=60)
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
