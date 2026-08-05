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
                project: str | None = Query(None),
                agent: str | None = Query(None)):
        store = _store()
        return store.get_recent_recalls(limit=limit, project=project, agent=agent)

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
    def recall_trend(
        days: int = Query(30, ge=7, le=90),
        agent: str | None = Query(None),
    ):
        store = _store()
        return store.get_recall_trend(days=days, agent=agent)

    @app.get("/api/recall-worth")
    def recall_worth():
        """Episode-level accounting: does recall reduce work, or just add tokens?

        Parses transcripts on demand, so results are cached briefly to keep the
        dashboard responsive.
        """
        import time as _time

        cached = getattr(app.state, "_recall_worth", None)
        if cached and _time.time() - cached[0] < 300:
            return cached[1]
        try:
            from memor.episodes import scan_episodes, summarize

            summary = summarize(scan_episodes())
        except Exception as exc:  # never take the dashboard down for a metric
            summary = {
                "overall": {"verdict": "insufficient_data", "episodes": 0, "usable": 0},
                "by_project": {},
                "strata": [],
                "error": str(exc)[:200],
            }
        app.state._recall_worth = (_time.time(), summary)
        return summary

    @app.get("/api/compression")
    def compression():
        """Compression state, realized savings, and whether the bill moved.

        Deliberately leads with whether the experiment is actually live: a stale
        install or a missed service restart otherwise costs a week of silence
        before anyone notices nothing was being measured.
        """
        import time as _time

        from memor.compression_worth import (
            liveness,
            load_savings_rows,
            summarize_savings,
        )
        from memor.config import is_compress_older_turns, load_config

        cfg = load_config()
        started = cfg.get("compress_started_at")
        enabled = is_compress_older_turns()
        summary = summarize_savings(load_savings_rows(_db_path, days=30))
        since_summary = (
            summarize_savings(load_savings_rows(_db_path, since=float(started)))
            if started
            else None
        )

        by_type = summary.by_type
        payload = {
            "enabled": enabled,
            "started_at": started,
            "liveness": liveness(enabled, started, since_summary),
            "realized": {
                "requests": summary.requests,
                "passthrough_pct": round(summary.passthrough_pct, 1),
                "tokens_before": summary.tokens_before,
                "tokens_after": summary.tokens_after,
                "saved": summary.saved,
                "saved_pct": round(summary.realized_pct, 1),
                "scored": summary.scored,
                "by_type": by_type,
                # Proof the code path is firing at all, not just the log crusher.
                "code_payloads": sum(v for k, v in by_type.items() if k.startswith("code")),
            },
            "cost": None,
        }

        if started:
            cached = getattr(app.state, "_cost_compare", None)
            if cached and _time.time() - cached[0] < 300:
                payload["cost"] = cached[1]
            else:
                try:
                    from memor.episodes import compare_at, scan_episodes

                    cost = compare_at(scan_episodes(), float(started))
                    payload["cost"] = {
                        k: cost[k]
                        for k in ("verdict", "cost_delta_pct", "n_before", "n_after")
                    }
                except Exception as exc:
                    payload["cost"] = {"verdict": "insufficient_data", "error": str(exc)[:120]}
                app.state._cost_compare = (_time.time(), payload["cost"])
        return payload

    @app.get("/api/agent-desk")
    def agent_desk(agent: str = Query(..., min_length=1)):
        """Per-agent pane payload: stats + trends + recent recalls."""
        store = _store()
        stats = store.get_agent_stats(agent)
        return {
            "stats": stats,
            "recall_trend": store.get_recall_trend(days=30, agent=agent),
            "savings_series": store.get_proxy_savings_series(days=30, agent=agent),
            "savings_summary": store.get_proxy_savings_summary(days=30, agent=agent),
            "recalls": store.get_recent_recalls(limit=25, agent=agent),
        }

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
    def savings_ledger(
        days: int = Query(30, ge=1, le=90),
        agent: str | None = Query(None),
    ):
        import json
        import time as _time
        store = _store()
        cutoff = _time.time() - (days * 86400)

        summary = store.get_proxy_savings_summary(days, agent=agent)
        series = store.get_proxy_savings_series(days, agent=agent)

        ct_clauses = ["timestamp >= ?"]
        ct_params: list = [cutoff]
        if agent:
            ct_clauses.append("agent=?")
            ct_params.append(agent)
        ct_where = " AND ".join(ct_clauses)
        content_type_rows = store.db.execute(
            f"SELECT content_types FROM proxy_savings WHERE {ct_where}",
            ct_params,
        ).fetchall()

        # The proxy pipeline writes {content_type: payloads_compressed}; the
        # ledger has no per-type token split, so we report compressed counts.
        ct_totals: dict[str, int] = {}
        for row in content_type_rows:
            if not row["content_types"]:
                continue
            try:
                cts = json.loads(row["content_types"])
            except (TypeError, ValueError):
                continue
            if not isinstance(cts, dict):
                continue
            for ct, count in cts.items():
                if isinstance(count, (int, float)):
                    ct_totals[ct] = ct_totals.get(ct, 0) + int(count)

        content_types = [
            {"content_type": ct, "count": count}
            for ct, count in sorted(ct_totals.items(), key=lambda kv: -kv[1])
        ]

        return {
            "summary": summary,
            "per_day": [
                {
                    "day": r["day"],
                    "tokens_before": r["tokens_before"],
                    "tokens_after": r["tokens_after"],
                    "tokens_saved": r["tokens_saved"],
                    "cumulative_saved": r["cumulative_saved"],
                }
                for r in series
            ],
            "content_types": content_types,
        }

    @app.get("/api/proxy-savings-by-agent")
    def proxy_savings_by_agent(days: int = Query(30, ge=1, le=90)):
        store = _store()
        return {"agents": store.get_proxy_savings_by_agent(days)}

    @app.get("/api/proxy-status")
    def proxy_status():
        import socket
        import subprocess
        from memor.config import load_config, proxy_port
        
        cfg = load_config()
        port = proxy_port()
        
        # Check proxy: TCP connect to 127.0.0.1:proxy_port or HTTP /health
        proxy_healthy = False
        proxy_mode = None
        compressor_ready = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(("127.0.0.1", port))
            proxy_healthy = (result == 0)
            sock.close()
        except Exception:
            pass

        if proxy_healthy:
            try:
                import json
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health", method="GET")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                    if health.get("ok"):
                        proxy_mode = health.get("mode")
                        compressor_ready = health.get("compressor_ready")
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
        
        result = {
            "proxy": proxy_healthy,
            "hook": hook_healthy,
            "daemon": daemon_healthy,
            "proxy_agents": cfg.get("proxy_agents", {}),
        }
        if proxy_mode is not None:
            result["mode"] = proxy_mode
        if compressor_ready is not None:
            result["compressor_ready"] = compressor_ready
        return result

    return app


def _get_dim(db_path: str) -> int:
    """Read dim from meta table, default to 1536 (OpenAI)."""
    from memor.store.sqlite_store import read_dim
    return read_dim(db_path, 1536)
