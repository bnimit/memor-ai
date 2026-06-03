from __future__ import annotations
import json, sqlite3, struct
import sqlite_vec
from memorable.types import Artifact, Scope

def _serialize(v: list[float]) -> bytes:
    return struct.pack("%sf" % len(v), *v)

class SqliteStore:
    def __init__(self, path: str, dim: int):
        self.dim = dim
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._init_schema()

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
        """)
        self.db.commit()

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
        """, (_serialize(vector), max(k*5, k),
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
