from typer.testing import CliRunner
from memorable.cli import app

def test_cli_query_smoke(tmp_path, monkeypatch):
    runner = CliRunner()
    db = str(tmp_path/"m.db")
    # ingest the bundled fixture transcript
    r1 = runner.invoke(app, ["ingest-cc", "tests/fixtures/sample.jsonl",
                             "--project","stablex","--db",db,"--fake","--no-filter"])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(app, ["query","auth refresh loop","--project","stablex","--db",db,"--fake"])
    assert r2.exit_code == 0
    assert "auth" in r2.output.lower()
