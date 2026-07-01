from __future__ import annotations
import json, re, sqlite3, struct
from pathlib import Path
import sqlite_vec
from memor.types import Artifact, Scope, SessionUsage

def _serialize(v: list[float]) -> bytes:
    return struct.pack("%sf" % len(v), *v)

def _choose_chunk_size(active_count: int) -> int:
    if active_count < 1000:
        return 64
    if active_count < 10000:
        return 256
    if active_count < 100000:
        return 512
    return 1024

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
        self._migrate_fts()
        self._migrate_quality_decay()
        self._migrate_negative_count()
        self._migrate_recall_agent()
        self._migrate_key_vectors()

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
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_artifacts USING fts5(
          id UNINDEXED, text, tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS eval_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, config TEXT, metrics TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS memory_quality(
          artifact_id TEXT PRIMARY KEY,
          recall_count INTEGER DEFAULT 0,
          use_count INTEGER DEFAULT 0,
          negative_count INTEGER DEFAULT 0,
          last_recalled REAL,
          quality_score REAL DEFAULT 0.5,
          last_decayed_at REAL);
        CREATE TABLE IF NOT EXISTS recall_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp REAL, project TEXT, query_preview TEXT,
          hits_count INTEGER, top_score REAL, tokens_injected INTEGER,
          latency_ms REAL, status TEXT, session_id TEXT);
        CREATE TABLE IF NOT EXISTS session_stats(
          session_id TEXT PRIMARY KEY,
          project TEXT NOT NULL,
          turn_count INTEGER NOT NULL DEFAULT 0,
          total_input_tokens INTEGER NOT NULL DEFAULT 0,
          total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
          total_cache_create_tokens INTEGER NOT NULL DEFAULT 0,
          total_output_tokens INTEGER NOT NULL DEFAULT 0,
          tool_call_count INTEGER NOT NULL DEFAULT 0,
          recall_turn_count INTEGER NOT NULL DEFAULT 0,
          first_turn_at REAL,
          last_turn_at REAL,
          updated_at REAL NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_session_stats_project ON session_stats(project);
        CREATE TABLE IF NOT EXISTS turn_metrics(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          project TEXT NOT NULL,
          turn_idx INTEGER NOT NULL,
          user_timestamp REAL,
          tool_call_count INTEGER DEFAULT 0,
          had_recall INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_turn_metrics_session ON turn_metrics(session_id);
        CREATE TABLE IF NOT EXISTS key_vectors(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id TEXT NOT NULL,
          key_type TEXT NOT NULL,
          key_text TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_key_mem ON key_vectors(memory_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_keys USING vec0(embedding float[{self.dim}]);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_keys USING fts5(
          key_id UNINDEXED, key_text, tokenize='porter unicode61');
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

    def _migrate_quality_decay(self):
        """Add last_decayed_at column to memory_quality if missing."""
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(memory_quality)").fetchall()]
        if "last_decayed_at" not in cols:
            try:
                self.db.execute("ALTER TABLE memory_quality ADD COLUMN last_decayed_at REAL")
                self.db.commit()
            except Exception:
                pass

    def _migrate_negative_count(self):
        """Add negative_count column to memory_quality if missing."""
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(memory_quality)").fetchall()]
        if "negative_count" not in cols:
            try:
                self.db.execute(
                    "ALTER TABLE memory_quality ADD COLUMN negative_count INTEGER DEFAULT 0")
                self.db.commit()
            except Exception:
                pass

    def _migrate_recall_agent(self):
        """Add agent column to recall_log if missing."""
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(recall_log)").fetchall()]
        if "agent" not in cols:
            try:
                self.db.execute("ALTER TABLE recall_log ADD COLUMN agent TEXT DEFAULT 'claude'")
                self.db.commit()
            except Exception:
                pass

    def _migrate_key_vectors(self):
        """Create key_vectors/vec_keys/fts_keys on DBs created before Project 1.
        _init_schema already creates them for new DBs; this is a no-op then."""
        self.db.executescript(f"""
        CREATE TABLE IF NOT EXISTS key_vectors(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id TEXT NOT NULL, key_type TEXT NOT NULL, key_text TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_key_mem ON key_vectors(memory_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_keys USING vec0(embedding float[{self.dim}]);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_keys USING fts5(
          key_id UNINDEXED, key_text, tokenize='porter unicode61');
        """)
        self.db.commit()

    def _migrate_fts(self):
        """One-time backfill of the FTS index for databases created before
        lexical search existed. Runs only when artifacts exist but the index is
        empty, so it's a no-op on every subsequent open."""
        has_artifacts = self.db.execute("SELECT 1 FROM artifacts LIMIT 1").fetchone()
        if not has_artifacts:
            return
        has_fts = self.db.execute("SELECT 1 FROM fts_artifacts LIMIT 1").fetchone()
        if has_fts:
            return
        self.rebuild_fts()

    def add_artifacts(self, artifacts: list[Artifact], vectors: list[list[float]]) -> None:
        cur = self.db.cursor()
        for a, v in zip(artifacts, vectors):
            old = cur.execute("SELECT rowid FROM artifacts WHERE id=?", (a.id,)).fetchone()
            if old:
                cur.execute("DELETE FROM vec_artifacts WHERE rowid=?", (old[0],))
            cur.execute(
              "INSERT OR REPLACE INTO artifacts(id,kind,project,source,text,token_count,created_at,meta,active,superseded_by)"
              " VALUES(?,?,?,?,?,?,?,?,1,NULL)",
              (a.id, a.kind, a.project, a.source, a.text, a.token_count, a.created_at, json.dumps(a.meta)))
            rowid = cur.execute("SELECT rowid FROM artifacts WHERE id=?", (a.id,)).fetchone()[0]
            cur.execute("INSERT INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(v)))
            cur.execute("DELETE FROM fts_artifacts WHERE id=?", (a.id,))
            cur.execute("INSERT INTO fts_artifacts(id, text) VALUES(?,?)", (a.id, a.text))
        self.db.commit()

    def add_edge(self, src_id: str, dst_id: str, type: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO edges(src_id,dst_id,type) VALUES(?,?,?)",
                        (src_id, dst_id, type))
        self.db.commit()

    def _row_to_artifact(self, r) -> Artifact:
        return Artifact(id=r["id"], kind=r["kind"], project=r["project"], source=r["source"],
                        text=r["text"], token_count=r["token_count"], created_at=r["created_at"],
                        meta=json.loads(r["meta"]))

    # sqlite-vec caps the KNN `k` parameter at this value.
    _VEC_KNN_LIMIT = 4096

    def search(self, vector: list[float], scope: Scope, k: int) -> list[tuple[Artifact, float]]:
        from memor.types import GLOBAL_PROJECT
        # Cap the KNN fetch at sqlite-vec's hard limit so large k can't error.
        fetch = min(max(k * 20, 200), self._VEC_KNN_LIMIT)
        rows = self.db.execute(f"""
          SELECT a.*, v.distance AS distance
          FROM (SELECT rowid, distance FROM vec_artifacts
                WHERE embedding MATCH ? AND k = ?) v
          JOIN artifacts a ON a.rowid = v.rowid
          WHERE a.active = 1
            AND (? IS NULL OR a.project = ? OR a.project = ?)
            AND (? IS NULL OR a.created_at >= ?)
            AND (? IS NULL OR a.created_at <= ?)
          ORDER BY v.distance ASC
        """, (_serialize(vector), fetch,
              scope.project, scope.project, GLOBAL_PROJECT,
              scope.since, scope.since,
              scope.until, scope.until)).fetchall()
        out = []
        for r in rows[:k]:
            if scope.kinds is not None and r["kind"] not in scope.kinds:
                continue
            sim = 1.0 - float(r["distance"])
            out.append((self._row_to_artifact(r), sim))
        return out

    def rebuild_fts(self) -> int:
        """Backfill the FTS index from the artifacts table. Idempotent — used to
        index databases created before lexical search existed. Returns the row
        count indexed."""
        cur = self.db.cursor()
        cur.execute("DELETE FROM fts_artifacts")
        cur.execute("INSERT INTO fts_artifacts(id, text) SELECT id, text FROM artifacts")
        n = self.db.execute("SELECT COUNT(*) AS c FROM fts_artifacts").fetchone()["c"]
        self.db.commit()
        return n

    def rebuild_vec_index(self, embedder, vacuum: bool = True) -> dict:
        import time as _time
        start = _time.time()
        chunk_count_before = self.db.execute(
            "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]

        rows = self.db.execute(
            "SELECT id, text, rowid FROM artifacts WHERE active=1"
        ).fetchall()
        texts = [r["text"] for r in rows]
        rowids = [r["rowid"] for r in rows]

        if texts:
            vectors = embedder.embed(texts)
        else:
            vectors = []

        self.db.execute("DROP TABLE IF EXISTS vec_artifacts")
        chunk_size = _choose_chunk_size(len(rows))
        self.db.execute(
            f"CREATE VIRTUAL TABLE vec_artifacts USING vec0("
            f"embedding float[{self.dim}], chunk_size={chunk_size})")

        cur = self.db.cursor()
        for rowid, vec in zip(rowids, vectors):
            cur.execute("INSERT INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(vec)))
        self.db.commit()

        if vacuum:
            self.db.execute("VACUUM")

        chunk_count_after = self.db.execute(
            "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]
        elapsed = round((_time.time() - start) * 1000, 1)

        return {
            "before_chunks": chunk_count_before,
            "after_chunks": chunk_count_after,
            "vectors_reindexed": len(rows),
            "chunk_size": chunk_size,
            "duration_ms": elapsed,
        }

    def search_lexical(self, query: str, scope: Scope, k: int) -> list[tuple[Artifact, float]]:
        """Lexical BM25 search over artifact text via FTS5. Returns
        (artifact, bm25_score) ordered best-first (lower bm25 = better match).
        Terms are OR-combined so partial matches still surface — the dense
        channel handles semantics, this channel recovers exact terms."""
        from memor.types import GLOBAL_PROJECT
        terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
        if not terms:
            return []
        match = " OR ".join(terms)
        rows = self.db.execute(f"""
          SELECT a.*, bm25(fts_artifacts) AS bm25_rank
          FROM fts_artifacts f JOIN artifacts a ON a.id = f.id
          WHERE fts_artifacts MATCH ? AND a.active = 1
            AND (? IS NULL OR a.project = ? OR a.project = ?)
            AND (? IS NULL OR a.created_at >= ?)
            AND (? IS NULL OR a.created_at <= ?)
          ORDER BY bm25_rank ASC LIMIT ?
        """, (match, scope.project, scope.project, GLOBAL_PROJECT,
              scope.since, scope.since, scope.until, scope.until, k)).fetchall()
        out = []
        for r in rows:
            if scope.kinds is not None and r["kind"] not in scope.kinds:
                continue
            out.append((self._row_to_artifact(r), float(r["bm25_rank"])))
        return out

    def add_keys(self, memory_id: str, keys: list[tuple[str, str]],
                 vectors: list[list[float]]) -> None:
        cur = self.db.cursor()
        for (ktype, ktext), v in zip(keys, vectors):
            cur.execute(
                "INSERT INTO key_vectors(memory_id, key_type, key_text) VALUES(?,?,?)",
                (memory_id, ktype, ktext))
            kid = cur.lastrowid
            cur.execute("INSERT INTO vec_keys(rowid, embedding) VALUES(?,?)",
                        (kid, _serialize(v)))
            cur.execute("INSERT INTO fts_keys(key_id, key_text) VALUES(?,?)",
                        (kid, ktext))
        self.db.commit()

    def search_keys(self, vector: list[float], scope: Scope, k: int) -> list[tuple[str, float]]:
        from memor.types import GLOBAL_PROJECT
        fetch = min(max(k * 20, 200), self._VEC_KNN_LIMIT)
        rows = self.db.execute("""
          SELECT kv.memory_id AS mid, v.distance AS distance
          FROM (SELECT rowid, distance FROM vec_keys
                WHERE embedding MATCH ? AND k = ?) v
          JOIN key_vectors kv ON kv.id = v.rowid
          JOIN artifacts a ON a.id = kv.memory_id
          WHERE a.active = 1
            AND (? IS NULL OR a.project = ? OR a.project = ?)
          ORDER BY v.distance ASC
        """, (_serialize(vector), fetch,
              scope.project, scope.project, GLOBAL_PROJECT)).fetchall()
        best: dict[str, float] = {}
        for r in rows:
            sim = 1.0 - float(r["distance"])
            mid = r["mid"]
            if mid not in best or sim > best[mid]:
                best[mid] = sim
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:k]

    def delete_keys(self, memory_id: str) -> None:
        rows = self.db.execute(
            "SELECT id FROM key_vectors WHERE memory_id=?", (memory_id,)).fetchall()
        for r in rows:
            self.db.execute("DELETE FROM vec_keys WHERE rowid=?", (r["id"],))
            self.db.execute("DELETE FROM fts_keys WHERE key_id=?", (r["id"],))
        self.db.execute("DELETE FROM key_vectors WHERE memory_id=?", (memory_id,))
        self.db.commit()

    def count_keys(self) -> int:
        return self.db.execute("SELECT COUNT(*) AS c FROM key_vectors").fetchone()["c"]

    def find_similar_fact(self, vector: list[float], project: str,
                          threshold: float = 0.92) -> str | None:
        from memor.types import GLOBAL_PROJECT
        rows = self.db.execute(f"""
          SELECT kv.memory_id AS mid, v.distance AS distance
          FROM (SELECT rowid, distance FROM vec_keys
                WHERE embedding MATCH ? AND k = {self._VEC_KNN_LIMIT}) v
          JOIN key_vectors kv ON kv.id = v.rowid
          JOIN artifacts a ON a.id = kv.memory_id
          WHERE a.active = 1 AND kv.key_type = 'fact'
            AND (a.project = ? OR a.project = ?)
          ORDER BY v.distance ASC LIMIT 1
        """, (_serialize(vector), project, GLOBAL_PROJECT)).fetchall()
        if not rows:
            return None
        sim = 1.0 - float(rows[0]["distance"])
        return rows[0]["mid"] if sim >= threshold else None

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

    def _deactivate_artifact(self, artifact_id: str) -> None:
        row = self.db.execute(
            "SELECT rowid FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row:
            self.db.execute("DELETE FROM vec_artifacts WHERE rowid=?", (row[0],))
            self.db.execute("DELETE FROM fts_artifacts WHERE id=?", (artifact_id,))

    def deactivate(self, artifact_id: str, superseded_by: str) -> None:
        self._deactivate_artifact(artifact_id)
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

    def get_latest_eval(self, eval_type: str = "counterfactual") -> dict | None:
        row = self.db.execute(
            "SELECT * FROM eval_runs WHERE json_extract(config, '$.type') = ? "
            "ORDER BY created_at DESC LIMIT 1", (eval_type,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "config": json.loads(row["config"]),
            "metrics": json.loads(row["metrics"]),
        }

    def log_recall(self, project: str, query_preview: str, hits_count: int,
                   top_score: float, tokens_injected: int, latency_ms: float,
                   status: str, session_id: str = "",
                   agent: str = "claude") -> None:
        import time as _time
        self.db.execute(
            "INSERT INTO recall_log(timestamp,project,query_preview,hits_count,"
            "top_score,tokens_injected,latency_ms,status,session_id,agent) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_time.time(), project, query_preview[:100], hits_count, top_score,
             tokens_injected, latency_ms, status, session_id, agent))
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

    def get_agent_breakdown(self) -> list[dict]:
        rows = self.db.execute("""
            SELECT COALESCE(agent, 'claude') as agent,
                   COUNT(*) as recalls,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as hits,
                   AVG(latency_ms) as avg_latency
            FROM recall_log
            GROUP BY COALESCE(agent, 'claude')
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

    def record_recall(self, artifact_ids: list[str]) -> None:
        import time as _time
        now = _time.time()
        for aid in artifact_ids:
            self.db.execute("""
                INSERT INTO memory_quality(artifact_id, recall_count, use_count, last_recalled, quality_score)
                VALUES(?, 1, 0, ?, 0.5)
                ON CONFLICT(artifact_id) DO UPDATE SET
                  recall_count = recall_count + 1,
                  last_recalled = ?
            """, (aid, now, now))
        self.db.commit()

    def record_usage(self, artifact_ids: list[str]) -> None:
        for aid in artifact_ids:
            self.db.execute("""
                UPDATE memory_quality SET use_count = use_count + 1 WHERE artifact_id = ?
            """, (aid,))
        self.db.commit()
        self._recompute_quality(artifact_ids)

    def record_negative(self, artifact_ids: list[str]) -> None:
        for aid in artifact_ids:
            self.db.execute("""
                INSERT INTO memory_quality(artifact_id, recall_count, use_count, negative_count, quality_score)
                VALUES(?, 0, 0, 1, 0.5)
                ON CONFLICT(artifact_id) DO UPDATE SET
                  negative_count = negative_count + 1
            """, (aid,))
        self.db.commit()
        self._recompute_quality(artifact_ids)

    def _recompute_quality(self, artifact_ids: list[str]) -> None:
        for aid in artifact_ids:
            row = self.db.execute(
                "SELECT recall_count, use_count, negative_count FROM memory_quality WHERE artifact_id=?",
                (aid,)).fetchone()
            if row:
                rc = row["recall_count"] or 1
                uc = row["use_count"] or 0
                nc = row["negative_count"] or 0
                score = max(0.0, (uc - nc + 1) / (rc + 2))
                self.db.execute(
                    "UPDATE memory_quality SET quality_score=? WHERE artifact_id=?",
                    (round(score, 3), aid))
        self.db.commit()

    def get_quality_score(self, artifact_id: str) -> float:
        row = self.db.execute(
            "SELECT quality_score FROM memory_quality WHERE artifact_id=?",
            (artifact_id,)).fetchone()
        return row["quality_score"] if row else 0.5

    def get_quality_scores(self, artifact_ids: list[str]) -> dict[str, float]:
        """Batch form of get_quality_score: one query for the whole candidate
        set (callers default missing ids to 0.5). Keeps the widened retrieval
        pool off the N+1-query path on the per-prompt hot path."""
        ids = list(artifact_ids)
        if not ids:
            return {}
        qmarks = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT artifact_id, quality_score FROM memory_quality "
            f"WHERE artifact_id IN ({qmarks})", ids).fetchall()
        return {r["artifact_id"]: r["quality_score"] for r in rows}

    def get_stale_memories(self, days: int = 30) -> list[str]:
        import time as _time
        cutoff = _time.time() - (days * 86400)
        rows = self.db.execute("""
            SELECT a.id FROM artifacts a
            LEFT JOIN memory_quality q ON a.id = q.artifact_id
            WHERE a.kind = 'memory' AND a.active = 1
              AND (q.artifact_id IS NULL OR q.last_recalled IS NULL OR q.last_recalled < ?)
              AND a.created_at < ?
        """, (cutoff, cutoff)).fetchall()
        return [r["id"] for r in rows]

    def decay_quality(self, stale_days: int = 14, factor: float = 0.5,
                      deactivate_floor: float = 0.03) -> int:
        """Halve quality_score for memories not recalled in stale_days.
        Only decays each memory once per stale_days interval (tracked via
        last_decayed_at). Also catches memories with NULL last_recalled
        that are old enough. If the decayed score drops below
        deactivate_floor, deactivate the memory.
        Returns number of memories decayed."""
        import time as _time
        now = _time.time()
        recall_cutoff = now - (stale_days * 86400)
        decay_cutoff = now - (stale_days * 86400)
        rows = self.db.execute("""
            SELECT q.artifact_id, q.quality_score
            FROM memory_quality q
            JOIN artifacts a ON a.id = q.artifact_id
            WHERE a.kind = 'memory' AND a.active = 1
              AND (q.last_recalled IS NULL OR q.last_recalled < ?)
              AND (q.last_decayed_at IS NULL OR q.last_decayed_at < ?)
        """, (recall_cutoff, decay_cutoff)).fetchall()
        decayed = 0
        for r in rows:
            new_score = round(r["quality_score"] * factor, 4)
            if new_score < deactivate_floor:
                self._deactivate_artifact(r["artifact_id"])
                self.db.execute("UPDATE artifacts SET active=0 WHERE id=?",
                                (r["artifact_id"],))
            self.db.execute(
                "UPDATE memory_quality SET quality_score=?, last_decayed_at=? WHERE artifact_id=?",
                (new_score, now, r["artifact_id"]))
            decayed += 1
        self.db.commit()
        return decayed

    def deactivate_stale(self, days: int = 30) -> int:
        ids = self.get_stale_memories(days)
        for aid in ids:
            self._deactivate_artifact(aid)
            self.db.execute("UPDATE artifacts SET active=0 WHERE id=?", (aid,))
        self.db.commit()
        return len(ids)

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
            coverage = (s["prompts_with_hits"] or 0) / prompts
            overhead_pct = round(injected / context_window * 100, 2)
            session_list.append({
                "session_id": s["session_id"],
                "prompts": prompts,
                "tokens_injected": injected,
                "overhead_pct": overhead_pct,
                "coverage": round(coverage, 3),
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
            "coverage": round((totals["with_hits"] or 0) / total_recalls, 3) if total_recalls else 0,
            "avg_quality": round(totals["avg_quality"] or 0, 3),
            "sessions": session_list,
        }

    def upsert_session_stats(self, usage: SessionUsage) -> None:
        import time
        self.db.execute("""
            INSERT INTO session_stats(
                session_id, project, turn_count,
                total_input_tokens, total_cache_read_tokens, total_cache_create_tokens,
                total_output_tokens, tool_call_count, recall_turn_count,
                first_turn_at, last_turn_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                project=excluded.project,
                turn_count=excluded.turn_count,
                total_input_tokens=excluded.total_input_tokens,
                total_cache_read_tokens=excluded.total_cache_read_tokens,
                total_cache_create_tokens=excluded.total_cache_create_tokens,
                total_output_tokens=excluded.total_output_tokens,
                tool_call_count=excluded.tool_call_count,
                recall_turn_count=excluded.recall_turn_count,
                first_turn_at=excluded.first_turn_at,
                last_turn_at=excluded.last_turn_at,
                updated_at=excluded.updated_at
        """, (
            usage.session_id, usage.project, usage.turn_count,
            usage.total_input_tokens, usage.total_cache_read_tokens,
            usage.total_cache_create_tokens, usage.total_output_tokens,
            usage.tool_call_count, usage.recall_turn_count,
            usage.first_turn_at, usage.last_turn_at, time.time(),
        ))
        self.db.commit()

    def get_session_efficiency(self) -> dict:
        rows = self.db.execute("""
            SELECT * FROM session_stats WHERE turn_count > 0
            ORDER BY last_turn_at DESC
        """).fetchall()
        if not rows:
            return {
                "avg_input_per_turn": 0, "avg_input_per_turn_with_recall": 0,
                "avg_input_per_turn_without_recall": 0, "efficiency_gain_pct": 0,
                "total_sessions": 0, "sessions_with_recall": 0,
                "sessions_without_recall": 0,
                "avg_tool_calls_with_recall": 0, "avg_tool_calls_without_recall": 0,
                "sessions": [],
            }

        with_recall = [r for r in rows if r["recall_turn_count"] > 0]
        without_recall = [r for r in rows if r["recall_turn_count"] == 0]

        def avg_input(session_rows):
            total_input = sum(r["total_input_tokens"] for r in session_rows)
            total_turns = sum(r["turn_count"] for r in session_rows)
            return round(total_input / total_turns) if total_turns > 0 else 0

        def avg_tools(session_rows):
            total_tools = sum(r["tool_call_count"] for r in session_rows)
            total_turns = sum(r["turn_count"] for r in session_rows)
            return round(total_tools / total_turns, 1) if total_turns > 0 else 0

        avg_with = avg_input(with_recall)
        avg_without = avg_input(without_recall)
        gain = round((1 - avg_with / avg_without) * 100, 1) if avg_without > 0 else 0

        sessions = []
        for r in rows[:50]:
            turns = r["turn_count"]
            sessions.append({
                "session_id": r["session_id"],
                "project": r["project"],
                "turn_count": turns,
                "total_input_tokens": r["total_input_tokens"],
                "total_output_tokens": r["total_output_tokens"],
                "tool_call_count": r["tool_call_count"],
                "recall_turn_count": r["recall_turn_count"],
                "avg_input_per_turn": round(r["total_input_tokens"] / turns) if turns else 0,
                "has_recall": r["recall_turn_count"] > 0,
                "last_turn_at": r["last_turn_at"],
            })

        return {
            "avg_input_per_turn": avg_input(rows),
            "avg_input_per_turn_with_recall": avg_with,
            "avg_input_per_turn_without_recall": avg_without,
            "efficiency_gain_pct": gain,
            "total_sessions": len(rows),
            "sessions_with_recall": len(with_recall),
            "sessions_without_recall": len(without_recall),
            "avg_tool_calls_with_recall": avg_tools(with_recall),
            "avg_tool_calls_without_recall": avg_tools(without_recall),
            "sessions": sessions,
        }

    def save_turn_metrics(self, session_id: str, project: str, metrics: list) -> None:
        """Persist per-turn tool call metrics. Idempotent — deletes old metrics
        for the session first."""
        self.db.execute("DELETE FROM turn_metrics WHERE session_id=?", (session_id,))
        for m in metrics:
            self.db.execute(
                "INSERT INTO turn_metrics(session_id, project, turn_idx, "
                "user_timestamp, tool_call_count, had_recall) VALUES(?,?,?,?,?,?)",
                (session_id, project, m.turn_idx, m.user_timestamp,
                 m.tool_call_count, 1 if m.had_recall else 0))
        self.db.commit()

    def get_token_roi(self, project: str | None = None) -> dict:
        """Compute tool-call ROI: avg tool calls per turn with vs without recall."""
        where = "WHERE 1=1"
        params: list = []
        if project:
            where += " AND project = ?"
            params.append(project)

        row = self.db.execute(f"""
            SELECT
              COUNT(CASE WHEN had_recall = 1 THEN 1 END) AS turns_with,
              COUNT(CASE WHEN had_recall = 0 THEN 1 END) AS turns_without,
              AVG(CASE WHEN had_recall = 1 THEN tool_call_count END) AS avg_with,
              AVG(CASE WHEN had_recall = 0 THEN tool_call_count END) AS avg_without
            FROM turn_metrics {where}
        """, params).fetchone()

        turns_with = row["turns_with"] or 0
        turns_without = row["turns_without"] or 0
        avg_with = round(row["avg_with"] or 0, 2)
        avg_without = round(row["avg_without"] or 0, 2)
        reduction = round((1 - avg_with / avg_without) * 100, 1) if avg_without > 0 else 0

        return {
            "turns_with_recall": turns_with,
            "turns_without_recall": turns_without,
            "avg_tools_with_recall": avg_with,
            "avg_tools_without_recall": avg_without,
            "tool_call_reduction_pct": reduction,
        }

    def get_roi_trend(self, project: str | None = None,
                      days: int = 30) -> list[dict]:
        where = "WHERE user_timestamp > 0"
        params: list = []
        if project:
            where += " AND project = ?"
            params.append(project)

        rows = self.db.execute(f"""
            SELECT date(user_timestamp, 'unixepoch', 'localtime') AS day,
                   AVG(CASE WHEN had_recall = 1 THEN tool_call_count END) AS avg_with,
                   AVG(CASE WHEN had_recall = 0 THEN tool_call_count END) AS avg_without,
                   COUNT(CASE WHEN had_recall = 1 THEN 1 END) AS turns_with,
                   COUNT(CASE WHEN had_recall = 0 THEN 1 END) AS turns_without,
                   COUNT(*) AS turns_total
            FROM turn_metrics {where}
            GROUP BY day
            HAVING turns_with > 0 AND turns_without > 0
            ORDER BY day
        """, params).fetchall()

        result = []
        for r in rows:
            avg_w = round(r["avg_with"], 2)
            avg_wo = round(r["avg_without"], 2)
            reduction = round((1 - avg_w / avg_wo) * 100, 1) if avg_wo > 0 else 0
            result.append({
                "day": r["day"],
                "avg_with": avg_w,
                "avg_without": avg_wo,
                "reduction_pct": reduction,
                "turns_with": r["turns_with"],
                "turns_without": r["turns_without"],
                "turns_total": r["turns_total"],
            })
        return result

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
