from __future__ import annotations
import json
import sys
from pathlib import Path
import typer
from memor.store.sqlite_store import SqliteStore
from memor.retrieve.retriever import Retriever
from memor.types import Scope
from memor.ingest.claude_code import parse_transcript

app = typer.Typer(no_args_is_help=True)

def _db_path(db: str) -> str:
    return str(Path(db).expanduser())

def _get_dim(db_path: str) -> int:
    import sqlite3
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        db.close()
        if row:
            return int(row["value"])
    except Exception:
        pass
    return 256

def _embedder(fake: bool):
    if fake:
        from memor.embed.fake import FakeEmbedder
        return FakeEmbedder(dim=16)
    return _auto_embedder()


def _auto_embedder():
    """Local ONNX embedder by default. No API key needed for search."""
    from memor.embed.local import LocalEmbedder
    return LocalEmbedder()

HELP_TEXT = """\
memor — measured memory for coding agents

GETTING STARTED
  memor install-hook     Install the Claude Code recall hook + download model
  memor daemon           Start the background watcher (ingests + distills)
  memor dashboard        Open the web dashboard at localhost:8420

QUERYING
  memor query <text>     Search memories from the command line
    --project <name>     Scope to a specific project
    --k <n>              Number of results (default: 8)

INGESTION
  memor ingest-cc <file>           Ingest a single transcript
  memor ingest-project <dir>       Bulk ingest a project's transcripts
    --project <name>               Project name (required)
  memor ingest-doc <file>          Ingest a markdown document
    --project <name>               Project name (required)

MAINTENANCE
  memor reingest                   Wipe DB and re-ingest everything
  memor reingest --project <name>  Re-ingest only one project
  memor distill --project <name>   Run distillation manually
  memor forget-stale               Deactivate memories not recalled in 30 days
  memor scan                       Audit DB for leaked secrets
  memor scan --purge               Redact secrets in place
  memor setup-model                Download/retry the embedding model (~60MB)

EVALUATION
  memor eval <cases.json>          Run eval suite
  memor eval-judge --project <name>  LLM-as-judge evaluation
  memor bench-embed --project <name> Compare embedding models

CONFIGURATION
  Everything works locally with zero API keys.
  No configuration needed — just install, hook, and run the daemon.

EXAMPLES
  memor install-hook && memor daemon    # one-time setup
  memor query "auth flow" --project my-app
  memor reingest --project my-app -y    # refresh one project
  memor dashboard                       # see stats at localhost:8420
"""


@app.command("help")
def help_cmd():
    """Print the memor manual."""
    typer.echo(HELP_TEXT)


@app.command("setup-model")
def setup_model():
    """Download the embedding model (~60MB). Re-run to retry a failed download."""
    typer.echo("Downloading embedding model...")
    try:
        from memor.embed.local import LocalEmbedder
        embedder = LocalEmbedder()
        typer.echo(f"Model ready: potion-base-8M (dim={embedder.dim})")
    except Exception as e:
        typer.echo(f"Download failed: {e}")
        typer.echo("Check your internet connection and try again: memor setup-model")
        raise typer.Exit(1)


@app.command("ingest-cc")
def ingest_cc(path: str, project: str = typer.Option(...), db: str = "memor.db",
              fake: bool = False, no_filter: bool = False):
    e = _embedder(fake)
    s = SqliteStore(_db_path(db), dim=e.dim)
    arts = parse_transcript(Path(path), project=project, filter_noise=not no_filter)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")

@app.command("query")
def query(text: str, project: str = typer.Option(None), db: str = "memor.db",
          k: int = 8, fake: bool = False):
    e = _embedder(fake)
    s = SqliteStore(_db_path(db), dim=e.dim)
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
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
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
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'", (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_counterfactual_cases(arts, project=project)
    Path(out).write_text(json.dumps([{"query":c.query,"project":c.scope_project,
        "relevant_ids":sorted(c.relevant_ids),"baseline_full_tokens":c.baseline_full_tokens} for c in cases], indent=2))
    typer.echo(f"wrote {len(cases)} cases to {out}")

@app.command("eval-judge")
def eval_judge_cmd(project: str = typer.Option(...), db: str = "memor.db",
                   k: int = 8, fake: bool = False,
                   llm_provider: str = "anthropic", llm_model: str = "claude-sonnet-4-6",
                   holdout: int = 2):
    """Run LLM-as-judge eval: measures whether recalled context is actually useful."""
    from memor.eval.judge import build_judge_cases, run_judge_suite
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'",
                        (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_judge_cases(arts, project=project, holdout_turns=holdout)
    if not cases:
        typer.echo("No judge cases could be built — need at least 2 sessions with >= 4 turns each.")
        raise typer.Exit(1)
    typer.echo(f"Built {len(cases)} judge cases. Running evaluation...")
    if llm_provider == "anthropic":
        from memor.llm.anthropic import AnthropicLLM
        llm = AnthropicLLM(model=llm_model)
    else:
        from memor.llm.openai_compat import OpenAICompatLLM
        import os
        llm = OpenAICompatLLM(base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
                              api_key=os.environ.get("OPENAI_API_KEY", ""), model=llm_model)
    summary = run_judge_suite(cases, store=s, embedder=e, llm=llm, k=k)
    typer.echo(json.dumps(summary, indent=2))
    s.save_eval_run({"type": "judge", "k": k, "project": project, "holdout": holdout}, summary)
    typer.echo(f"Judge eval complete: mean relevance = {summary['mean_relevance']:.3f}")


@app.command("bench-embed")
def bench_embed(project: str = typer.Option(...), db: str = "memor.db",
                k: int = 8, fake: bool = False):
    """Benchmark multiple embedding models on your data. Compares recall@k, nDCG@k, and latency."""
    from memor.eval.embed_benchmark import run_embed_benchmark, CANDIDATE_MODELS
    from memor.eval.dataset import build_counterfactual_cases
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'",
                        (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_counterfactual_cases(arts, project=project)
    if not cases:
        typer.echo("No eval cases could be built.")
        raise typer.Exit(1)
    typer.echo(f"Running benchmark on {len(arts)} artifacts, {len(cases)} cases...")
    if fake:
        from memor.embed.fake import FakeEmbedder
        results = run_embed_benchmark(arts, cases, model_specs=[{"name":"fake","model_name":"fake"}],
                                      embedder_factory=lambda _: FakeEmbedder(dim=16),
                                      db_dir=str(Path(db).parent), k=k)
    else:
        results = run_embed_benchmark(arts, cases, db_dir=str(Path(db).parent), k=k)
    typer.echo(f"\n{'Model':<30} {'Dim':>5} {'Recall@k':>10} {'nDCG@k':>10} {'Embed ms':>10} {'Query ms':>10}")
    typer.echo("-" * 80)
    for r in results:
        typer.echo(f"{r.model_name:<30} {r.dim:>5} {r.recall_at_k:>10.3f} {r.ndcg_at_k:>10.3f} "
                   f"{r.embed_latency_ms:>10.1f} {r.retrieval_latency_ms:>10.1f}")


@app.command("distill")
def distill(project: str = typer.Option(...), db: str = "memor.db",
            fake: bool = False, llm_provider: str = "anthropic", llm_model: str = "claude-sonnet-4-6"):
    from memor.distill.distiller import Distiller
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
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
    s = SqliteStore(_db_path(db), dim=e.dim)
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
    e = _embedder(fake); s = SqliteStore(_db_path(db), dim=e.dim)
    arts = parse_document(Path(path), project=project, kind=kind)
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    typer.echo(f"ingested {len(arts)} chunks from {path}")


@app.command("reingest")
def reingest(project: str = typer.Option(None, help="Only reingest a specific project"),
             projects_dir: str = typer.Option(None, help="Override ~/.claude/projects/"),
             confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation")):
    """Re-ingest transcripts. Without --project, wipes the entire DB and starts fresh.
    With --project, only clears and re-ingests that project's data."""
    from memor.daemon import (
        CLAUDE_PROJECTS_DIR, DEFAULT_DB, STATE_FILE, DISTILLED_FILE,
        run_poll_cycle, scan_transcripts, ingest_file, _make_embedder,
        save_state, save_distilled_state, load_state, load_distilled_state,
    )
    d = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR

    if project:
        if not confirm:
            typer.confirm(
                f'This will clear all data for project "{project}" and re-ingest. Continue?',
                abort=True)
        db_path = str(DEFAULT_DB)
        if not DEFAULT_DB.exists():
            typer.echo("No database found. Run 'memor daemon' first.")
            raise typer.Exit(1)
        embedder = _make_embedder()
        store = SqliteStore(db_path, dim=embedder.dim)
        deleted = store.db.execute(
            "DELETE FROM artifacts WHERE project=?", (project,)).rowcount
        store.db.commit()
        typer.echo(f"  cleared {deleted} artifacts for project '{project}'")
        state = load_state()
        transcripts = scan_transcripts(d)
        count = 0
        for path, proj_name in transcripts:
            if proj_name == project:
                n = ingest_file(path, proj_name, store, embedder)
                state[str(path)] = path.stat().st_mtime
                count += n
        save_state(state)
        distilled = load_distilled_state()
        distilled -= {sid for sid in distilled if True}
        save_distilled_state(distilled)
        typer.echo(f"Done: re-ingested {count} chunks for project '{project}'")
    else:
        if not confirm:
            typer.confirm(
                f"This will delete {DEFAULT_DB} and re-ingest all transcripts. Continue?",
                abort=True)
        for f in [DEFAULT_DB, STATE_FILE, DISTILLED_FILE]:
            if f.exists():
                f.unlink()
                typer.echo(f"  deleted {f}")
        embedder = _make_embedder()
        store = SqliteStore(str(DEFAULT_DB), dim=embedder.dim)
        typer.echo(f"Re-ingesting from {d}...")
        state, distilled = run_poll_cycle({}, store, embedder, d)
        save_state(state)
        save_distilled_state(distilled)
        chunks = store.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='session_chunk' AND active=1"
        ).fetchone()["c"]
        memories = store.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1"
        ).fetchone()["c"]
        typer.echo(f"Done: {chunks} chunks, {memories} memories")


@app.command("forget-stale")
def forget_stale(days: int = typer.Option(30, help="Deactivate memories not recalled in this many days"),
                 db: str = typer.Option(str(Path.home() / ".memor" / "memor.db")),
                 confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation")):
    """Deactivate memories that haven't been recalled in N days."""
    db_path = _db_path(db)
    if not Path(db_path).exists():
        typer.echo("No database found.")
        raise typer.Exit(1)
    s = SqliteStore(db_path, dim=_get_dim(db_path))
    stale = s.get_stale_memories(days)
    if not stale:
        typer.echo("No stale memories found.")
        return
    if not confirm:
        typer.confirm(f"Deactivate {len(stale)} memories not recalled in {days} days?", abort=True)
    count = s.deactivate_stale(days)
    typer.echo(f"Deactivated {count} stale memories.")


@app.command("scan")
def scan(db: str = typer.Option(str(Path.home() / ".memor" / "memor.db")),
         purge: bool = typer.Option(False, "--purge", help="Redact secrets in place"),
         confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation")):
    """Scan the memory DB for secrets (API keys, tokens, connection strings)."""
    from memor.redact import scan_artifacts, purge_secrets_from_db
    db_path = _db_path(db)
    if not Path(db_path).exists():
        typer.echo("No database found.")
        raise typer.Exit(1)
    s = SqliteStore(db_path, dim=_get_dim(db_path))
    findings = scan_artifacts(s)
    if not findings:
        typer.echo("No secrets detected in the memory store.")
        return
    typer.echo(f"Found potential secrets in {len(findings)} artifacts:")
    for f in findings[:20]:
        types = ", ".join(name for name, _ in f["secrets"])
        typer.echo(f"  [{f['kind']}] {f['artifact_id'][:30]} ({f['project']}) — {types}")
    if len(findings) > 20:
        typer.echo(f"  ... and {len(findings) - 20} more")
    if purge:
        if not confirm:
            typer.confirm(f"Redact secrets in {len(findings)} artifacts?", abort=True)
        count = purge_secrets_from_db(s)
        typer.echo(f"Redacted secrets in {count} artifacts.")
    else:
        typer.echo("Run with --purge to redact these secrets in place.")


@app.command("daemon")
def daemon(poll_interval: int = typer.Option(30, help="Seconds between polls"),
           projects_dir: str = typer.Option(None, help="Override ~/.claude/projects/")):
    """Run the auto-ingest daemon (foreground). Watches ~/.claude/projects/ for new transcripts."""
    from memor.daemon import run_daemon, CLAUDE_PROJECTS_DIR
    d = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    run_daemon(poll_interval=poll_interval, projects_dir=d)


def _install_hook_logic(settings_path: Path, hook_command: str) -> None:
    """Core logic for install-hook, separated for testing."""
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    hook_cmd = {"type": "command", "command": hook_command, "timeout": 5000}
    entry = {"matcher": "", "hooks": [hook_cmd]}
    existing_idx = None
    for i, group in enumerate(prompt_hooks):
        for h in group.get("hooks", []):
            if "memor-hook" in h.get("command", ""):
                existing_idx = i
                break
        if existing_idx is not None:
            break
    if existing_idx is not None:
        prompt_hooks[existing_idx] = entry
    else:
        prompt_hooks.append(entry)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2))


@app.command("install-hook")
def install_hook():
    """Install the Claude Code recall hook into ~/.claude/settings.json."""
    import shutil
    hook_bin = shutil.which("memor-hook")
    if not hook_bin:
        typer.echo("Error: 'memor-hook' not found on PATH.", err=True)
        typer.echo("  If you installed with pipx: pipx install memor-cli", err=True)
        typer.echo("  If developing locally: pip install -e .", err=True)
        raise typer.Exit(1)
    settings_path = Path.home() / ".claude" / "settings.json"
    _install_hook_logic(settings_path, hook_bin)
    typer.echo(f"Hook installed: {hook_bin}")
    typer.echo(f"Settings updated: {settings_path}")
    typer.echo()
    typer.echo("Pre-downloading embedding model...")
    try:
        from memor.embed.local import LocalEmbedder
        embedder = LocalEmbedder()
        typer.echo(f"  model ready (dim={embedder.dim})")
    except Exception as e:
        typer.echo(f"  model download failed: {e}")
        typer.echo("  retry later with: memor setup-model")
    typer.echo()
    typer.echo("Next steps:")
    typer.echo("  1. Start the daemon: memor daemon")
    typer.echo("  2. Open the dashboard: memor dashboard")
    typer.echo("  Everything works locally — no API keys needed.")


@app.command("dashboard")
def dashboard(port: int = typer.Option(8420, help="Port to serve on"),
              no_open: bool = typer.Option(False, help="Don't open browser"),
              db: str = typer.Option(str(Path.home() / ".memor" / "memor.db"))):
    """Launch the web dashboard."""
    import uvicorn
    from memor.dashboard.server import create_app
    db_resolved = _db_path(db)
    if not Path(db_resolved).exists():
        typer.echo(f"Database not found at {db_resolved}")
        typer.echo("Run 'memor daemon' first to create and populate the database.")
        raise typer.Exit(1)
    app_instance = create_app(db_resolved)
    if not no_open:
        import webbrowser
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    typer.echo(f"Memor dashboard: http://localhost:{port}")
    uvicorn.run(app_instance, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
