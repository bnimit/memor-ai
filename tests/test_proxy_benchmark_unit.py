from pathlib import Path
import pytest
from memor.eval.proxy_benchmark import run_benchmark

def test_proxy_benchmark_meets_release_gate(tmp_path):
    """Test that the proxy benchmark meets the release gate: stricter criteria."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "proxy_benchmark"
    
    report = run_benchmark(fixtures_dir, db_path=str(tmp_path / "bench.db"))
    
    # Check that we have the expected number of fixtures
    assert len(report.tasks) >= 5, "Should have at least 5 fixtures"
    
    # Check tool-rich count
    tool_rich_tasks = [t for t in report.tasks if t.get("tool_rich")]
    assert len(tool_rich_tasks) >= 3, "Should have at least 3 tool-rich fixtures"
    
    # Gate criteria (a): mean ≥15% on tool-rich subset
    assert report.tool_rich_mean_pct_saved >= 15.0, \
        f"Tool-rich mean savings {report.tool_rich_mean_pct_saved:.1f}% < 15% (release gate)"
    
    # Gate criteria (b): ≥3 tool-rich fixtures with pct_saved > 0
    tool_rich_with_savings = [t for t in tool_rich_tasks if t["pct_saved"] > 0]
    assert len(tool_rich_with_savings) >= 3, \
        f"Only {len(tool_rich_with_savings)}/{len(tool_rich_tasks)} tool-rich fixtures have savings > 0 (need ≥3)"
    
    # Gate criteria (c): all fixtures must pass (required substrings preserved)
    failed_tasks = [t for t in report.tasks if not t["passed"]]
    assert len(failed_tasks) == 0, \
        f"Failed fixtures: {[t['name'] for t in failed_tasks]}"
    
    # Overall gate check
    assert report.gate_passed, "Release gate should be marked as passed"

def test_proxy_benchmark_individual_fixtures(tmp_path):
    """Test individual fixture names and properties."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "proxy_benchmark"
    
    report = run_benchmark(fixtures_dir, db_path=str(tmp_path / "bench.db"))
    
    fixture_names = {t["name"] for t in report.tasks}
    
    # Expected tool-rich fixtures
    expected_tool_rich = {"error_logs", "json_response", "openai_build_logs", "api_response_logs"}
    tool_rich_names = {t["name"] for t in report.tasks if t.get("tool_rich")}
    
    assert expected_tool_rich.issubset(tool_rich_names), \
        f"Missing tool-rich fixtures: {expected_tool_rich - tool_rich_names}"
