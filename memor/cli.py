from __future__ import annotations
import json
from pathlib import Path
import typer
from memor.store.sqlite_store import SqliteStore
from memor.retrieve.retriever import Retriever
from memor.types import Scope
from memor.ingest.claude_code import parse_transcript

app = typer.Typer(no_args_is_help=True)

def _embedder(fake: bool):
    if fake:
        from memor.embed.fake import FakeEmbedder
        return FakeEmbedder(dim=16)
    from memor.embed.local import LocalEmbedder
    return LocalEmbedder()

@app.command("ingest-cc")
def ingest_cc(path: str, project: str = typer.Option(...), db: str = "memor.db",
              fake: bool = False, no_filter: bool = False):
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    arts = parse_transcript(Path(path), project=project, filter_noise=not no_filter)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")

@app.command("query")
def query(text: str, project: str = typer.Option(None), db: str = "memor.db",
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
def eval_cmd(cases_path: str, db: str = "memor.db", k: int = 8, fake: bool = False):
    from memor.eval.dataset import EvalCase
    from memor.eval.runner import run_suite
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    raw = json.loads(Path(cases_path).read_text())
    cases = [EvalCase(query=c["query"], scope_project=c["project"],
                      relevant_ids=set(c["relevant_ids"]),
                      baseline_full_tokens=c["baseline_full_tokens"]) for c in raw]
    summary = run_suite(cases, store=s, embedder=e, k=k)
    typer.echo(json.dumps(summary, indent=2))
    s.save_eval_run({"k": k, "cases": cases_path}, summary)
    typer.echo("(eval run persisted)")

@app.command("build-cases")
def build_cases(project: str = typer.Option(...), db: str = "memor.db",
                out: str = "cases.json", fake: bool = False):
    from memor.eval.dataset import build_counterfactual_cases
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'", (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_counterfactual_cases(arts, project=project)
    Path(out).write_text(json.dumps([{"query":c.query,"project":c.scope_project,
        "relevant_ids":sorted(c.relevant_ids),"baseline_full_tokens":c.baseline_full_tokens} for c in cases], indent=2))
    typer.echo(f"wrote {len(cases)} cases to {out}")

@app.command("distill")
def distill(project: str = typer.Option(...), db: str = "memor.db",
            fake: bool = False, llm_provider: str = "anthropic", llm_model: str = "claude-sonnet-4-6"):
    from memor.distill.distiller import Distiller
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    if llm_provider == "anthropic":
        from memor.llm.anthropic import AnthropicLLM; llm = AnthropicLLM(model=llm_model)
    else:
        from memor.llm.openai_compat import OpenAICompatLLM
        import os
        llm = OpenAICompatLLM(base_url=os.environ.get("OPENAI_BASE_URL","http://localhost:11434/v1"),
                              api_key=os.environ.get("OPENAI_API_KEY",""), model=llm_model)
    d = Distiller(s, e, llm)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'", (project,)).fetchall()
    by_session: dict[str, list] = {}
    for r in rows:
        a = s._row_to_artifact(r)
        by_session.setdefault(a.meta.get("session_id","?"), []).append(a)
    total = 0
    for sid, chunks in by_session.items():
        chunks.sort(key=lambda a: a.meta.get("ord",0))
        ids = d.distill_session(sid, chunks, project=project)
        total += len(ids)
        typer.echo(f"  session {sid}: {len(ids)} memories")
    typer.echo(f"distilled {total} memories from {len(by_session)} sessions")


@app.command("ingest-project")
def ingest_project(project_dir: str, project: str = typer.Option(...),
                   db: str = "memor.db", fake: bool = False, no_filter: bool = False):
    """Recursively ingest all .jsonl transcripts (including subagent transcripts) from a Claude Code project directory."""
    e = _embedder(fake)
    s = SqliteStore(db, dim=e.dim)
    files = sorted(Path(project_dir).rglob("*.jsonl"))
    total = 0
    for f in files:
        arts = parse_transcript(f, project=project, filter_noise=not no_filter)
        if arts:
            s.add_artifacts(arts, e.embed([a.text for a in arts]))
            total += len(arts)
            typer.echo(f"  {f.name}: {len(arts)} chunks")
    typer.echo(f"ingested {total} chunks from {len(files)} files")

@app.command("ingest-doc")
def ingest_doc(path: str, project: str = typer.Option(...), kind: str = "note",
               db: str = "memor.db", fake: bool = False):
    from memor.ingest.documents import parse_document
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    arts = parse_document(Path(path), project=project, kind=kind)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")


@app.command("daemon")
def daemon(poll_interval: int = typer.Option(30, help="Seconds between polls"),
           projects_dir: str = typer.Option(None, help="Override ~/.claude/projects/")):
    """Run the auto-ingest daemon (foreground). Watches ~/.claude/projects/ for new transcripts."""
    from memor.daemon import run_daemon, CLAUDE_PROJECTS_DIR
    d = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    run_daemon(poll_interval=poll_interval, projects_dir=d)


if __name__ == "__main__":
    app()
