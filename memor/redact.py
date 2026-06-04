"""Secret detection and redaction — runs at ingest before embedding or storage.

Catches: API keys (AWS, OpenAI, GitHub, Anthropic, Stripe, etc.), JWTs,
PEM blocks, connection strings, .env-style assignments, high-entropy tokens.
Redacts in-place to preserve surrounding signal."""
from __future__ import annotations
import math
import re

_PLACEHOLDER = "[REDACTED]"

# --- Pattern-based detection ---

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
_AWS_SECRET = re.compile(r"(?:aws.{0,20})?[0-9a-zA-Z/+]{40}(?=\s|$|\")")
_OPENAI_KEY = re.compile(r"sk-[a-zA-Z0-9_-]{20,}")
_ANTHROPIC_KEY = re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}")
_GITHUB_TOKEN = re.compile(r"gh[ps]_[A-Za-z0-9_]{36,}")
_GITHUB_FINE = re.compile(r"github_pat_[A-Za-z0-9_]{20,}")
_STRIPE_KEY = re.compile(r"[sr]k_(live|test)_[A-Za-z0-9]{20,}")
_SLACK_TOKEN = re.compile(r"xox[bpras]-[A-Za-z0-9\-]{10,}")
_GENERIC_KEY = re.compile(r"(?:key|token|secret|password|apikey|api_key)[\"']?\s*[=:]\s*['\"]?([^\s'\"]{8,})", re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)
_CONN_STRING = re.compile(
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|sqlite)"
    r"://[^\s\"'`]{10,}", re.I)
_ENV_ASSIGNMENT = re.compile(
    r"^[A-Z_]{2,50}(?:_KEY|_SECRET|_TOKEN|_PASSWORD|_PASS|_API|_CREDENTIAL)[=:]\s*\S+",
    re.MULTILINE)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_key", _AWS_KEY),
    ("aws_secret", _AWS_SECRET),
    ("openai_key", _OPENAI_KEY),
    ("anthropic_key", _ANTHROPIC_KEY),
    ("github_token", _GITHUB_TOKEN),
    ("github_fine", _GITHUB_FINE),
    ("stripe_key", _STRIPE_KEY),
    ("slack_token", _SLACK_TOKEN),
    ("jwt", _JWT),
    ("pem", _PEM_BLOCK),
    ("connection_string", _CONN_STRING),
    ("env_assignment", _ENV_ASSIGNMENT),
    ("generic_key", _GENERIC_KEY),
]

# --- Entropy-based detection for unrecognized high-entropy tokens ---

_TOKEN_RE = re.compile(r"[A-Za-z0-9_/+\-]{20,}")

def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((count / n) * math.log2(count / n) for count in freq.values())

_ENTROPY_THRESHOLD = 4.0
_MIN_ENTROPY_LEN = 20


def detect_secrets(text: str) -> list[tuple[str, str]]:
    """Return list of (pattern_name, matched_string) for all secrets found."""
    found: list[tuple[str, str]] = []
    for name, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            matched = m.group(1) if m.lastindex and name == "generic_key" else m.group(0)
            found.append((name, matched))

    for m in _TOKEN_RE.finditer(text):
        token = m.group(0)
        if len(token) >= _MIN_ENTROPY_LEN and _shannon_entropy(token) >= _ENTROPY_THRESHOLD:
            already = any(token in s for _, s in found)
            if not already:
                found.append(("high_entropy", token))
    return found


def redact_text(text: str) -> tuple[str, int]:
    """Redact secrets in text. Returns (redacted_text, count_of_redactions)."""
    secrets = detect_secrets(text)
    if not secrets:
        return text, 0
    result = text
    count = 0
    for _, secret in sorted(secrets, key=lambda x: -len(x[1])):
        if secret in result:
            result = result.replace(secret, _PLACEHOLDER)
            count += 1
    return result, count


def scan_artifacts(store) -> list[dict]:
    """Scan all active artifacts for secrets. Returns list of findings."""
    rows = store.db.execute(
        "SELECT id, project, text, kind FROM artifacts WHERE active = 1"
    ).fetchall()
    findings = []
    for r in rows:
        secrets = detect_secrets(r["text"])
        if secrets:
            findings.append({
                "artifact_id": r["id"],
                "project": r["project"],
                "kind": r["kind"],
                "secrets": [(name, s[:20] + "...") for name, s in secrets],
            })
    return findings


def purge_secrets_from_db(store) -> int:
    """Redact secrets from all active artifacts in place. Returns count of affected artifacts."""
    rows = store.db.execute(
        "SELECT id, text FROM artifacts WHERE active = 1"
    ).fetchall()
    affected = 0
    for r in rows:
        redacted, count = redact_text(r["text"])
        if count > 0:
            store.db.execute(
                "UPDATE artifacts SET text = ? WHERE id = ?",
                (redacted, r["id"]))
            affected += 1
    if affected:
        store.db.commit()
    return affected
