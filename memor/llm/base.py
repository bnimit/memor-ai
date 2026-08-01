DISTILL_PROMPT = """You are distilling a coding session into reusable memory.
Return STRICT JSON: {{"memories":[{{"type":"decision|lesson|snippet|bugfix",
"text":"<one concise reusable fact>", "supersedes_text":"<prior fact this reverses, or omit>"}}]}}
Only include durable, reusable facts. Session text:
---
{session_text}
---"""

# Backend contract: complete(prompt: str, *, max_tokens: int = ...,
# grammar: str | None = None) -> str. Cloud backends (anthropic, openai_compat)
# ignore `grammar`; the local llama_cpp backend enforces it via GBNF.
