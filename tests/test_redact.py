"""Tests for secret detection, redaction at ingest, and DB purging."""
import time
from memor.redact import detect_secrets, redact_text, scan_artifacts, purge_secrets_from_db
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_store(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    e = FakeEmbedder(dim=16)
    return s, e


# --- detect_secrets ---

def test_detect_aws_key():
    text = "my key is AKIAIOSFODNN7EXAMPLE and it works"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "aws_key" in names


def test_detect_openai_key():
    text = "export OPENAI_API_KEY=sk-proj-abcdefghij1234567890abcdefghij"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "openai_key" in names


def test_detect_anthropic_key():
    text = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij1234567890"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "anthropic_key" in names


def test_detect_github_token():
    text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "github_token" in names


def test_detect_github_fine_grained():
    text = "github_pat_ABCDEFGHIJKLMNOPQRST1234567890"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "github_fine" in names


def test_detect_stripe_key():
    prefix = "sk_test_"
    text = f"{prefix}{'X' * 24}"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "stripe_key" in names


def test_detect_jwt():
    text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "jwt" in names


def test_detect_connection_string():
    text = "DATABASE_URL=postgres://admin:s3cret@db.example.com:5432/mydb"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "connection_string" in names


def test_detect_pem_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJc\n-----END RSA PRIVATE KEY-----"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "pem" in names


def test_detect_env_assignment():
    text = "DB_PASSWORD=hunter2\nAPP_NAME=myapp"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "env_assignment" in names


def test_detect_generic_key_value():
    text = 'config = {"api_key": "abcdef1234567890abcdef"}'
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "generic_key" in names


def test_detect_slack_token():
    text = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
    secrets = detect_secrets(text)
    names = [n for n, _ in secrets]
    assert "slack_token" in names


def test_no_false_positive_normal_code():
    text = "we decided to use argon2 for password hashing because it is memory hard"
    secrets = detect_secrets(text)
    assert len(secrets) == 0


def test_no_false_positive_function_names():
    text = "def get_user_by_id(user_id): return db.query(User).filter_by(id=user_id).first()"
    secrets = detect_secrets(text)
    assert len(secrets) == 0


def test_no_false_positive_short_tokens():
    text = "the token count is 150 and the model is gpt-4o"
    secrets = detect_secrets(text)
    assert len(secrets) == 0


# --- redact_text ---

def test_redact_replaces_secret():
    text = "use this key: sk-proj-abcdefghij1234567890abcdefghij to auth"
    redacted, count = redact_text(text)
    assert count >= 1
    assert "sk-proj" not in redacted
    assert "[REDACTED]" in redacted
    assert "use this key:" in redacted
    assert "to auth" in redacted


def test_redact_preserves_clean_text():
    text = "the architecture decision was to use PostgreSQL over Redis"
    redacted, count = redact_text(text)
    assert count == 0
    assert redacted == text


def test_redact_multiple_secrets():
    text = "AKIAIOSFODNN7EXAMPLE and sk-proj-abcdefghij1234567890abcdefghij"
    redacted, count = redact_text(text)
    assert count >= 2
    assert "AKIA" not in redacted
    assert "sk-proj" not in redacted


def test_redact_connection_string_preserves_context():
    text = "the database is at postgres://admin:s3cret@db.example.com:5432/mydb for prod"
    redacted, count = redact_text(text)
    assert count >= 1
    assert "s3cret" not in redacted
    assert "for prod" in redacted


# --- ingest integration ---

def test_ingest_redacts_secrets(tmp_path):
    import json
    transcript = tmp_path / "session1.jsonl"
    lines = [
        json.dumps({"type": "user", "timestamp": "2025-01-01T00:00:00Z",
                     "message": {"role": "user",
                                 "content": "my API key is sk-proj-abcdefghij1234567890abcdefghij please use it"}}),
        json.dumps({"type": "assistant", "timestamp": "2025-01-01T00:01:00Z",
                     "message": {"role": "assistant",
                                 "content": "I'll use that key to authenticate with the API"}}),
    ]
    transcript.write_text("\n".join(lines))
    from memor.ingest.claude_code import parse_transcript
    arts = parse_transcript(transcript, "test-project")
    for a in arts:
        assert "sk-proj" not in a.text
        if "key" in a.text.lower():
            assert "[REDACTED]" in a.text


# --- scan and purge ---

def test_scan_finds_secrets_in_db(tmp_path):
    s, e = _make_store(tmp_path)
    art = Artifact(id="leak1", kind="memory", project="p", source="distill",
                   text="use key AKIAIOSFODNN7EXAMPLE for S3",
                   token_count=8, created_at=100.0, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    findings = scan_artifacts(s)
    assert len(findings) == 1
    assert findings[0]["artifact_id"] == "leak1"


def test_scan_clean_db(tmp_path):
    s, e = _make_store(tmp_path)
    art = Artifact(id="clean1", kind="memory", project="p", source="distill",
                   text="use argon2 for password hashing",
                   token_count=6, created_at=100.0, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    findings = scan_artifacts(s)
    assert len(findings) == 0


def test_purge_redacts_in_place(tmp_path):
    s, e = _make_store(tmp_path)
    art = Artifact(id="leak2", kind="memory", project="p", source="distill",
                   text="connect with postgres://admin:s3cret@db.example.com:5432/prod",
                   token_count=10, created_at=100.0, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    count = purge_secrets_from_db(s)
    assert count == 1
    row = s.db.execute("SELECT text FROM artifacts WHERE id='leak2'").fetchone()
    assert "s3cret" not in row["text"]
    assert "[REDACTED]" in row["text"]
