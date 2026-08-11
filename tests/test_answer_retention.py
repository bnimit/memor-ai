"""The retention harness must be trustworthy before its numbers mean anything.

Its first version reported 8.9% retention at 0% compression -- impossible, since
nothing had been removed. The cause was the pairing: an agent edits the file on
disk, which has moved past an earlier read, so 358 of 393 quoted fragments were
never in the payload they were scored against. The harness was measuring how
often a file had changed, and calling it compression damage.

These tests pin the properties that make the measurement mean something: the
identity case must score 100%, deletion must be detected, and evidence that
never appeared in the payload must not be counted.
"""
import json

import pytest

from memor.eval.answer_retention import (
    MIN_EVIDENCE_CHARS,
    Case,
    extract_cases,
    score,
)

PAYLOAD = "\n".join([
    "def compute_total(items):",
    "    subtotal = sum(i.price for i in items)",
    "    tax = subtotal * TAX_RATE",
    "    return subtotal + tax",
    "",
    "TAX_RATE = 0.0825  # the number that matters",
    "",
] + [f"# filler line {i} with enough text to be worth removing" for i in range(60)])

EVIDENCE = "TAX_RATE = 0.0825  # the number that matters"


def _case(payload=PAYLOAD, evidence=(EVIDENCE,)):
    return Case(payload=payload, file_path="billing.py", evidence=list(evidence))


class TestHarnessIntegrity:
    def test_identity_compression_scores_perfect(self):
        """The sanity check the first version failed."""
        result = score([_case()], lambda text, path: text)
        assert result.retention_pct == 100.0
        assert result.saved_pct == 0.0

    def test_deleting_the_evidence_is_detected(self):
        result = score([_case()],
                       lambda text, path: text.replace(EVIDENCE, ""))
        assert result.retention_pct == 0.0

    def test_truncation_that_drops_the_middle_is_detected(self):
        def truncate(text, path):
            lines = text.splitlines()
            return "\n".join(lines[:2] + lines[-2:])

        assert score([_case()], truncate).retention_pct == 0.0

    def test_whitespace_reflow_is_not_counted_as_loss(self):
        """A compressor may reindent without destroying meaning."""
        def reflow(text, path):
            return "\n".join("  " + line.strip() for line in text.splitlines())

        assert score([_case()], reflow).retention_pct == 100.0

    def test_savings_are_reported_alongside_retention(self):
        """Retention without a savings figure invites reporting the easy half."""
        def half(text, path):
            lines = text.splitlines()
            keep = [l for l in lines if EVIDENCE in l or "def " in l]
            return "\n".join(keep)

        result = score([_case()], half)
        assert result.retention_pct == 100.0
        assert result.saved_pct > 50


class TestCaseMining:
    def _transcript(self, tmp_path, records):
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records))
        return p

    def _read(self, path, text, use_id="u1"):
        return [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": use_id, "name": "Read",
                 "input": {"file_path": path}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": text}]}},
        ]

    def _edit(self, path, old):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "e1", "name": "Edit",
             "input": {"file_path": path, "old_string": old,
                       "new_string": "replacement"}}]}}

    def test_a_grounded_edit_becomes_a_case(self, tmp_path):
        t = self._transcript(tmp_path,
                             self._read("billing.py", PAYLOAD)
                             + [self._edit("billing.py", EVIDENCE)])
        cases = extract_cases(t)
        assert len(cases) == 1
        assert cases[0].evidence == [EVIDENCE]

    def test_evidence_absent_from_the_payload_is_discarded(self, tmp_path):
        """The bug: the file on disk has moved past the read we captured."""
        t = self._transcript(tmp_path,
                             self._read("billing.py", PAYLOAD)
                             + [self._edit("billing.py",
                                           "text that was never in that payload at all")])
        assert extract_cases(t) == []

    def test_a_short_fragment_is_ignored(self, tmp_path):
        """Short strings match by chance and prove nothing."""
        short = PAYLOAD.splitlines()[0][:MIN_EVIDENCE_CHARS - 5]
        t = self._transcript(tmp_path,
                             self._read("billing.py", PAYLOAD)
                             + [self._edit("billing.py", short)])
        assert extract_cases(t) == []

    def test_an_edit_with_no_prior_read_is_not_a_case(self, tmp_path):
        t = self._transcript(tmp_path, [self._edit("billing.py", EVIDENCE)])
        assert extract_cases(t) == []

    def test_a_malformed_transcript_returns_nothing(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("not json\n{also not\n")
        assert extract_cases(p) == []
