import httpx


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float | None = None):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        # temperature=0 makes the eval judge deterministic/repeatable; None omits
        # the field (server default) for normal use.
        self.temperature = temperature

    def complete(self, prompt: str, *, max_tokens: int = 1024, grammar: str | None = None) -> str:
        # grammar accepted for interface parity; ignored (no GBNF over HTTP)
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
