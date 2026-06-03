from pathlib import Path
from memor.ingest.claude_code import parse_transcript

def test_parse_transcript_yields_chunks():
    f = Path(__file__).parent / "fixtures" / "sample.jsonl"
    arts = parse_transcript(f, project="stablex", filter_noise=False)
    assert len(arts) == 2
    assert all(a.kind == "session_chunk" and a.project == "stablex" for a in arts)
    assert "auth refresh loop" in arts[0].text
    assert "401" in arts[1].text
    assert arts[0].created_at < arts[1].created_at
    assert arts[0].meta["session_id"] == arts[1].meta["session_id"]  # same file = same session
