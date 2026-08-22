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


def test_every_payload_shape_in_the_corpus_is_classified_correctly():
    """Regression cover for each shape the real corpus contains.

    An earlier version of this test read a JSON set out of the scratch
    directory. Scratch is the live TMPDIR and gets cleared, which silently
    turned this into a skip -- a safety check that vanishes when someone tidies
    up is not a safety check. Committing the real payloads was not an option
    either: 1.4 MB of the user's source in the repo, and truncating to shrink
    it changed the classification the fixture existed to pin.

    So the shapes are reproduced synthetically. The six below are every
    (kind, tool, line-numbered) combination present across 149 real
    guard-blocked payloads: read/batch/bash line-numbered file reads, edit and
    agentgrep output that is not, and fetched jcode_docs pages.
    """
    from memor.compress import detect_content_type

    body = [
        "import json",
        "from typing import Any",
        "",
        "class Handler:",
        "    def __init__(self, config: dict[str, Any]) -> None:",
        "        self.config = config",
        "",
        "    def process(self, payload: str) -> dict:",
        "        data = json.loads(payload)",
        "        if not data.get('id'):",
        "            raise ValueError('missing id')",
        "        return {'ok': True}",
    ] * 8

    numbered = "\n".join(f"{i:6}\t{l}" for i, l in enumerate(body, 1))
    plain = "\n".join(body)
    edit_style = "The file has been updated. Result:\n" + numbered
    grep_style = "\n".join(f"src/mod.py:{i}: {l}" for i, l in enumerate(body, 1))

    # Assert the property, not the label. What matters is that no code line is
    # removed; several routes achieve that. `source` refuses outright, `text`
    # runs a lossless tidy, `search` folds paths without dropping matches. An
    # earlier draft asserted `== "source"` and failed on edit output, which is
    # classified `text` and still loses nothing.
    from memor.compress import compress_text

    for label, text in (
        ("read (line-numbered)", numbered),
        ("batch (line-numbered)", "--- [1] read ---\n" + numbered),
        ("bash cat (line-numbered)", numbered),
        ("edit result", edit_style),
        ("plain source dump", plain),
    ):
        result = compress_text(text)
        # A line carrying only a line-number prefix is a blank line in the
        # file; collapsing those is the lossless tidy doing its job.
        lost = [l for l in text.split("\n")
                if l.strip() and not l.rstrip().rstrip("\t").strip().isdigit()
                and l.split("\t", 1)[-1].strip()
                and l not in result.text]
        assert not lost, (
            f"{label}: {len(lost)} code lines removed, first {lost[0][:60]!r}")

    # grep-shaped output routes to `search`, which folds the repeated path into
    # a header and groups line numbers. The format changes; no match text does.
    grep_result = compress_text(grep_style)
    assert grep_result.content_type == "search"
    for line in body:
        if line.strip():
            assert line in grep_result.text, f"search dropped {line[:40]!r}"

    # The one shape that must be released.
    docs = (
        "Source: `docs/HOOKS.md` (bundled with this Jcode build)\n\n"
        "# Lifecycle Hooks\n\n"
        + "jcode runs external commands at well-defined points.\n" * 50
        + "```toml\n[hooks]\nturn_end = \"~/bin/notify\"\n```\n"
        + "Observers are detached and fire-and-forget.\n" * 50
    )
    assert detect_content_type(docs) != "source"


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
