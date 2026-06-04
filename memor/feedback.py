"""Feedback analyzer — detects whether recalled memories were used by the agent.

After a session ends, cross-references recall_log with the transcript to see
if the agent's responses referenced recalled content. Updates memory_quality
scores accordingly."""
from __future__ import annotations
import json
from pathlib import Path
from memor.store.sqlite_store import SqliteStore

MIN_OVERLAP_TOKENS = 5


def _extract_assistant_texts(transcript_path: Path) -> list[str]:
    texts = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content.lower())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", "").lower())
    return texts


def _text_was_used(memory_text: str, assistant_texts: list[str]) -> bool:
    words = memory_text.lower().split()
    if len(words) < MIN_OVERLAP_TOKENS:
        return False
    key_phrases = []
    for i in range(0, len(words) - 4):
        key_phrases.append(" ".join(words[i:i+5]))
    if not key_phrases:
        return False
    matches = 0
    for phrase in key_phrases:
        for text in assistant_texts:
            if phrase in text:
                matches += 1
                break
    return matches >= max(2, len(key_phrases) // 5)


def analyze_session_feedback(
    store: SqliteStore, session_id: str, transcript_path: Path
) -> int:
    recalls = store.db.execute(
        "SELECT * FROM recall_log WHERE session_id=? AND hits_count > 0",
        (session_id,)
    ).fetchall()
    if not recalls:
        return 0

    recalled_ids = set()
    for r in recalls:
        log_id = r["id"]
        rl_time = r["timestamp"]
        nearby = store.db.execute("""
            SELECT q.artifact_id FROM memory_quality q
            JOIN artifacts a ON a.id = q.artifact_id
            WHERE q.last_recalled BETWEEN ? - 2 AND ? + 2
              AND a.active = 1
        """, (rl_time, rl_time)).fetchall()
        for row in nearby:
            recalled_ids.add(row["artifact_id"])

    if not recalled_ids:
        return 0

    assistant_texts = _extract_assistant_texts(transcript_path)
    if not assistant_texts:
        return 0

    used_ids = []
    for aid in recalled_ids:
        art = store.db.execute(
            "SELECT text FROM artifacts WHERE id=?", (aid,)
        ).fetchone()
        if art and _text_was_used(art["text"], assistant_texts):
            used_ids.append(aid)

    if used_ids:
        store.record_usage(used_ids)

    return len(used_ids)
