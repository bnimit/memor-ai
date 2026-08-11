"""Request anatomy: get the image accounting right, or the answer is nonsense.

A base64 payload tokenises as a huge string. Feeding one to a tokenizer put
images at 31% of a session and made them look like the largest compression
opportunity available; pricing them the way a provider does puts them at 0.4%.
The error was 70x and it nearly directed real work at a phantom.

The cross-check that caught it belongs in the tests too: an anatomy that implies
far more tokens than the provider ever billed is measuring something the
provider does not.
"""
import json

import pytest

from memor.eval.request_anatomy import (
    COMPRESSIBLE,
    CONVERSATION,
    GENERATED_CODE,
    IMAGES,
    analyse,
    ceiling_pct,
    format_report,
    load_messages,
)


def _msg(role, blocks):
    return {"role": role, "content": blocks}


def _image(kb):
    # base64 inflates by 4/3, so this many chars decodes to about `kb` KB.
    return {"type": "image", "source": {"type": "base64",
                                        "media_type": "image/png",
                                        "data": "A" * int(kb * 1024 / 0.75)}}


class TestImageAccounting:
    def test_a_screenshot_is_not_counted_as_its_base64_length(self):
        """The 70x error. A 160 KB image is ~1-2K tokens, not ~113K."""
        a = analyse([_msg("user", [_image(160)])])
        assert a.tokens[IMAGES] < 10_000, (
            f"image priced at {a.tokens[IMAGES]:,} tokens; "
            "this is the base64-length mistake")

    def test_images_do_not_dominate_a_realistic_request(self):
        blocks = [_image(160)]
        conversation = [_msg("assistant", [{"type": "text", "text": "word " * 5000}])]
        a = analyse([_msg("user", blocks)] + conversation)
        assert a.share(IMAGES) < 50, "one screenshot cannot outweigh 5,000 words"

    def test_image_blocks_are_counted(self):
        a = analyse([_msg("user", [_image(10), _image(10)])])
        assert a.images == 2


class TestBucketing:
    def test_tool_results_are_the_compressible_bucket(self):
        a = analyse([_msg("user", [{"type": "tool_result", "tool_use_id": "t",
                                    "content": "output " * 100}])])
        assert a.tokens[COMPRESSIBLE] > 0
        assert a.tokens[GENERATED_CODE] == 0

    def test_tool_use_arguments_are_generated_code(self):
        a = analyse([_msg("assistant", [{"type": "tool_use", "name": "Edit",
                                         "input": {"new_string": "x" * 400}}])])
        assert a.tokens[GENERATED_CODE] > 0
        assert a.tokens[COMPRESSIBLE] == 0

    def test_text_is_conversation(self):
        a = analyse([_msg("assistant", [{"type": "text", "text": "hello " * 50}])])
        assert a.tokens[CONVERSATION] > 0

    def test_a_plain_string_message_is_conversation(self):
        a = analyse([{"role": "user", "content": "just a prompt"}])
        assert a.tokens[CONVERSATION] > 0

    def test_an_empty_request_does_not_divide_by_zero(self):
        a = analyse([])
        assert a.total == 0 and a.share(COMPRESSIBLE) == 0.0


class TestCeiling:
    def test_the_ceiling_is_the_compressible_share_times_the_rate(self):
        a = analyse([
            _msg("user", [{"type": "tool_result", "tool_use_id": "t",
                           "content": "x " * 1000}]),
            _msg("assistant", [{"type": "tool_use", "name": "Edit",
                                "input": {"new_string": "y " * 1000}}]),
        ])
        share = a.share(COMPRESSIBLE)
        assert ceiling_pct(a, rate_on_compressible=0.5) == pytest.approx(share * 0.5)

    def test_perfect_compression_still_cannot_exceed_the_slice(self):
        """Even removing every compressible token leaves the rest."""
        a = analyse([
            _msg("user", [{"type": "tool_result", "tool_use_id": "t",
                           "content": "x " * 100}]),
            _msg("assistant", [{"type": "text", "text": "y " * 900}]),
        ])
        assert ceiling_pct(a, rate_on_compressible=1.0) < 100.0

    def test_the_report_names_what_may_not_be_touched(self):
        a = analyse([
            _msg("assistant", [{"type": "tool_use", "name": "Edit",
                                "input": {"new_string": "x" * 100}}]),
            _msg("assistant", [{"type": "text", "text": "explaining " * 40}]),
        ])
        text = format_report(a)
        assert "corrupts edits" in text
        assert "cached prefix" in text


class TestLoading:
    def test_a_malformed_transcript_yields_nothing(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("not json\n{broken\n")
        assert load_messages(p) == []

    def test_only_user_and_assistant_records_are_loaded(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in [
            {"type": "summary", "message": {"role": "user", "content": "skip"}},
            {"type": "user", "message": {"role": "user", "content": "keep"}},
        ]))
        loaded = load_messages(p)
        assert len(loaded) == 1 and loaded[0]["content"] == "keep"
