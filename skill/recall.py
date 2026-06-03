"""Memor recall skill -- retrieves relevant context for an agent."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = str(Path.home() / ".memor" / "memor.db")


def _format_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _format_hits(hits, project: str) -> str:
    """Format hits into an agent-readable context block."""
    lines = [f"## Recalled Context (project: {project})", ""]
    for i, h in enumerate(hits, 1):
        a = h.artifact
        kind_tag = a.kind
        # Truncate very long texts to keep output focused
        text = a.text if len(a.text) <= 600 else a.text[:600] + "..."
        source_parts = []
        session_id = a.meta.get("session_id")
        if session_id:
            source_parts.append(f"session {session_id[:8]}")
        source_parts.append(_format_timestamp(a.created_at))
        source = ", ".join(source_parts)

        lines.append(f"### {i}. [{kind_tag}] {text}")
        lines.append(f"Source: {source} | score: {h.score:.3f}")
        lines.append("")
    return "\n".join(lines)


def _format_trace(hits, latency_ms: float) -> str:
    """Format a one-line trace summary."""
    total_tokens = sum(h.artifact.token_count for h in hits)
    return f"Trace: {len(hits)} hits, {latency_ms:.0f}ms, {total_tokens} tokens recalled"


def main():
    p = argparse.ArgumentParser(description="Recall relevant context from Memor")
    p.add_argument("--query", required=True, help="Question or task to recall context for")
    p.add_argument("--project", required=True, help="Project name to scope retrieval")
    p.add_argument("--db", default=DEFAULT_DB, help=f"Path to memory DB (default: {DEFAULT_DB})")
    p.add_argument("--k", type=int, default=8, help="Number of hits (default: 8)")
    p.add_argument("--fake", action="store_true", help="Use fake embedder (testing only)")
    a = p.parse_args()

    db_path = Path(a.db)
    if not db_path.exists():
        print(f"ERROR: Database not found at {a.db}")
        print()
        print("The memor memory database has not been created yet.")
        print("Run the auto-ingest daemon to populate it:")
        print()
        print("  memor daemon")
        print()
        print("This will watch ~/.claude/projects/ and ingest session transcripts.")
        sys.exit(1)

    if a.fake:
        from memor.embed.fake import FakeEmbedder
        embedder = FakeEmbedder(dim=16)
    else:
        from memor.embed.local import LocalEmbedder
        embedder = LocalEmbedder()

    from memor.store.sqlite_store import SqliteStore
    from memor.retrieve.retriever import Retriever
    from memor.types import Scope

    store = SqliteStore(a.db, dim=embedder.dim)
    trace = Retriever(store, embedder, k=a.k).query(a.query, Scope(project=a.project))

    if not trace.hits:
        print(f"## Recalled Context (project: {a.project})")
        print()
        print("No relevant context found. The memory store may be empty for this project,")
        print("or the query did not match any stored artifacts.")
        print()
        print(f"---\nTrace: 0 hits, {trace.latency_ms:.0f}ms")
        sys.exit(0)

    print(_format_hits(trace.hits, a.project))
    print("---")
    print(_format_trace(trace.hits, trace.latency_ms))


if __name__ == "__main__":
    main()
