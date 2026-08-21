from __future__ import annotations
import json
import re

# Keywords that identify code only when they START a line. Matching them
# anywhere would fire on ordinary prose — "We had to import the data by hand
# because the export was broken" contains two of them.
_LINE_INITIAL_MARKERS = (
    "def ", "class ", "import ", "from ", "async def", "lambda ", "@",
    "function ", "const ", "let ", "var ", "export ", "module.exports",
    "package ", "func ", "fn ", "impl ", "pub ", "type ", "struct ",
    "public ", "private ", "protected ", "static ", "namespace ", "using ",
    "#include", "#define", "#!", "<!DOCTYPE", "<html", "<script", "<style",
    "```", "if (", "for (", "while (", "switch (", "} else",
)

# Substrings so syntactically distinctive that prose effectively never has them.
_STRONG_MARKERS = ("from __future__", "#include <", "=> {", "});", "</", "/>")

# pytest / go test / jest / rspec result lines.
_TEST_RESULT = re.compile(
    r"\b(PASSED|FAILED|SKIPPED|XFAIL|XPASS)\b"
    r"|^\s*---\s*(PASS|FAIL|SKIP)\b"      # go test: "--- PASS: TestThing (0.00s)"
    r"|^\s*(ok|FAIL)\s+\S"                # go test package summary lines
    r"|^\s*[✓✗√×]\s"                      # jest / mocha / vitest
)

#: pytest's default progress output: "tests/test_x.py ....F..  [ 12%]". This is
#: the single most common shape of noisy test output an agent pastes into a
#: transcript, and without it a 400-line run is classified as generic prose and
#: passed through whole. The percentage suffix is what makes it unambiguous:
#: the line must end in a bracketed percentage and carry a run of status
#: characters before it, which prose and source do not do.
#: Two shapes: verbose ("tests/test_x.py ....  [ 12%]") and quiet, where
#: pytest -q emits a bare run of status characters with no filename at all
#: ("........  [  5%]"). Requiring a leading token missed the quiet form
#: entirely, which is the shape CI logs and `-q` runs actually produce.
_TEST_PROGRESS = re.compile(
    r"^(?:\S+\s+)?[.sFExX✓✗]{2,}\s*\[\s*\d{1,3}%\]\s*$"
)

#: Fraction of lines that must be pytest progress before the payload is
#: treated as test output.
#:
#: A bare count is not enough, and the failure is not hypothetical: an
#: implementation report in this repo quotes four pytest lines among 145 lines
#: of prose. On a count-of-three rule it was reclassified from prose to log and
#: the crusher deleted 86 lines of explanation. Genuine test output is
#: *dominated* by these lines; a document that merely quotes some is not.
_TEST_PROGRESS_MIN_RATIO = 0.15

#: A line that is essentially just a filesystem path, optionally with the
#: decoration `git --stat` and friends add (` path/to/x.py | 12 +++--`), a
#: leading bullet, or a tree-drawing prefix. Deliberately anchored: a path
#: mentioned mid-sentence is prose, a path *as* the line is a manifest entry.
_MANIFEST_LINE = re.compile(
    r"^\s*[-*+|`\u2502\u251c\u2514\u2500\s]*"      # bullets / tree glyphs
    r"[\w.@~][\w.@+\-]*(?:[/\\][\w.@+\-]+)+"        # a path with a separator
    r"\s*(?:\|[^|]*)?[,;]?\s*$"                     # optional ` | 12 +++` tail
)

#: Fraction of lines that must look like manifest entries before a payload is
#: treated as a file list rather than a log.
#:
#: Same lesson as ``_TEST_PROGRESS_MIN_RATIO``, learned from the opposite
#: direction: `gh pr view --files` output carried an ISO date on 3 of 84 lines,
#: which was enough to classify 70 filenames as a log. The crusher then kept
#: the first and last five and deleted 66 paths — and for a manifest the list
#: *is* the answer, so the agent received a confident, wrong, shorter answer.
#: A manifest is *dominated* by path lines; a log that mentions a few files is
#: not.
_MANIFEST_MIN_RATIO = 0.5

#: Below this a "manifest" is too short for the distinction to matter, and the
#: log crusher's first/last-five rule already preserves it whole.
_MANIFEST_MIN_LINES = 12


def looks_like_manifest(text: str) -> bool:
    """True when a payload is predominantly a list of paths.

    Manifests (``git log --stat``, ``gh pr view --files``, ``find``, ``ls -R``)
    are structurally indistinguishable from logs to a keyword sniffer, because
    build logs also name files. The difference is density: in a manifest almost
    every line *is* a path, and dropping the middle destroys the payload's
    entire content rather than trimming noise from it.
    """
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < _MANIFEST_MIN_LINES:
        return False
    hits = sum(1 for ln in lines if _MANIFEST_LINE.match(ln))
    return hits / len(lines) >= _MANIFEST_MIN_RATIO


#: Extension -> content type. When a payload came from a file we know the type
#: for certain, and guessing from bytes is strictly worse: `var(--warn)` in a
#: stylesheet is enough to make a heuristic call it a log and crush it. The
#: filename is already in the agent's tool call; consult it before the content.
_EXT_CONTENT_TYPE = {
    # Never crushed — bodies are elided by the code path, never by line rules.
    **{e: "source" for e in (
        ".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".rs", ".java", ".kt", ".kts", ".swift", ".c", ".h", ".cc", ".cpp",
        ".hpp", ".cs", ".rb", ".php", ".scala", ".sh", ".bash", ".zsh",
        ".sql", ".proto", ".graphql", ".vue", ".svelte", ".css", ".scss",
        ".html", ".htm", ".xml", ".toml", ".ini", ".tf",
    )},
    # Prose: nothing safely separable, so passed through.
    **{e: "text" for e in (".md", ".mdx", ".rst", ".txt", ".adoc")},
    # Genuinely crushable structured output.
    ".json": "json",
    ".jsonl": "json",
    ".log": "log",
    ".yaml": "text",
    ".yml": "text",
}


def content_type_for_path(file_path: str | None) -> str | None:
    """Content type implied by a filename, or None when unknown."""
    if not file_path:
        return None
    lowered = file_path.lower().split("?", 1)[0]
    for ext, kind in _EXT_CONTENT_TYPE.items():
        if lowered.endswith(ext):
            return kind
    return None


_SHEBANG = re.compile(r"^#!\s*/")
# A closing brace or semicolon ending a line is a strong structural code signal.
_CODE_LINE_END = re.compile(r"[;{}\)]\s*$")


def looks_like_source(text: str) -> bool:
    """True when text is source code rather than machine output.

    This is a safety gate, not a classifier. The log crusher deletes
    "repetitive" lines, which is correct for logs and catastrophic for code —
    a file read comes back mutilated with no error, and the agent then edits
    against content that was never in the file. Source code must never reach a
    crusher, so this errs toward saying yes.
    """
    if not text:
        return False
    if _SHEBANG.match(text.lstrip()[:200]):
        return True

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False

    hits = {m for m in _STRONG_MARKERS if m in text}
    for marker in _LINE_INITIAL_MARKERS:
        if any(ln.startswith(marker) for ln in lines):
            hits.add(marker)
        if len(hits) >= 2:
            return True
    if len(hits) >= 2:
        return True

    # Fall back to structure: lots of lines ending in ; { } ) is code, not output.
    if len(lines) >= 10:
        structural = sum(1 for ln in lines if _CODE_LINE_END.search(ln))
        if structural / len(lines) >= 0.30:
            return True
    return False


def detect_content_type(text: str, file_path: str | None = None) -> str:
    """Detect content type: json | search | source | log | text.

    ``source`` is a refusal, not a compressor: it marks content that must be
    passed through untouched. Checked after ``search`` so that grep output over
    a codebase still compresses.

    When ``file_path`` is known it decides, because a filename is evidence and
    the content heuristics are only a guess. Sniffing is for payloads with no
    file behind them — shell output, grep results, API responses.
    """
    known = content_type_for_path(file_path)
    if known is not None:
        return known

    # Try JSON first
    try:
        json.loads(text)
        return "json"
    except (json.JSONDecodeError, ValueError):
        pass

    lines = [line for line in text.split('\n') if line.strip()]

    # Check for search results (≥3 lines matching path:line: pattern).
    # The leading token must look like a path — contain a separator or a file
    # extension — otherwise clock times such as "10:00:00" read as matches.
    search_pattern = re.compile(
        r'^(?:[^\s:]*[/\\][^\s:]*|[^\s:]+\.[A-Za-z0-9_]+):\d+:'
    )
    search_matches = sum(1 for line in lines if search_pattern.match(line))
    if search_matches >= 3:
        return "search"

    # Test-runner output before the source guard: `--- PASS: TestX (0.00s)` is
    # unambiguous, but its trailing `)` reads as code to the structural check.
    if sum(1 for line in lines if _TEST_RESULT.search(line)) >= 3:
        return "log"
    # Progress lines additionally require density, because unlike an explicit
    # "FAILED test_x" marker they appear verbatim inside prose that discusses
    # a test run.
    progress = sum(1 for line in lines if _TEST_PROGRESS.match(line))
    if progress >= 3 and progress / len(lines) >= _TEST_PROGRESS_MIN_RATIO:
        return "log"

    # Guard source code before the log heuristic. Code trips it constantly:
    # `var(--warn)` in CSS matches \bWARN\b, and any file with timestamps in
    # strings clears the threshold.
    if looks_like_source(text):
        return "source"

    # Check for log format (≥3 timestamp-like or log-level tokens)
    log_indicators = 0
    log_pattern = re.compile(r'\b(INFO|DEBUG|WARN(ING)?|ERROR|FATAL|CRITICAL|TRACE)\b', re.IGNORECASE)
    timestamp_pattern = re.compile(r'\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}')

    for line in lines:
        if log_pattern.search(line) or timestamp_pattern.search(line):
            log_indicators += 1
            if log_indicators >= 3:
                # A manifest is not a log, however many dates it carries. Commit
                # listings and PR file lists pair timestamps with paths, and the
                # log crusher keeps only the first and last five lines — which
                # for a file list deletes the answer and returns a shorter one
                # that looks complete.
                if looks_like_manifest(text):
                    return "text"
                return "log"

    return "text"
