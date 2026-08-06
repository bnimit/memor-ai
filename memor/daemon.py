"""Auto-ingest daemon: polls local agent session stores, then auto-distills."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from memor.ingest.claude_code import parse_transcript
from memor.ingest.sources import (
    IngestUnit,
    default_local_source_paths,
    scan_all_sources,
)
from memor.store.sqlite_store import SqliteStore
from memor.types import Scope

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_DIR = Path.home() / ".memor"
DEFAULT_DB = STATE_DIR / "memor.db"
STATE_FILE = STATE_DIR / "ingested.json"
DISTILLED_FILE = STATE_DIR / "distilled.json"
POLL_INTERVAL = 30  # seconds
MAX_DISTILL_TOKENS = 4000  # cap text sent to LLM per session

#: How often the whole-store maintenance sweeps may run.
#:
#: Quality decay, cross-project promotion and near-duplicate compaction each
#: scan every memory in the store. They were gated on "did we ingest anything",
#: which during an active coding session is true on essentially every 30s poll,
#: so all three swept the entire store every 30 seconds and the daemon never
#: went idle. None of them is time-critical: they look for patterns that
#: accumulate over days.
MAINTENANCE_INTERVAL = 3600  # seconds
MAINTENANCE_STAMP = STATE_DIR / "last_maintenance"


def _maintenance_due(now: float | None = None, *, interval: float | None = None) -> bool:
    """True when the whole-store sweeps should run again.

    The stamp lives on disk so a restart cannot turn into a fresh full sweep,
    which is what made restarting the daemon look like a CPU spike.
    """
    import time as _time

    # Read the module attribute at call time rather than binding it as a
    # default, so the interval stays adjustable at runtime.
    interval = MAINTENANCE_INTERVAL if interval is None else interval
    now = _time.time() if now is None else now
    try:
        last = float(MAINTENANCE_STAMP.read_text().strip())
    except (OSError, ValueError):
        last = 0.0
    return (now - last) >= interval


def _mark_maintenance(now: float | None = None) -> None:
    import time as _time

    now = _time.time() if now is None else now
    try:
        MAINTENANCE_STAMP.parent.mkdir(parents=True, exist_ok=True)
        MAINTENANCE_STAMP.write_text(str(now))
    except OSError:
        pass


def _project_name_from_dir(dirname: str) -> str:
    """Derive a clean project name from a Claude projects directory name.
    Uses the smart filesystem-aware resolver that handles dashes in dir names."""
    from memor.project import resolve_project_from_claude_dir
    return resolve_project_from_claude_dir(dirname)


def load_state() -> dict[str, float]:
    """Load the ingested state file: {filepath -> mtime}."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict[str, float]) -> None:
    """Persist the ingested state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def scan_transcripts(projects_dir: Path) -> list[tuple[Path, str]]:
    """Scan for all .jsonl transcript files, returning (path, project_name) pairs."""
    results = []
    if not projects_dir.is_dir():
        return results
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_name = _project_name_from_dir(project_dir.name)
        for jsonl_file in sorted(project_dir.rglob("*.jsonl")):
            results.append((jsonl_file, project_name))
    return results


def load_distilled_state() -> set[str]:
    """Load the set of already-distilled session IDs."""
    if DISTILLED_FILE.exists():
        try:
            return set(json.loads(DISTILLED_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_distilled_state(distilled: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DISTILLED_FILE.write_text(json.dumps(sorted(distilled), indent=2))


def _make_llm():
    """Create a distillation LLM. Prefers the local, offline GGUF model when
    MEMOR_LLM_DISTILL is enabled and available. Cloud backends stay opt-in via
    their API keys. Returns None -> extractive fallback."""
    if os.environ.get("MEMOR_LLM_DISTILL", "0").lower() in ("1", "true", "yes"):
        from memor.llm import llama_cpp as lc
        if lc.available():
            try:
                return lc.LlamaCppLLM()
            except lc.LlamaCppUnavailable:
                pass
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from memor.llm.anthropic import AnthropicLLM
        return AnthropicLLM(model="claude-sonnet-4-6", api_key=api_key)
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1")
    if api_key:
        from memor.llm.openai_compat import OpenAICompatLLM
        return OpenAICompatLLM(base_url=base_url, api_key=api_key, model="gpt-4o-mini")
    return None


def ingest_file(path: Path, project: str, store: SqliteStore, embedder) -> int:
    """Ingest a single Claude transcript file. Returns number of chunks ingested."""
    unit = IngestUnit(
        state_key=str(path),
        mtime=0.0,
        project=project,
        agent="claude",
        parse=lambda: parse_transcript(path, project=project, filter_noise=True),
        path=path,
    )
    return ingest_unit(unit, store, embedder)


def ingest_unit(unit: IngestUnit, store: SqliteStore, embedder) -> int:
    """Ingest one source unit. Returns number of chunks ingested."""
    arts = unit.parse()
    if not arts:
        return 0
    vecs = embedder.embed([a.text for a in arts])
    store.add_artifacts(arts, vecs)

    if unit.agent == "claude" and unit.path is not None:
        from memor.ingest.claude_code import parse_session_usage
        try:
            usage = parse_session_usage(unit.path, unit.project)
            if usage:
                store.upsert_session_stats(usage)
        except Exception:
            pass

    return len(arts)


def distill_new_sessions(
    store: SqliteStore, embedder, llm, distilled: set[str]
) -> set[str]:
    """Distill any sessions that haven't been distilled yet. Returns updated set.
    Uses full LLM distiller if llm is provided, otherwise falls back to extractive-only."""
    from memor.llm.llama_cpp import LlamaCppLLM
    if isinstance(llm, LlamaCppLLM):
        from memor.distill.distiller import LocalDistiller
        gq = os.environ.get("MEMOR_QUERY_KEYS", "0").lower() in ("1", "true", "yes")
        d = LocalDistiller(store, embedder, llm, gen_questions=gq)
    elif llm:
        from memor.distill.distiller import Distiller
        d = Distiller(store, embedder, llm)
    else:
        from memor.distill.distiller import ExtractiveDistiller
        d = ExtractiveDistiller(store, embedder)
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='session_chunk'"
    ).fetchall()
    by_session: dict[str, list] = {}
    for r in rows:
        a = store._row_to_artifact(r)
        sid = a.meta.get("session_id", "?")
        by_session.setdefault(sid, []).append(a)

    for sid, chunks in by_session.items():
        if sid in distilled:
            continue
        chunks.sort(key=lambda a: a.meta.get("ord", 0))
        total_tok = sum(c.token_count for c in chunks)
        # Cap context sent to LLM
        if total_tok > MAX_DISTILL_TOKENS:
            selected = chunks[:10] + chunks[-5:]
        else:
            selected = chunks
        project = chunks[0].project
        try:
            mem_ids = d.distill_session(sid, selected, project=project)
            distilled.add(sid)
            if mem_ids:
                print(f"  distilled session {sid[:20]}... -> {len(mem_ids)} memories")
        except Exception as e:
            print(f"  ERROR distilling {sid[:20]}...: {e}")
    return distilled


COMPACT_SIM_THRESHOLD = 0.85


def compact_memories(store: SqliteStore, embedder) -> int:
    """Find near-duplicate active memories and deactivate the older/lower-quality one."""
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='memory' AND active=1"
    ).fetchall()
    if len(rows) < 2:
        return 0
    memories = [store._row_to_artifact(r) for r in rows]
    vecs = embedder.embed([m.text for m in memories])
    # Same O(n^2) shape as cross-project promotion: at these sizes a Python
    # cosine per pair costs seconds on every cycle that ingests anything, and
    # the daemon polls every 30s. One normalized matrix turns the inner loop
    # into a lookup. Falls back to the original arithmetic without numpy.
    from memor.global_memories import _similarity_matrix
    unit = _similarity_matrix(vecs)
    deactivated = 0
    seen = set()
    for i in range(len(memories)):
        if memories[i].id in seen:
            continue
        sims = unit[i + 1:] @ unit[i] if unit is not None else None
        for j in range(i + 1, len(memories)):
            if memories[j].id in seen:
                continue
            if memories[i].project != memories[j].project:
                continue
            if sims is not None:
                sim = float(sims[j - i - 1])
            else:
                dot = sum(a * b for a, b in zip(vecs[i], vecs[j]))
                norm_i = sum(a * a for a in vecs[i]) ** 0.5
                norm_j = sum(a * a for a in vecs[j]) ** 0.5
                sim = dot / (norm_i * norm_j) if norm_i and norm_j else 0
            if sim >= COMPACT_SIM_THRESHOLD:
                qi = store.get_quality_score(memories[i].id)
                qj = store.get_quality_score(memories[j].id)
                if qi != qj:
                    loser = j if qi > qj else i
                else:
                    loser = i if memories[j].created_at > memories[i].created_at else j
                winner = j if loser == i else i
                store.deactivate(memories[loser].id, superseded_by=memories[winner].id)
                seen.add(memories[loser].id)
                deactivated += 1
    return deactivated


def should_compact(store: SqliteStore) -> bool:
    try:
        chunk_count = store.db.execute(
            "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]
        active_count = store.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE active=1").fetchone()["c"]
    except Exception as e:
        print(f"  should_compact error: {e}")
        return False
    if chunk_count == 0 or active_count == 0:
        return False
    from memor.store.sqlite_store import _choose_chunk_size
    chunk_size = _choose_chunk_size(active_count)
    ideal_chunks = max(1, active_count // chunk_size + 1)
    return chunk_count > ideal_chunks * 2


def auto_compact(store: SqliteStore, embedder) -> dict | None:
    if not should_compact(store):
        return None
    return store.rebuild_vec_index(embedder, vacuum=False)


def run_poll_cycle(
    state: dict[str, float],
    store: SqliteStore,
    embedder,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    llm=None,
    distilled: set[str] | None = None,
    *,
    kimi_sessions_dir: Path | None = None,
    kimi_json_path: Path | None = None,
    goose_db_path: Path | None = None,
    jcode_sessions_dir: Path | None = None,
) -> tuple[dict[str, float], set[str], dict[str, int]]:
    """Run one poll cycle: ingest new units, then distill new sessions.

    Claude is always scanned via ``projects_dir``. Kimi/Goose are scanned only
    when their path kwargs are provided (daemon/backfill pass home defaults;
    unit tests omit them so only the fixture Claude tree is used).

    Returns (updated ingest state, updated distilled set, chunks_by_agent).
    """
    if distilled is None:
        distilled = set()

    new_ingested = False
    units = scan_all_sources(
        claude_projects_dir=projects_dir,
        kimi_sessions_dir=kimi_sessions_dir,
        kimi_json_path=kimi_json_path,
        goose_db_path=goose_db_path,
        jcode_sessions_dir=jcode_sessions_dir,
    )

    pending = [
        u for u in units
        if not (state.get(u.state_key) is not None and u.mtime <= state[u.state_key])
    ]

    bulk = len(pending) > 10
    total_pending = len(pending)
    counts_by_agent: dict[str, int] = {}

    for idx, unit in enumerate(pending):
        progress_prefix = f"[{idx + 1}/{total_pending}] " if bulk else ""
        label = Path(unit.state_key).name if unit.path else unit.state_key
        try:
            count = ingest_unit(unit, store, embedder)
            state[unit.state_key] = unit.mtime
            counts_by_agent[unit.agent] = counts_by_agent.get(unit.agent, 0) + count
            if count > 0:
                print(
                    f"  {progress_prefix}ingested {count} chunks from {label} "
                    f"({unit.agent}, project: {unit.project})"
                )
                new_ingested = True
            else:
                print(f"  {progress_prefix}skipped {label} (0 chunks after filtering)")
        except Exception as e:
            # Record the file as seen even though it failed. The state key is
            # what stops a unit being retried, so leaving it unset made a file
            # that always raises come back on every poll forever, re-running
            # the whole post-ingest pipeline each time. A retry is only useful
            # once the file changes, and a changed mtime brings it back anyway.
            state[unit.state_key] = unit.mtime
            print(f"  {progress_prefix}ERROR ingesting {label}: {e}")

    # Auto-distill new sessions (LLM if available, extractive fallback otherwise)
    if new_ingested:
        mode = "abstractive" if llm else "extractive (LLM-free)"
        print(f"  running {mode} distillation on new sessions...")
        distilled = distill_new_sessions(store, embedder, llm, distilled)

    # Feedback + turn metrics: Claude transcripts only
    if new_ingested:
        from memor.feedback import analyze_session_feedback
        from memor.turn_metrics import parse_turn_metrics, correlate_with_recalls
        for unit in pending:
            if unit.agent != "claude" or unit.path is None:
                continue
            session_id = unit.path.stem
            try:
                # Proxy-served recalls carry no session id, so the analyzer is
                # given the conversation key as well to find them.
                from memor.conversation import conversation_key as _convo_key
                from memor.episodes import parse_episodes as _parse
                try:
                    _eps = _parse(unit.path)
                    convo = _eps[0].conversation_key if _eps else ""
                except Exception:
                    convo = ""
                used = analyze_session_feedback(
                    store, session_id, unit.path, embedder=embedder,
                    conversation_key=convo,
                )
                if used > 0:
                    print(f"  feedback: {used} memories confirmed used in {session_id[:12]}...")
            except Exception:
                pass
            try:
                metrics = parse_turn_metrics(unit.path, session_id)
                if metrics:
                    metrics = correlate_with_recalls(metrics, store, session_id)
                    store.save_turn_metrics(session_id, unit.project, metrics)
            except Exception:
                pass

    # Whole-store maintenance. Each of these scans every memory, so they run on
    # an interval rather than on every cycle that happened to ingest a chunk --
    # during an active session that was every 30 seconds, and it kept the
    # daemon permanently busy. They look for patterns that build up over days,
    # so an hourly sweep loses nothing.
    maintenance = new_ingested and _maintenance_due()

    # Soft quality decay: unused memories lose quality over time
    if maintenance:
        try:
            decayed = store.decay_quality(stale_days=14, factor=0.5, deactivate_floor=0.03)
            if decayed > 0:
                print(f"  decayed quality for {decayed} stale memories")
        except Exception:
            pass

    # Promote cross-project patterns to global scope
    if maintenance:
        try:
            from memor.global_memories import run_promotion
            promoted = run_promotion(store, embedder, min_projects=3)
            if promoted > 0:
                print(f"  promoted {promoted} memories to global scope")
        except Exception:
            pass

    # Compact near-duplicate memories (run occasionally, not every cycle)
    if maintenance:
        try:
            compacted = compact_memories(store, embedder)
            if compacted > 0:
                print(f"  compacted {compacted} near-duplicate memories")
        except Exception:
            pass

    # Auto-compact vec index if bloated
    if maintenance:
        try:
            result = auto_compact(store, embedder)
            if result:
                print(f"  vec compact: {result['before_chunks']} -> {result['after_chunks']} chunks "
                      f"({result['vectors_reindexed']} vectors, {result['duration_ms']}ms)")
        except Exception:
            pass

    if maintenance:
        # Stamped after the sweeps, not before, so a crash mid-sweep retries on
        # the next cycle rather than waiting out the whole interval.
        _mark_maintenance()

    return state, distilled, counts_by_agent


def run_backfill(
    store: SqliteStore,
    embedder,
    *,
    projects_dir: Path | None = None,
    kimi_sessions_dir: Path | None = None,
    kimi_json_path: Path | None = None,
    goose_db_path: Path | None = None,
    jcode_sessions_dir: Path | None = None,
    llm=None,
) -> dict[str, int]:
    """One-shot ingest across local agent sources. Returns chunk counts by agent."""
    paths = default_local_source_paths()
    state = load_state()
    distilled = load_distilled_state()
    state, distilled, counts = run_poll_cycle(
        state,
        store,
        embedder,
        projects_dir if projects_dir is not None else paths["claude_projects_dir"],
        llm=llm,
        distilled=distilled,
        kimi_sessions_dir=(
            kimi_sessions_dir if kimi_sessions_dir is not None
            else paths["kimi_sessions_dir"]
        ),
        kimi_json_path=(
            kimi_json_path if kimi_json_path is not None
            else paths["kimi_json_path"]
        ),
        goose_db_path=(
            goose_db_path if goose_db_path is not None
            else paths["goose_db_path"]
        ),
        jcode_sessions_dir=(
            jcode_sessions_dir if jcode_sessions_dir is not None
            else paths["jcode_sessions_dir"]
        ),
    )
    save_state(state)
    save_distilled_state(distilled)
    return counts


def redistill_project(store, embedder, llm, project, *, deactivate_old=True,
                      gen_questions=False, progress=None) -> dict:
    """Re-distill a project's raw session_chunks with the local distiller.
    Deactivates prior distilled memories first (reversible).
    Re-runs safely (dedup-on-fact prevents duplicate memories), but re-distills
    every session each call — it does not skip already-distilled sessions.
    Returns counts."""
    from memor.distill.distiller import LocalDistiller
    deactivated = 0
    if deactivate_old:
        olds = store.db.execute(
            "SELECT id FROM artifacts WHERE kind='memory' AND source='distill' "
            "AND project=? AND active=1", (project,)).fetchall()
        for r in olds:
            # _deactivate_artifact only clears the vec/fts index; the explicit active=0 below is what makes it reversible-deactivated
            store._deactivate_artifact(r["id"])
            store.db.execute("UPDATE artifacts SET active=0 WHERE id=?", (r["id"],))
            store.delete_keys(r["id"])
            deactivated += 1
        store.db.commit()
    distiller = LocalDistiller(store, embedder, llm, gen_questions=gen_questions)
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='session_chunk' AND project=?",
        (project,)).fetchall()
    by_session: dict[str, list] = {}
    for r in rows:
        a = store._row_to_artifact(r)
        by_session.setdefault(a.meta.get("session_id", "?"), []).append(a)
    total_mem = 0
    for i, (sid, chunks) in enumerate(by_session.items()):
        chunks.sort(key=lambda a: a.meta.get("ord", 0))
        ids = distiller.distill_session(sid, chunks, project)
        total_mem += len(ids)
        if progress:
            progress(i + 1, len(by_session), sid)
    return {"sessions": len(by_session), "memories": total_mem, "deactivated": deactivated}


def _make_embedder():
    """Local ONNX embedder by default. No API key needed for search."""
    from memor.embed.local import LocalEmbedder
    return LocalEmbedder()


def run_daemon(poll_interval: int = POLL_INTERVAL, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> None:
    """Run the daemon loop (foreground, blocking)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    embedder = _make_embedder()
    store = SqliteStore(str(DEFAULT_DB), dim=embedder.dim)
    state = load_state()
    distilled = load_distilled_state()
    paths = default_local_source_paths()
    kimi_dir = paths["kimi_sessions_dir"]
    kimi_json = paths["kimi_json_path"]
    goose_db = paths["goose_db_path"]
    jcode_dir = paths["jcode_sessions_dir"]

    llm = _make_llm()

    print(r"""
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|
""")
    print(f"  watching:      {projects_dir}")
    print(f"                 {kimi_dir}")
    print(f"                 {goose_db}")
    print(f"                 {jcode_dir}")
    print(f"  db:            {DEFAULT_DB}")
    print(f"  embeddings:    local model2vec (dim={embedder.dim})")
    print(f"  poll interval: {poll_interval}s")
    print(f"  tracking:      {len(state)} ingested files, {len(distilled)} sessions distilled")
    print(f"  distillation:  {'abstractive' if llm else 'extractive'}")
    print()

    try:
        while True:
            print(f"[{time.strftime('%H:%M:%S')}] polling...")
            state, distilled, _ = run_poll_cycle(
                state, store, embedder, projects_dir, llm=llm, distilled=distilled,
                kimi_sessions_dir=kimi_dir,
                kimi_json_path=kimi_json,
                goose_db_path=goose_db,
                jcode_sessions_dir=jcode_dir,
            )
            save_state(state)
            save_distilled_state(distilled)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\ndaemon stopped.")
        save_state(state)
        save_distilled_state(distilled)
