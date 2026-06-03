# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Email **nimitbhandari17@gmail.com** with:

1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if you have one)

You will receive an acknowledgment within 48 hours. We aim to release a fix within 7 days of confirmation.

## Security Model

```
 ┌─────────────────────────────────────────────────────────────┐
 │  WHAT MEMOR STORES                                           │
 │                                                               │
 │  ~/.memor/memor.db                                           │
 │    - Session transcript text (code, conversations)           │
 │    - Distilled memories (decisions, patterns, fixes)         │
 │    - Embedding vectors (384-dim, not reversible to text)     │
 │    - Metadata (project names, timestamps, session IDs)       │
 │                                                               │
 │  ~/.memor/ingested.json                                      │
 │    - File paths and modification times of ingested files     │
 │                                                               │
 │  ~/.memor/distilled.json                                     │
 │    - Session IDs that have been distilled                    │
 └─────────────────────────────────────────────────────────────┘
```

### Threat model

Memor is a **local-first tool**. All data stays on your machine unless you explicitly configure an external LLM API. Key considerations:

**Data at rest**
- The SQLite database contains raw session text, which may include code, credentials mentioned in conversations, and architectural details
- The database file has no encryption by default — it is as secure as your filesystem permissions
- If your sessions contain secrets, the database will too

**Data in transit**
- Embedding: **local by default** (sentence-transformers). No network calls unless you configure an API embedder
- Distillation: sends extracted session text to the configured LLM API (Anthropic or OpenAI-compatible) over HTTPS
- The daemon reads from `~/.claude/projects/` — a directory already on your local machine

**API keys**
- `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are read from environment variables only
- They are never written to the database, logged, or included in any output

### Recommendations

1. **Protect the database file** — `chmod 600 ~/.memor/memor.db`
2. **Review before sharing** — the database contains raw session text. Don't share it without reviewing contents
3. **Use extractive-only mode** if you don't want session text sent to any external API (no API key = no external calls)
4. **Audit the daemon** — it reads all `.jsonl` files under `~/.claude/projects/`. If those files contain sensitive content, it will be ingested

## Dependencies

Core dependencies are minimal and well-established:

| Dependency | Purpose | Notes |
|---|---|---|
| `sqlite-vec` | Vector search extension | C extension, pinned version |
| `numpy` | Vector operations | Widely audited |
| `typer` | CLI framework | No network access |
| `httpx` | HTTP client (API embedders/LLMs) | Only used when API endpoints configured |
| `sentence-transformers` | Local embeddings | Optional, downloads model on first use |
| `anthropic` | Anthropic API client | Optional, only for LLM distillation |
