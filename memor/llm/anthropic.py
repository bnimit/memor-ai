import time


class AnthropicLLM:
    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None,
                 *, max_retries: int = 8):
        import anthropic

        # Disable the SDK's own (silent) retries so our visible backoff below
        # is the single source of waiting — otherwise the SDK sleeps on 429
        # internally before raising, which looks like a hang.
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.model = model
        self.max_retries = max_retries

    def complete(self, prompt: str, *, max_tokens: int = 1024, grammar: str | None = None) -> str:
        # grammar accepted for interface parity; ignored (no GBNF over HTTP)
        import anthropic

        attempt = 0
        while True:
            try:
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(b.text for b in msg.content if b.type == "text")
            except anthropic.RateLimitError as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                # Honor Retry-After when present; otherwise wait out the
                # per-minute token window (tier-1 limits reset each minute).
                wait = _retry_after_seconds(e) or 60
                print(f"  rate limited (429); waiting {wait}s then retrying "
                      f"(attempt {attempt}/{self.max_retries})...", flush=True)
                time.sleep(wait)


def _retry_after_seconds(err) -> float | None:
    try:
        val = err.response.headers.get("retry-after")
        return float(val) if val is not None else None
    except Exception:
        return None
