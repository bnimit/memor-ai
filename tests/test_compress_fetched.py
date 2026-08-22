"""Tests for fetched-document classification.

The source guard calls a documentation page "source" because it contains a
fenced code sample, which held ~5% of context on the local corpus. These tests
pin the distinction between *is* code and *contains* code.

Labels come from provenance on 179 real blocked payloads: 150 file reads
(file_path present or majority line-numbered) and 29 fetched documents.
"""
from __future__ import annotations


def _docs_page() -> str:
    """A documentation page: prose, with fenced samples. Not a file."""
    return (
        "Fetched https://docs.example.com/guide (29105 bytes)\n\n"
        "# Webhooks\n\n"
        "Configure a webhook endpoint to receive events. Each delivery is\n"
        "signed so you can verify it came from us.\n\n"
        "```python\n"
        "def handler(request):\n"
        "    verify(request.headers['X-Sig'])\n"
        "    return 200\n"
        "```\n\n"
        + "Every event carries an id, a type, and a created timestamp.\n" * 40
        + "\n## Retries\n\n"
        + "Failed deliveries retry with exponential backoff for 24 hours.\n" * 40
    )


def _source_file() -> str:
    """A real file read. Must stay protected."""
    return "\n".join(
        [f"{i:6}\t" + line for i, line in enumerate([
            "import json",
            "from typing import Any",
            "",
            "class Handler:",
            "    def __init__(self, config: dict[str, Any]) -> None:",
            "        self.config = config",
            "        self._cache = {}",
            "",
            "    def process(self, payload: str) -> dict:",
            "        data = json.loads(payload)",
            "        if not data.get('id'):",
            "            raise ValueError('missing id')",
            "        return {'ok': True, 'id': data['id']}",
        ] * 8, start=1)]
    )


def test_fetched_document_is_not_classified_as_source():
    """A docs page with a code sample is prose, and prose compresses."""
    from memor.compress import detect_content_type

    assert detect_content_type(_docs_page()) != "source"


def test_real_file_read_is_still_protected():
    """The property the guard exists for. Must not regress."""
    from memor.compress import detect_content_type

    assert detect_content_type(_source_file()) == "source"


def test_fenced_code_survives_verbatim():
    """Prose between fences may compress; the sample inside must not."""
    from memor.compress import compress_text

    r = compress_text(_docs_page())
    for line in ("def handler(request):",
                 "    verify(request.headers['X-Sig'])",
                 "    return 200"):
        assert line in r.text, f"lost fenced code: {line!r}"


def test_fetched_document_actually_compresses():
    from memor.compress import compress_text

    r = compress_text(_docs_page())
    assert r.passthrough is False
    assert r.tokens_after < r.tokens_before


def test_detection_matches_provenance_on_the_real_corpus():
    """Guard rail against tuning the rule until it overfits.

    Runs the classifier over the frozen provenance-labelled set when it is
    present. Skipped in environments without it rather than failing, since the
    set is built from local sessions.
    """
    import json
    from pathlib import Path

    from memor.compress import detect_content_type

    path = Path.home() / ".jcode/scratch/guard_set.json"
    if not path.exists():
        import pytest

        pytest.skip("provenance-labelled set not present")

    data = json.loads(path.read_text())
    # Every genuine file read must still read as source.
    missed = [x for x in data["file"]
              if detect_content_type(x["text"]) != "source"]
    assert not missed, f"{len(missed)} real file reads lost protection"

    # No genuine file read may carry a fetch header either: that is the signal
    # the classifier keys on, so a collision would be silent corruption.
    from memor.compress.detect import _FETCHED_HEADER

    collisions = [x for x in data["file"]
                  if _FETCHED_HEADER.match(x["text"].lstrip()[:200])]
    assert not collisions, f"{len(collisions)} file reads carry a fetch header"

    # Fetched documents that are mostly prose should be released; ones that
    # are mostly fenced code are source in a thin wrapper and stay held. Assert
    # the rule rather than a rate, since the strict-provenance set is small.
    from memor.compress.detect import looks_like_fetched_document

    for item in data["fetched"]:
        text = item["text"]
        inside = False
        fenced = plain = 0
        for line in text.split("\n"):
            if line.lstrip().startswith("```"):
                inside = not inside
                continue
            if inside:
                fenced += len(line)
            else:
                plain += len(line)
        mostly_code = bool(fenced + plain) and fenced / (fenced + plain) > 0.5
        released = looks_like_fetched_document(text)
        assert released is not mostly_code, (
            f"{item['tokens']}-token doc: mostly_code={mostly_code} "
            f"but released={released}")


def test_hook_path_also_releases_fetched_documents():
    """The hook gates on `looks_like_source` before calling `compress_text`.

    That is the path with the real savings (83.5% measured), so a classifier
    improvement that only reaches the proxy is dead code where it matters most.
    """
    from memor.posttool_compress import build_response

    out = build_response({
        "tool_name": "bash",
        "tool_response": {"stdout": _docs_page(), "exit_code": 0},
    })
    assert out, "hook declined a fetched document the classifier released"
    text = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    for line in ("def handler(request):", "    return 200"):
        assert line in text, f"hook lost fenced code: {line!r}"


def test_hook_still_refuses_a_real_file_read():
    """The property that must not regress on the hook path either."""
    from memor.posttool_compress import build_response

    out = build_response({
        "tool_name": "bash",
        "tool_response": {"stdout": _source_file(), "exit_code": 0},
    })
    assert out == {}, "hook rewrote a file read"
