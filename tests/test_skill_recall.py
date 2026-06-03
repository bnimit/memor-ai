import subprocess, sys
from pathlib import Path


def test_recall_script_outputs_context_block(tmp_path):
    # build a tiny db via the CLI (fake embedder), then call the skill entrypoint
    db = str(tmp_path / "m.db")
    subprocess.run(
        [sys.executable, "-m", "memorable.cli", "ingest-cc",
         "tests/fixtures/sample.jsonl", "--project", "stablex", "--db", db,
         "--fake", "--no-filter"],
        check=True,
    )
    out = subprocess.run(
        [sys.executable, "skill/recall.py", "--query", "auth refresh loop",
         "--project", "stablex", "--db", db, "--fake", "--k", "3"],
        capture_output=True, text=True, check=True,
    )
    output = out.stdout
    # Should have the formatted header
    assert "## Recalled Context (project: stablex)" in output
    # Should have at least one numbered hit
    assert "### 1." in output
    # Should have a trace line
    assert "Trace:" in output
    assert "hits" in output
    # Content should reference auth
    assert "auth" in output.lower()


def test_recall_script_missing_db(tmp_path):
    out = subprocess.run(
        [sys.executable, "skill/recall.py", "--query", "anything",
         "--project", "nope", "--db", str(tmp_path / "nonexistent.db"), "--fake"],
        capture_output=True, text=True,
    )
    assert out.returncode != 0
    assert "Database not found" in out.stdout
    assert "memorable daemon" in out.stdout
