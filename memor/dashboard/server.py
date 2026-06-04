from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from memor.store.sqlite_store import SqliteStore

STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | None = None) -> FastAPI:
    if db_path is None:
        db_path = str(Path.home() / ".memor" / "memor.db")

    app = FastAPI(title="Memor Dashboard")
    _db_path = db_path

    def _store() -> SqliteStore:
        return SqliteStore(_db_path, dim=_get_dim(_db_path))

    @app.get("/", response_class=HTMLResponse)
    def index():
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text())
        return HTMLResponse("<h1>Memor Dashboard</h1><p>index.html not found</p>")

    @app.get("/api/summary")
    def summary():
        store = _store()
        return store.get_recall_stats()

    @app.get("/api/projects")
    def projects():
        store = _store()
        return store.get_project_stats()

    @app.get("/api/recalls")
    def recalls(limit: int = Query(50, ge=1, le=500),
                project: str | None = Query(None)):
        store = _store()
        return store.get_recent_recalls(limit=limit, project=project)

    @app.get("/api/savings")
    def savings():
        store = _store()
        rows = store.db.execute("""
            SELECT project,
                   SUM(tokens_injected) as recalled_tokens,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_relevance
            FROM recall_log
            WHERE status IN ('ok', 'extractive_only')
            GROUP BY project
        """).fetchall()
        result = []
        for r in rows:
            project = r["project"]
            full_ctx = store.db.execute(
                "SELECT SUM(token_count) as total FROM artifacts WHERE project=? AND active=1",
                (project,)).fetchone()["total"] or 0
            recalled = r["recalled_tokens"] or 0
            result.append({
                "project": project,
                "recalled_tokens": recalled,
                "full_context_tokens": full_ctx,
                "reduction_pct": round((1 - recalled / full_ctx) * 100, 1) if full_ctx > 0 else 0,
                "avg_relevance": round(r["avg_relevance"] or 0, 3),
            })
        return result

    @app.get("/api/health")
    def health():
        store = _store()
        db_size = os.path.getsize(_db_path) if os.path.exists(_db_path) else 0
        counts = {}
        for row in store.db.execute(
            "SELECT kind, COUNT(*) as c FROM artifacts WHERE active=1 GROUP BY kind"
        ).fetchall():
            counts[row["kind"]] = row["c"]
        last_ingest = store.db.execute(
            "SELECT MAX(created_at) as t FROM artifacts"
        ).fetchone()["t"]
        dim_row = store.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        return {
            "onboarding_status": store.get_onboarding_status(),
            "db_size_bytes": db_size,
            "artifact_counts": counts,
            "last_ingest_timestamp": last_ingest,
            "embedder_dim": int(dim_row["value"]) if dim_row else None,
        }

    return app


def _get_dim(db_path: str) -> int:
    """Read dim from meta table, default to 1536 (OpenAI)."""
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
    return 1536
