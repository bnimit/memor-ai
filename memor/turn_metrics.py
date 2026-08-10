"""Turn-level metrics parser — extracts per-turn tool call counts from
Claude Code transcripts and correlates with recall events to measure ROI.

The key ROI question: "Do turns where Memor injected context use fewer tool
calls (Read, Bash grep, etc.) than turns where it didn't?"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from memor.episodes import is_user_prompt
from datetime import datetime
from pathlib import Path


def _to_epoch(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return 0.0


@dataclass
class TurnMetric:
    turn_idx: int
    user_timestamp: float
    tool_call_count: int
    had_recall: bool = False
    tool_names: list[str] = field(default_factory=list)


def parse_turn_metrics(transcript_path: Path, session_id: str) -> list[TurnMetric]:
    """Parse a transcript into per-turn metrics (tool call counts)."""
    records = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    metrics = []
    turn_idx = 0
    pending_user_ts = None
    pending_tools: list[str] = []

    for rec in records:
        rec_type = rec.get("type")
        # A tool result is delivered as a record typed "user". Treating those as
        # prompts split every turn at the first tool call: 92% of "user" records
        # in real transcripts are tool results, so a task with 30 tool calls was
        # recorded as ~30 turns of one call each. tool_call_count could then only
        # be 0 or 1 (67%/33% of all rows), and a median over it cannot move no
        # matter what recall does -- which is what made the dashboard panel read
        # 0.0 in both arms.
        if rec_type == "user" and is_user_prompt(rec):
            if pending_user_ts is not None:
                metrics.append(TurnMetric(
                    turn_idx=turn_idx,
                    user_timestamp=pending_user_ts,
                    tool_call_count=len(pending_tools),
                    tool_names=list(pending_tools),
                ))
                turn_idx += 1
            pending_user_ts = _to_epoch(rec.get("timestamp", 0.0))
            pending_tools = []
        elif rec_type == "assistant" and pending_user_ts is not None:
            content = rec.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending_tools.append(block.get("name", "unknown"))

    # The final turn has no following prompt to close it.
    if pending_user_ts is not None:
        metrics.append(TurnMetric(
            turn_idx=turn_idx,
            user_timestamp=pending_user_ts,
            tool_call_count=len(pending_tools),
            tool_names=list(pending_tools),
        ))

    return metrics


def correlate_with_recalls(
    metrics: list[TurnMetric], store, session_id: str,
    tolerance_s: float = 10.0,
) -> list[TurnMetric]:
    """Tag each turn as had_recall=True if a recall event occurred near its timestamp."""
    rows = store.db.execute("""
        SELECT timestamp FROM recall_log
        WHERE session_id = ? AND hits_count > 0
        ORDER BY timestamp
    """, (session_id,)).fetchall()
    recall_times = [r["timestamp"] for r in rows]

    for m in metrics:
        for rt in recall_times:
            if abs(m.user_timestamp - rt) < tolerance_s:
                m.had_recall = True
                break

    return metrics
