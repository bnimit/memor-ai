"""Settle verdicts on recalls the daemon will never revisit.

The feedback loop grades a session at the moment it is ingested. Recalls served
before the loop reached every agent are therefore stranded: their sessions are
already in the store, will not be scanned again, and their verdicts stay
``pending`` forever. On this machine that was 133 rows, including every
cross-tool recall -- the product's headline capability, unmeasured.

This walks the local sources once and grades what it can. It is a one-off
catch-up, not a replacement for the daemon: new sessions are still graded as
they arrive.

Two rules keep the result honest. A recall with no session to check against
stays ``pending``, because guessing from an absent transcript is how the
previous feedback code credited every memory in a project. And a verdict is
only ever settled from text written *after* the recall, which the analyzer
enforces.
"""
from __future__ import annotations

from pathlib import Path

from memor.store.sqlite_store import SqliteStore


def backfill_feedback(
    store: SqliteStore,
    *,
    embedder=None,
    claude_projects_dir: Path | None = None,
    jcode_sessions_dir: Path | None = None,
    kimi_sessions_dir: Path | None = None,
    kimi_json_path: Path | None = None,
    goose_db_path: Path | None = None,
    progress=None,
) -> int:
    """Grade stranded recalls. Returns the number of verdicts settled.

    Sources default to the local paths the daemon watches. Passing an explicit
    directory scopes the walk, which the tests use and which is also the way to
    re-grade one agent without touching the rest.
    """

    from memor.feedback import analyze_session_feedback, turns_for_unit
    from memor.ingest.sources import default_local_source_paths, scan_all_sources

    defaults = default_local_source_paths()
    units = scan_all_sources(
        claude_projects_dir=(claude_projects_dir
                             if claude_projects_dir is not None
                             else defaults["claude_projects_dir"]),
        kimi_sessions_dir=(kimi_sessions_dir
                           if kimi_sessions_dir is not None
                           else defaults["kimi_sessions_dir"]),
        kimi_json_path=(kimi_json_path
                        if kimi_json_path is not None
                        else defaults["kimi_json_path"]),
        goose_db_path=(goose_db_path
                       if goose_db_path is not None
                       else defaults["goose_db_path"]),
        jcode_sessions_dir=(jcode_sessions_dir
                            if jcode_sessions_dir is not None
                            else defaults["jcode_sessions_dir"]),
    )

    # Newest first: a stranded recall is far likelier to belong to a recent
    # session, and on a large store this settles most of them early.
    units.sort(key=lambda u: getattr(u, "mtime", 0.0), reverse=True)

    from memor.daemon import _session_id_for

    settled = 0
    for index, unit in enumerate(units):
        if progress is not None:
            progress(index, len(units), settled)
        if _pending_count(store) == 0:
            break

        session_id = _session_id_for(unit)
        try:
            turns = turns_for_unit(unit)
        except Exception:
            continue
        if not turns:
            continue

        # Proxy-served recalls carry no session id, only a key derived from the
        # opening user message. It has to be derived exactly as the recall path
        # derived it, which the normalised turns cannot do: they are lowercased
        # for matching, and the hash is case-sensitive, so a key computed from
        # them never matches. They also count tool results as user turns, while
        # the canonical derivation skips those as mid-loop records. So the key
        # comes from the source parser, not from the turns.
        convo = _conversation_key_for(unit)

        if not session_id and not convo:
            continue

        try:
            settled += analyze_session_feedback(
                store, session_id, embedder=embedder,
                conversation_key=convo, turns=turns,
            )
        except Exception:
            continue

    return settled


def _conversation_key_for(unit) -> str:
    """The key the recall path would have stored for this session.

    Claude sessions go through ``parse_episodes``, which is the same derivation
    the recall path used, so it is reused verbatim rather than approximated.
    Other agents never reach the proxy -- their recalls carry a session id --
    so they need no key.
    """
    if getattr(unit, "agent", "") != "claude":
        return ""
    path = getattr(unit, "path", None)
    if path is None:
        return ""
    try:
        from memor.episodes import parse_episodes

        episodes = parse_episodes(path)
        return episodes[0].conversation_key if episodes else ""
    except Exception:
        return ""


def _pending_count(store: SqliteStore) -> int:
    row = store.db.execute(
        "SELECT COUNT(*) AS n FROM recall_outcomes WHERE outcome = 'pending'"
    ).fetchone()
    return row["n"] if row else 0
