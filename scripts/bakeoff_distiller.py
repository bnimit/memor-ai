from __future__ import annotations
import argparse, time

CANDIDATES = [
    ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "*q4_k_m.gguf"),
    ("Qwen/Qwen3-4B-GGUF", "*q4_k_m.gguf"),
    ("bartowski/microsoft_Phi-4-mini-instruct-GGUF", "*Q4_K_M.gguf"),
]


def _sample_chunks(store, project, max_sessions=10):
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='session_chunk' AND project=?",
        (project,)).fetchall()
    by_session = {}
    for r in rows:
        a = store._row_to_artifact(r)
        by_session.setdefault(a.meta.get("session_id", "?"), []).append(a)
    return list(by_session.items())[:max_sessions]


def recall_latency_probe(store, embedder, project, *, k=8, max_queries=50):
    """Measure recall p50/p95 latency over the key_vectors path (use_keys=True).
    Uses the potion embedder only — no generative model. Feeds the Phase-A
    gate's 'recall p95 < 15ms' criterion."""
    import time
    from memor.retrieve.retriever import Retriever
    from memor.types import Scope
    rows = store.db.execute(
        "SELECT text FROM artifacts WHERE kind='session_chunk' AND project=? LIMIT ?",
        (project, max_queries)).fetchall()
    queries = [r["text"][:200] for r in rows]
    if not queries:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "n": 0}
    r = Retriever(store, embedder, k=k, use_keys=True)
    lat = []
    for q in queries:
        t0 = time.perf_counter()
        r.query(q, Scope(project=project))
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    return {"p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "n": len(lat)}


def evaluate_model(repo_id, filename, store, embedder, sessions):
    from memor.llm.llama_cpp import LlamaCppLLM
    from memor.distill.schema import build_prompt, parse_memories, GBNF_GRAMMAR
    from memor.tokencount import count_tokens
    llm = LlamaCppLLM(repo_id=repo_id, filename=filename)
    total = valid = 0
    val_tokens = []
    t0 = time.perf_counter()
    for sid, chunks in sessions:
        for c in chunks[:5]:
            total += 1
            raw = llm.complete(build_prompt(c.text, with_questions=False),
                               grammar=GBNF_GRAMMAR)
            mems = parse_memories(raw, with_questions=False)
            if mems:
                valid += 1
                val_tokens += [count_tokens(m["value"]) for m in mems]
    dt = (time.perf_counter() - t0)
    return {
        "model": repo_id,
        "valid_json_rate": round(valid / total, 3) if total else 0.0,
        "avg_value_tokens": round(sum(val_tokens) / len(val_tokens), 1) if val_tokens else 0,
        "avg_latency_s_per_chunk": round(dt / total, 2) if total else 0.0,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--max-sessions", type=int, default=10)
    args = ap.parse_args(argv)
    from memor.embed.local import LocalEmbedder
    from memor.store.sqlite_store import SqliteStore
    e = LocalEmbedder()
    s = SqliteStore(args.db, dim=e.dim)
    sessions = _sample_chunks(s, args.project, args.max_sessions)
    print(f"{'model':50} {'valid%':>8} {'val_tok':>8} {'s/chunk':>8}")
    for repo_id, filename in CANDIDATES:
        try:
            r = evaluate_model(repo_id, filename, s, e, sessions)
            print(f"{r['model']:50} {r['valid_json_rate']*100:>7.1f} "
                  f"{r['avg_value_tokens']:>8.1f} {r['avg_latency_s_per_chunk']:>8}")
        except Exception as ex:
            print(f"{repo_id:50}  ERROR: {ex}")
    probe = recall_latency_probe(s, e, args.project)
    print(f"\nrecall latency (key path, n={probe['n']}): "
          f"p50={probe['p50_ms']}ms p95={probe['p95_ms']}ms")


if __name__ == "__main__":
    main()
