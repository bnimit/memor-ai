import json, subprocess, sys
from pathlib import Path

def test_recall_script_outputs_context_block(tmp_path):
    # build a tiny db via the CLI (fake embedder), then call the skill entrypoint
    db = str(tmp_path/"m.db")
    subprocess.run([sys.executable,"-m","memorable.cli","ingest-cc",
                    "tests/fixtures/sample.jsonl","--project","stablex","--db",db,"--fake","--no-filter"],check=True)
    out = subprocess.run([sys.executable,"skill/recall.py","--query","auth refresh loop",
                          "--project","stablex","--db",db,"--fake","--k","3"],
                         capture_output=True,text=True,check=True)
    payload = json.loads(out.stdout)
    assert "context" in payload and "trace" in payload
    assert payload["trace"]["hits"] >= 1
    assert "auth" in payload["context"].lower()
