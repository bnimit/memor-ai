"""Proxy benchmark harness for release gate verification.

Runs compression pipeline on fixture request bodies and verifies:
- Mean token reduction ≥15% on tool-rich subset
- Required substrings preserved in compressed output
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder

@dataclass
class BenchmarkReport:
    """Result of running the proxy benchmark."""
    tool_rich_mean_pct_saved: float
    tasks: list[dict]
    gate_passed: bool

def run_benchmark(fixtures_dir: Path, db_path: str | None = None) -> BenchmarkReport:
    """Run compression benchmark on all fixtures in the directory.
    
    Args:
        fixtures_dir: Directory containing fixture JSON files
        db_path: Optional database path (uses temp db if not provided)
    
    Returns:
        BenchmarkReport with mean savings and per-fixture results
    """
    if db_path is None:
        import tempfile
        import os
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    
    # Initialize store with fake embedder (offline only)
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db_path, dim=embedder.dim)
    
    # Load all fixture files
    fixture_files = sorted(fixtures_dir.glob("*.json"))
    tasks = []
    
    for fixture_file in fixture_files:
        fixture_data = json.loads(fixture_file.read_text())
        
        # Run pipeline on the fixture body
        provider = fixture_data["provider"]
        body = fixture_data["body"]
        result = run_pipeline(provider, body, store)
        
        # Calculate percentage saved
        if result.tokens_before > 0:
            pct_saved = 100.0 * (result.tokens_before - result.tokens_after) / result.tokens_before
        else:
            pct_saved = 0.0
        
        # Check if required substrings are preserved
        required_substrings = fixture_data.get("required_substrings", [])
        passed = _check_required_substrings(result.body, provider, required_substrings)
        
        tasks.append({
            "name": fixture_data["name"],
            "tool_rich": fixture_data.get("tool_rich", False),
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "pct_saved": pct_saved,
            "passed": passed,
            "passthrough": result.passthrough,
        })
    
    # Calculate mean savings for tool-rich subset
    tool_rich_tasks = [t for t in tasks if t["tool_rich"]]
    if tool_rich_tasks:
        tool_rich_mean = sum(t["pct_saved"] for t in tool_rich_tasks) / len(tool_rich_tasks)
    else:
        tool_rich_mean = 0.0
    
    # Gate criteria:
    # (a) mean tool-rich pct ≥15%
    # (b) ≥3 tool-rich fixtures with pct_saved > 0
    # (c) all fixtures passed substring checks
    tool_rich_with_savings = [t for t in tool_rich_tasks if t["pct_saved"] > 0]
    all_passed = all(t["passed"] for t in tasks)
    
    gate_passed = (
        tool_rich_mean >= 15.0
        and len(tool_rich_with_savings) >= 3
        and all_passed
    )
    
    return BenchmarkReport(
        tool_rich_mean_pct_saved=tool_rich_mean,
        tasks=tasks,
        gate_passed=gate_passed
    )

def _check_required_substrings(body: dict, provider: str, required: list[str]) -> bool:
    """Check if all required substrings are present in the compressed output."""
    if not required:
        return True
    
    # Extract all text content from the body
    text_contents = []
    
    if provider == "anthropic":
        for msg in body.get("messages", []):
            content = msg.get("content", [])
            if isinstance(content, str):
                text_contents.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        text_contents.append(item.get("content", ""))
    
    elif provider == "openai":
        for msg in body.get("messages", []):
            if msg.get("role") == "tool":
                text_contents.append(msg.get("content", ""))
    
    # Combine all text and check for required substrings
    combined_text = "\n".join(text_contents)
    
    for substring in required:
        if substring not in combined_text:
            return False
    
    return True
