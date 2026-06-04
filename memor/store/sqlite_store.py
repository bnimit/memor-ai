from __future__ import annotations
import json, sqlite3, struct
from pathlib import Path
import sqlite_vec
from memor.types import Artifact, Scope

def _serialize(v: list[float]) -> bytes:
    return struct.pack("%sf" % len(v), *v)

class SqliteStore:
    def __init__(self, path: str, dim: int):
        self.dim = dim
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._init_schema()
        self._check_dim(dim)

    def _init_schema(self):
        self.db.executescript(f"""
        CREATE TABLE IF NOT EXISTS artifacts(
          id TEXT PRIMARY KEY, kind TEXT, project TEXT, source TEXT,
          text TEXT, token_count INTEGER, created_at REAL, meta TEXT,
          active INTEGER DEFAULT 1, superseded_by TEXT);
        CREATE TABLE IF NOT EXISTS edges(
          src_id TEXT, dst_id TEXT, type TEXT,
          PRIMARY KEY(src_id, dst_id, type));
        CREATE INDEX IF NOT EXISTS idx_art_project ON artifacts(project, active);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_artifacts USING vec0(
          embedding float[{self.dim}]);
        CREATE TABLE IF NOT EXISTS eval_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, config TEXT, metrics TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS recall_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp REAL, project TEXT, query_preview TEXT,
          hits_count INTEGER, top_score REAL, tokens_injected INTEGER,
          latency_ms REAL, status TEXT, session_id TEXT);
        """)
        self.db.commit()

    def _check_dim(self, dim: int):
        row = self.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO meta(key, value) VALUES('dim', ?)", (str(dim),))
            self.db.commit()
        elif int(row["value"]) != dim:
            raise SystemExit(
                f"Embedding dimension mismatch: database was created with dim={row['value']} "
                f"but current embedder has dim={dim}. Use the same embedder or re-ingest."
            )

    def add_artifacts(self, artifacts: list[Artifact], vectors: list[list[float]]) -> None:
        cur = self.db.cursor()
        for a, v in zip(artifacts, vectors):
            cur.execute(
              "INSERT OR REPLACE INTO artifacts(id,kind,project,source,text,token_count,created_at,meta,active,superseded_by)"
              " VALUES(?,?,?,?,?,?,?,?,1,NULL)",
              (a.id, a.kind, a.project, a.source, a.text, a.token_count, a.created_at, json.dumps(a.meta)))
            rowid = cur.execute("SELECT rowid FROM artifacts WHERE id=?", (a.id,)).fetchone()[0]
            cur.execute("INSERT OR REPLACE INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(v)))
        self.db.commit()

    def add_edge(self, src_id: str, dst_id: str, type: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO edges(src_id,dst_id,type) VALUES(?,?,?)",
                        (src_id, dst_id, type))
        self.db.commit()

    def _row_to_artifact(self, r) -> Artifact:
        return Artifact(id=r["id"], kind=r["kind"], project=r["project"], source=r["source"],
                        text=r["text"], token_count=r["token_count"], created_at=r["created_at"],
                        meta=json.loads(r["meta"]))

    def search(self, vector: list[float], scope: Scope, k: int) -> list[tuple[Artifact, float]]:
        rows = self.db.execute(f"""
          SELECT a.*, v.distance AS distance
          FROM (SELECT rowid, distance FROM vec_artifacts
                WHERE embedding MATCH ? AND k = ?) v
          JOIN artifacts a ON a.rowid = v.rowid
          WHERE a.active = 1
            AND (? IS NULL OR a.project = ?)
            AND (? IS NULL OR a.created_at >= ?)
            AND (? IS NULL OR a.created_at <= ?)
          ORDER BY v.distance ASC
        """, (_serialize(vector), max(k*20, 200),
              scope.project, scope.project,
              scope.since, scope.since,
              scope.until, scope.until)).fetchall()
        out = []
        for r in rows[:k]:
            if scope.kinds is not None and r["kind"] not in scope.kinds:
                continue
            sim = 1.0 - float(r["distance"])
            out.append((self._row_to_artifact(r), sim))
        return out

    def neighbors(self, ids: list[str], types: list[str], hops: int = 1) -> list[Artifact]:
        qmarks_ids = ",".join("?" * len(ids))
        qmarks_types = ",".join("?" * len(types))
        rows = self.db.execute(f"""
          WITH RECURSIVE walk(id, depth) AS (
            SELECT dst_id, 1 FROM edges
              WHERE src_id IN ({qmarks_ids}) AND type IN ({qmarks_types})
            UNION
            SELECT e.dst_id, w.depth+1 FROM edges e JOIN walk w ON e.src_id = w.id
              WHERE w.depth < ? AND e.type IN ({qmarks_types})
          )
          SELECT DISTINCT a.* FROM artifacts a JOIN walk ON a.id = walk.id
          WHERE a.active = 1
        """, (*ids, *types, hops, *types)).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def deactivate(self, artifact_id: str, superseded_by: str) -> None:
        self.db.execute("UPDATE artifacts SET active=0, superseded_by=? WHERE id=?",
                        (superseded_by, artifact_id))
        self.add_edge(superseded_by, artifact_id, "supersedes")

    def recent(self, scope: Scope, k: int) -> list[Artifact]:
        """Return the k most recent active artifacts matching scope, ordered by created_at DESC."""
        rows = self.db.execute("""
          SELECT * FROM artifacts WHERE active = 1
            AND (? IS NULL OR project = ?)
            AND (? IS NULL OR created_at >= ?)
            AND (? IS NULL OR created_at <= ?)
          ORDER BY created_at DESC LIMIT ?
        """, (scope.project, scope.project, scope.since, scope.since,
              scope.until, scope.until, k)).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def save_eval_run(self, config: dict, metrics: dict) -> int:
        import time
        cur = self.db.execute("INSERT INTO eval_runs(created_at, config, metrics) VALUES(?,?,?)",
                              (time.time(), json.dumps(config), json.dumps(metrics)))
        self.db.commit()
        return cur.lastrowid

    def log_recall(self, project: str, query_preview: str, hits_count: int,
                   top_score: float, tokens_injected: int, latency_ms: float,
                   status: str, session_id: str = "") -> None:
        import time as _time
        self.db.execute(
            "INSERT INTO recall_log(timestamp,project,query_preview,hits_count,"
            "top_score,tokens_injected,latency_ms,status,session_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (_time.time(), project, query_preview[:100], hits_count, top_score,
             tokens_injected, latency_ms, status, session_id))
        self.db.commit()

    def get_recall_stats(self) -> dict:
        r = self.db.execute("""
            SELECT COUNT(*) as total,
                   SUM(tokens_injected) as tokens,
                   AVG(latency_ms) as avg_latency,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as with_hits
            FROM recall_log
        """).fetchone()
        total = r["total"] or 0
        return {
            "total_recalls": total,
            "total_tokens": r["tokens"] or 0,
            "avg_latency_ms": round(r["avg_latency"] or 0, 1),
            "hit_rate": round((r["with_hits"] or 0) / total, 3) if total > 0 else 0,
        }

    def get_project_stats(self) -> list[dict]:
        rows = self.db.execute("""
            SELECT project,
                   COUNT(*) as recalls,
                   SUM(tokens_injected) as tokens,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_score,
                   SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_count,
                   SUM(CASE WHEN status='no_hits' THEN 1 ELSE 0 END) as no_hits_count,
                   SUM(CASE WHEN status='extractive_only' THEN 1 ELSE 0 END) as extractive_count
            FROM recall_log
            GROUP BY project
            ORDER BY recalls DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_recent_recalls(self, limit: int = 50, project: str | None = None) -> list[dict]:
        if project:
            rows = self.db.execute(
                "SELECT * FROM recall_log WHERE project=? ORDER BY timestamp DESC LIMIT ?",
                (project, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM recall_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_efficiency_stats(self, context_window: int = 200_000) -> dict:
        sessions = self.db.execute("""
            SELECT session_id,
                   COUNT(*) as prompts,
                   SUM(tokens_injected) as total_injected,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as prompts_with_hits,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_quality,
                   MIN(timestamp) as first_recall,
                   MAX(timestamp) as last_recall
            FROM recall_log
            WHERE session_id != ''
            GROUP BY session_id
            ORDER BY last_recall DESC
            LIMIT 50
        """).fetchall()
        session_list = []
        for s in sessions:
            injected = s["total_injected"] or 0
            prompts = s["prompts"] or 1
            precision = (s["prompts_with_hits"] or 0) / prompts
            overhead_pct = round(injected / context_window * 100, 2)
            session_list.append({
                "session_id": s["session_id"],
                "prompts": prompts,
                "tokens_injected": injected,
                "overhead_pct": overhead_pct,
                "precision": round(precision, 3),
                "avg_quality": round(s["avg_quality"] or 0, 3),
                "first_recall": s["first_recall"],
                "last_recall": s["last_recall"],
            })

        totals = self.db.execute("""
            SELECT COUNT(*) as total_recalls,
                   SUM(tokens_injected) as total_injected,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as with_hits,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_quality,
                   COUNT(DISTINCT session_id) as session_count
            FROM recall_log WHERE session_id != ''
        """).fetchone()
        total_recalls = totals["total_recalls"] or 0
        total_injected = totals["total_injected"] or 0
        session_count = totals["session_count"] or 1
        avg_per_session = round(total_injected / session_count) if session_count else 0
        return {
            "context_window": context_window,
            "total_sessions": totals["session_count"] or 0,
            "total_recalls": total_recalls,
            "total_tokens_injected": total_injected,
            "avg_tokens_per_session": avg_per_session,
            "avg_overhead_pct": round(avg_per_session / context_window * 100, 2),
            "precision": round((totals["with_hits"] or 0) / total_recalls, 3) if total_recalls else 0,
            "avg_quality": round(totals["avg_quality"] or 0, 3),
            "sessions": session_list,
        }

    def get_onboarding_status(self) -> str:
        chunks = self.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='session_chunk' AND active=1"
        ).fetchone()["c"]
        if chunks == 0:
            return "no_data"
        mems = self.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1"
        ).fetchone()["c"]
        if mems == 0:
            return "ingesting"
        llm_mems = self.db.execute("""
            SELECT COUNT(*) as c FROM artifacts
            WHERE kind='memory' AND active=1
              AND json_extract(meta, '$.mem_type') IN ('decision','lesson','snippet','bugfix')
        """).fetchone()["c"]
        if llm_mems > 0:
            return "full"
        return "extractive"
