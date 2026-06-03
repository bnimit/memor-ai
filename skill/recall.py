from __future__ import annotations
import argparse, json
from memorable.store.sqlite_store import SqliteStore
from memorable.retrieve.retriever import Retriever
from memorable.types import Scope

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--db", default="memorable.db")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--fake", action="store_true")
    a = p.parse_args()
    if a.fake:
        from memorable.embed.fake import FakeEmbedder; e = FakeEmbedder(dim=16)
    else:
        from memorable.embed.local import LocalEmbedder; e = LocalEmbedder()
    s = SqliteStore(a.db, dim=e.dim)
    trace = Retriever(s, e, k=a.k).query(a.query, Scope(project=a.project))
    context = "\n\n".join(f"- ({h.artifact.kind}) {h.artifact.text}" for h in trace.hits)
    print(json.dumps({
        "context": context,
        "trace": {"hits": len(trace.hits), "latency_ms": round(trace.latency_ms,1),
                  "tokens": sum(h.artifact.token_count for h in trace.hits),
                  "scored": [{"id":h.artifact.id,"score":round(h.score,3),
                              "components":h.components} for h in trace.hits]}}))

if __name__ == "__main__":
    main()
