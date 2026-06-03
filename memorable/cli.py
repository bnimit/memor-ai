from __future__ import annotations
import json
from pathlib import Path
import typer
from memorable.store.sqlite_store import SqliteStore
from memorable.retrieve.retriever import Retriever
from memorable.types import Scope
from memorable.ingest.claude_code import parse_transcript

app = typer.Typer(no_args_is_help=True)

def _embedder(fake: bool):
    if fake:
        from memorable.embed.fake import FakeEmbedder
        return FakeEmbedder(dim=16)
    from memorable.embed.local import LocalEmbedder
    return LocalEmbedder()

@app.command("ingest-cc")
def ingest_cc(path: str, project: str = typer.Option(...), db: str = "memorable.db", fake: bool = False):
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    arts = parse_transcript(Path(path), project=project)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")

@app.command("query")
def query(text: str, project: str = typer.Option(None), db: str = "memorable.db",
          k: int = 8, fake: bool = False):
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    r = Retriever(s, e, k=k)
    trace = r.query(text, Scope(project=project))
    for h in trace.hits:
        typer.echo(f"[{h.score:.3f}] {h.artifact.id} :: {h.artifact.text[:100]}")
    typer.echo(f"-- {len(trace.hits)} hits, {trace.latency_ms:.1f}ms, "
               f"{sum(h.artifact.token_count for h in trace.hits)} tokens")

@app.command("eval")
def eval_cmd(cases_path: str, db: str = "memorable.db", k: int = 8, fake: bool = False):
    from memorable.eval.dataset import EvalCase
    from memorable.eval.runner import run_suite
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    raw = json.loads(Path(cases_path).read_text())
    cases = [EvalCase(query=c["query"], scope_project=c["project"],
                      relevant_ids=set(c["relevant_ids"]),
                      baseline_full_tokens=c["baseline_full_tokens"]) for c in raw]
    summary = run_suite(cases, store=s, embedder=e, k=k)
    typer.echo(json.dumps(summary, indent=2))

if __name__ == "__main__":
    app()
