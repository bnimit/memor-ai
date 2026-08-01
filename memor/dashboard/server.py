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
    _cached_store: SqliteStore | None = None

    def _store() -> SqliteStore:
        nonlocal _cached_store
        if _cached_store is None:
            _cached_store = SqliteStore(_db_path, dim=_get_dim(_db_path))
        return _cached_store

    @app.get("/", response_class=HTMLResponse)
    def index():
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text())
        return HTMLResponse("<h1>Memor Dashboard</h1><p>index.html not found</p>")

    @app.get("/api/summary")
    def summary():
        from memor.types import GLOBAL_PROJECT
        store = _store()
        recall_stats = store.get_recall_stats()
        ingestion = {}
        for row in store.db.execute(
            "SELECT kind, COUNT(*) as c, SUM(token_count) as tokens "
            "FROM artifacts WHERE active=1 GROUP BY kind"
        ).fetchall():
            ingestion[row["kind"]] = {"count": row["c"], "tokens": row["tokens"] or 0}
        project_count = store.db.execute(
            "SELECT COUNT(DISTINCT project) as c FROM artifacts"
        ).fetchone()["c"]
        global_count = store.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE active=1 AND project=? AND kind='memory'",
            (GLOBAL_PROJECT,)
        ).fetchone()["c"]
        recall_stats["ingestion"] = ingestion
        recall_stats["project_count"] = project_count
        recall_stats["global_memories"] = global_count
        return recall_stats

    @app.get("/api/projects")
    def projects():
        store = _store()
        recall_stats = store.get_project_stats()
        recall_projects = {r["project"] for r in recall_stats} if recall_stats else set()

        artifact_rows = store.db.execute("""
            SELECT project,
                   COUNT(*) as artifacts,
                   SUM(CASE WHEN kind='session_chunk' THEN 1 ELSE 0 END) as chunks,
                   SUM(CASE WHEN kind='memory' THEN 1 ELSE 0 END) as memories,
                   SUM(token_count) as total_tokens,
                   MAX(created_at) as last_activity
            FROM artifacts WHERE active=1
            GROUP BY project
            ORDER BY artifacts DESC
        """).fetchall()

        active_rows = [
            r for r in artifact_rows
            if (r["memories"] or 0) > 0 or r["project"] in recall_projects
        ]

        if not recall_stats:
            return [dict(r) for r in active_rows]

        result = list(recall_stats)
        for r in active_rows:
            if r["project"] not in recall_projects:
                result.append(dict(r))
        return result

    @app.get("/api/recalls")
    def recalls(limit: int = Query(50, ge=1, le=500),
                project: str | None = Query(None)):
        store = _store()
        return store.get_recent_recalls(limit=limit, project=project)

    @app.get("/api/quality")
    def quality():
        store = _store()
        rows = store.db.execute("""
            SELECT q.artifact_id, q.recall_count, q.use_count,
                   COALESCE(q.negative_count, 0) as negative_count,
                   q.quality_score,
                   a.project, a.kind, json_extract(a.meta, '$.mem_type') as mem_type,
                   substr(a.text, 1, 100) as preview
            FROM memory_quality q
            JOIN artifacts a ON a.id = q.artifact_id
            WHERE a.active = 1
            ORDER BY q.quality_score DESC
            LIMIT 50
        """).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/efficiency")
    def efficiency():
        store = _store()
        return store.get_efficiency_stats()

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

    @app.get("/api/session-efficiency")
    def session_efficiency():
        store = _store()
        return store.get_session_efficiency()

    @app.get("/api/recall-trend")
    def recall_trend(days: int = Query(30, ge=7, le=90)):
        store = _store()
        rows = store.db.execute("""
            SELECT date(timestamp, 'unixepoch', 'localtime') as day,
                   COUNT(*) as recalls,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as hits,
                   SUM(tokens_injected) as tokens,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_score
            FROM recall_log
            WHERE timestamp >= unixepoch('now', ? || ' days')
            GROUP BY day ORDER BY day
        """, (f"-{days}",)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/roi")
    def roi(project: str | None = Query(None)):
        store = _store()
        return store.get_token_roi(project=project)

    @app.get("/api/roi-trend")
    def roi_trend(project: str | None = Query(None)):
        store = _store()
        return store.get_roi_trend(project=project)

    @app.get("/api/eval/latest")
    def eval_latest(eval_type: str = Query("counterfactual")):
        store = _store()
        result = store.get_latest_eval(eval_type)
        if not result:
            return {"status": "no_runs"}
        return result

    @app.get("/api/agent-breakdown")
    def agent_breakdown():
        store = _store()
        return store.get_agent_breakdown()

    @app.get("/api/version")
    def version():
        from memor import __version__
        return {"version": __version__}

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

    @app.get("/api/savings-ledger")
    def savings_ledger(days: int = Query(30, ge=1, le=90)):
        import json
        import time as _time
        store = _store()
        cutoff = _time.time() - (days * 86400)
        
        # Summary
        summary = store.get_proxy_savings_summary(days)
        
        # Per-day series
        per_day = store.db.execute("""
            SELECT date(timestamp, 'unixepoch', 'localtime') as day,
                   SUM(tokens_before) as tokens_before,
                   SUM(tokens_after) as tokens_after
            FROM proxy_savings
            WHERE timestamp >= ?
            GROUP BY day
            ORDER BY day
        """, (cutoff,)).fetchall()
        
        # Content types breakdown
        content_type_rows = store.db.execute("""
            SELECT content_types
            FROM proxy_savings
            WHERE timestamp >= ?
        """, (cutoff,)).fetchall()
        
        ct_totals = {}
        for row in content_type_rows:
            if row["content_types"]:
                cts = json.loads(row["content_types"])
                for ct, data in cts.items():
                    if ct not in ct_totals:
                        ct_totals[ct] = {"before": 0, "after": 0}
                    ct_totals[ct]["before"] += data.get("before", 0)
                    ct_totals[ct]["after"] += data.get("after", 0)
        
        content_types = [
            {
                "content_type": ct,
                "tokens_before": data["before"],
                "tokens_after": data["after"],
                "pct_saved": round((1 - data["after"] / data["before"]) * 100, 1) if data["before"] > 0 else 0
            }
            for ct, data in ct_totals.items()
        ]
        
        return {
            "summary": summary,
            "per_day": [dict(r) for r in per_day],
            "content_types": content_types,
        }

    @app.get("/api/proxy-status")
    def proxy_status():
        import socket
        import subprocess
        from memor.config import load_config, proxy_port
        
        cfg = load_config()
        port = proxy_port()
        
        # Check proxy: TCP connect to 127.0.0.1:proxy_port or HTTP /health
        proxy_healthy = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(("127.0.0.1", port))
            proxy_healthy = (result == 0)
            sock.close()
        except Exception:
            pass
        
        # Check hook: ~/.memor/hook.sock exists
        hook_path = Path.home() / ".memor" / "hook.sock"
        hook_healthy = hook_path.exists()
        
        # Check daemon: use service status (launchctl/systemd) or log mtime fallback
        daemon_healthy = False
        try:
            import platform
            if platform.system() == "Darwin":
                # macOS: check launchctl
                label = "ai.memor.daemon"
                r = subprocess.run(
                    ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                    capture_output=True, text=True, timeout=2
                )
                daemon_healthy = (r.returncode == 0 and "pid =" in r.stdout)
            else:
                # Linux: check systemd
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", "memor-daemon"],
                    capture_output=True, text=True, timeout=2
                )
                daemon_healthy = (r.stdout.strip() == "active")
        except Exception:
            # Fallback: check daemon.log mtime (recently touched = running)
            import time as _time
            log_path = Path.home() / ".memor" / "daemon.log"
            if log_path.exists():
                mtime = log_path.stat().st_mtime
                daemon_healthy = (_time.time() - mtime < 300)  # touched in last 5 min
        
        return {
            "proxy": proxy_healthy,
            "hook": hook_healthy,
            "daemon": daemon_healthy,
            "proxy_agents": cfg.get("proxy_agents", {}),
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
