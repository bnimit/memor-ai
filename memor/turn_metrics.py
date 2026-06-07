"""Turn-level metrics parser — extracts per-turn tool call counts from
Claude Code transcripts and correlates with recall events to measure ROI.

The key ROI question: "Do turns where Memor injected context use fewer tool
calls (Read, Bash grep, etc.) than turns where it didn't?"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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

    for rec in records:
        rec_type = rec.get("type")
        if rec_type == "user":
            pending_user_ts = rec.get("timestamp", 0.0)
        elif rec_type == "assistant" and pending_user_ts is not None:
            msg = rec.get("message", {})
            content = msg.get("content", [])
            tool_names = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_names.append(block.get("name", "unknown"))
            metrics.append(TurnMetric(
                turn_idx=turn_idx,
                user_timestamp=pending_user_ts,
                tool_call_count=len(tool_names),
                tool_names=tool_names,
            ))
            turn_idx += 1
            pending_user_ts = None

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
