DISTILL_PROMPT = """You are distilling a coding session into reusable memory.
Return STRICT JSON: {{"memories":[{{"type":"decision|lesson|snippet|bugfix",
"text":"<one concise reusable fact>", "supersedes_text":"<prior fact this reverses, or omit>"}}]}}
Only include durable, reusable facts. Session text:
---
{session_text}
---"""
