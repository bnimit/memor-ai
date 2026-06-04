"""Memor recall skill -- retrieves relevant context for an agent."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_DB = str(Path.home() / ".memor" / "memor.db")


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

    from memor.recall import recall
    threshold = 0.0 if a.fake else 0.05
    result = recall(a.query, a.project, a.db, embedder=embedder, k=a.k, threshold=threshold)
    print(result.formatted_context)


if __name__ == "__main__":
    main()
