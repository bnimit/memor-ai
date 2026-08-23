# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.13.x  | Yes       |
| < 0.13  | No        |

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
┌───────────────────────────────────────────────────────────────────┐
│  WHAT MEMOR STORES                                                │
│                                                                   │
│  ~/.memor/memor.db                                                │
│    session transcript text (code, conversations)                  │
│    distilled memories (decisions, patterns, fixes)                │
│    embedding vectors (256-dim, not reversible to text)            │
│    recall + outcome log (what was served, and whether it helped)  │
│    metadata (project names, timestamps, session IDs)              │
│                                                                   │
│  ~/.memor/ingested.json                                           │
│    file paths and modification times of ingested sessions         │
│                                                                   │
│  ~/.memor/distilled.json                                          │
│    session IDs that have been distilled                           │
└───────────────────────────────────────────────────────────────────┘
```

### Threat model

Memor is a **local-first tool**. All data stays on your machine unless you explicitly configure an external LLM API. Key considerations:

**Data at rest**
- The SQLite database contains raw session text, which may include code, credentials mentioned in conversations, and architectural details
- The database file has no encryption by default — it is as secure as your filesystem permissions
- If your sessions contain secrets, the database will too

**Data in transit**
- Embedding: **local by default** (model2vec static embeddings). No network calls unless you configure an API embedder
- Distillation: sends extracted session text to the configured LLM API (Anthropic or OpenAI-compatible) over HTTPS
- The daemon reads from the session stores your agents already write: `~/.claude/projects/`, `~/.jcode/sessions/`, Goose's `sessions.db`, `~/.kimi/sessions/`. All are directories already on your machine, and the daemon only ever reads them

**API keys**
- `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are read from environment variables only
- They are never written to the database, logged, or included in any output

### Recommendations

1. **Protect the database file** — `chmod 600 ~/.memor/memor.db`
2. **Review before sharing** — the database contains raw session text. Don't share it without reviewing contents
3. **Use extractive-only mode** if you don't want session text sent to any external API (no API key = no external calls)
4. **Audit what gets ingested** — the daemon reads every session your installed
   agents write (Claude Code, jcode, Goose, Kimi). Secrets are redacted at
   ingest, before anything is embedded or stored, but redaction is
   pattern-based: run `memor scan` to audit an existing database and
   `memor scan --purge` to redact in place
5. **Redaction is pattern-based, not a guarantee** — it catches known key
   shapes and high-entropy tokens. A secret in an unusual format can survive,
   so treat the database as sensitive regardless

## Dependencies

Core dependencies are minimal and well-established:

| Dependency | Purpose | Notes |
|---|---|---|
| `sqlite-vec` | Vector search extension | C extension, pinned version |
| `numpy` | Vector operations | Widely audited |
| `typer` | CLI framework | No network access |
| `httpx` | HTTP client (API embedders/LLMs) | Only used when API endpoints configured |
| `model2vec` | Local embeddings | Static token vectors, no inference runtime; downloads the model once on first use |
| `anthropic` | Anthropic API client | Optional, only for LLM distillation |
