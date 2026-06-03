from pathlib import Path
from memor.ingest.documents import parse_document

def test_parse_markdown_chunks_by_heading(tmp_path):
    p = tmp_path/"notes.md"
    p.write_text("# Auth design\nUse argon2.\n\n# Sync\nMutex around queue.\n")
    arts = parse_document(p, project="stablex", kind="note")
    assert len(arts) == 2
    assert arts[0].kind == "note" and arts[0].project == "stablex"
    assert "argon2" in arts[0].text and "Auth design" in arts[0].text
