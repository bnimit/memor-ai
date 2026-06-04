from memor.ingest.claude_code import _signal_score, _strip_system_reminders, parse_transcript
from pathlib import Path


def test_user_question_scores_positive():
    assert _signal_score("How does the retriever handle edge expansion?", "user", 12) > 0

def test_assistant_decision_scores_positive():
    text = "We decided to use argon2 instead of bcrypt because it's memory-hard."
    assert _signal_score(text, "assistant", 15) > 0

def test_assistant_bugfix_scores_positive():
    text = "The root cause was a race condition in the token refresh loop."
    assert _signal_score(text, "assistant", 14) > 0

def test_assistant_lesson_scores_positive():
    text = "Always use WAL mode when you have concurrent readers and writers on SQLite."
    assert _signal_score(text, "assistant", 15) > 0

def test_assistant_code_rationale_scores_positive():
    text = "We use a Unix socket instead of TCP because it avoids port conflicts:\n```python\nserver = await asyncio.start_unix_server(handle, path)\n```"
    assert _signal_score(text, "assistant", 25) > 0

def test_short_filler_scores_zero():
    assert _signal_score("Let me check that for you.", "assistant", 6) == 0

def test_very_short_text_scores_zero():
    assert _signal_score("OK", "assistant", 1) == 0

def test_base64_blob_scores_zero():
    blob = "A" * 300
    assert _signal_score(blob, "assistant", 75) == 0

def test_file_listing_scores_zero():
    text = "/src/main.py\n/src/utils.py\n/tests/test_main.py\n/README.md"
    assert _signal_score(text, "assistant", 8) == 0

def test_permission_prompt_scores_zero():
    assert _signal_score("Allow Read access to /src/main.py?", "user", 8) == 0

def test_strip_system_reminders():
    text = "Hello <system-reminder>secret stuff</system-reminder> world"
    assert _strip_system_reminders(text) == "Hello  world"

def test_strip_system_reminders_nothing_left():
    text = "<system-reminder>only this</system-reminder>"
    assert _strip_system_reminders(text).strip() == ""

def test_parse_transcript_deduplicates(tmp_path):
    lines = [
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}',
        '{"type":"user","timestamp":"2026-05-01T10:00:01Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}',
        '{"type":"assistant","timestamp":"2026-05-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"The root cause was a race condition in the token refresh. Here is the fix for the auth handler."}]}}',
    ]
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join(lines))
    arts = parse_transcript(f, project="test", filter_noise=True)
    user_texts = [a.text for a in arts if a.meta.get("role") == "user"]
    assert len(user_texts) <= 1

def test_parse_transcript_skips_non_user_assistant(tmp_path):
    lines = [
        '{"type":"system","timestamp":"2026-05-01T10:00:00Z","message":{"role":"system","content":"system prompt"}}',
        '{"type":"attachment","timestamp":"2026-05-01T10:00:01Z","attachment":{"type":"hook"}}',
        '{"type":"user","timestamp":"2026-05-01T10:00:02Z","message":{"role":"user","content":"How does the retriever handle edge expansion in the graph?"}}',
    ]
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join(lines))
    arts = parse_transcript(f, project="test", filter_noise=True)
    assert len(arts) == 1
    assert arts[0].meta["role"] == "user"
