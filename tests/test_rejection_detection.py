"""The rejection channel has to see how people actually disagree.

``memory_quality`` scores a memory by uses minus rejections. On this machine the
rejection side has never fired: 524 settled verdicts, zero rejections. That is
not a clean record, it is a blind detector -- every quality score in the product
is currently one-sided, counting hits with no misses.

The cause is that the patterns were written as the phrases a *wrong answer*
provokes in the abstract -- "no that's wrong", "that's incorrect" -- and people
do not talk that way to an agent they are working with. Sampling 947 real
human-typed turns from the local corpus, the existing detector fires on 2, and
one of those is "Actually we can include the changes from yesterday", which is
agreement.

What real disagreement looks like here, taken from that corpus:

    isn't the PR#85 and 85 identical in functionality ?
    I thought P0 includes c1,c2,c3,c4 and F-5 isn't it ?
    did we not cover the extensions ?
    we don't have that data since we haven't onboarded anyone yet

The dominant form is the **corrective question**: a rhetorical "isn't it /
didn't we / I thought" that asserts a correction while sounding like a query.
None of the previous patterns could see it.

These tests fix the behaviour against labelled real examples rather than
invented ones, because inventing the examples is how the original detector came
to match a register nobody uses.
"""
from __future__ import annotations

import pytest

# Real user turns from the local corpus, lightly trimmed. Labelled by hand.
CORRECTIONS = [
    "isn't the PR#85 and 85 identical in functionality ?",
    "I thought P0 includes c1,c2,c3,c4 and F-5 isn't it ?",
    "The PR was merged. When we were working on the Gltf reader did we not cover the extensions ?",
    "why do we have the KYB_TIER_FULLY_VERIFIED_MONTHLY_LIMIT_USD. I thought we only set limits on volume",
    "These are all payment related bugs shouldn't be we fix those together on a single PR ?",
    "no that's wrong, we moved off that months ago",
    "that's outdated, we switched to the new client",
]

#: Real corrections the detector does **not** catch, kept as a test so the
#: limit is visible rather than folded into a recall percentage.
#:
#: Neither carries a linguistic marker. "we don't have that data since we
#: haven't onboarded anyone yet" and "the in-memory texture support is
#: different from the separate PR" are flat declarative statements that happen
#: to contradict something said earlier; they are indistinguishable in form
#: from ordinary facts, and separating them needs the *prior* assistant turn,
#: not a pattern.
#:
#: Chasing them with regex would mean matching "is different from" or "we don't
#: have", which appear constantly in normal traffic. Missing a correction costs
#: one unrecorded rejection; a false one penalises whichever memory happened to
#: be in context. The asymmetry says stop here.
KNOWN_MISSES = [
    "we don't have that data since we haven't onboarded anyone yet",
    "The in-memory texture support is different from the separate PR for full gltf",
]

#: Turns that must NOT read as rejection. Agreement and ordinary questions
#: dominate real traffic, so a detector that fires on them would poison far
#: more scores than it fixes -- the failure mode of the previous version, in
#: the opposite direction.
NOT_CORRECTIONS = [
    "yes lets do that but as we are filing new issues make sure its not a duplicate",
    "subagent driven please yes",
    "lets do it, but if the adversarial review does not come up with a major bug",
    "if that matches the other libraries lets use the same",
    "Can you address the PR comments please ?",
    "will the deploy handle installing postgres as well and also seeding the demo data ?",
    "What version does the redis PR mention as the next version for it ?",
    "Help me with a summary of the PRs in simple english",
    "can we work on the issues 56 and 57 next",
    "Actually we can include the changes that we did yesterday as well",
    "first commit everything so far and then try it so we can just revert",
]


@pytest.mark.parametrize("text", CORRECTIONS)
def test_real_corrections_are_detected(text):
    from memor.feedback import looks_like_correction

    assert looks_like_correction(text), f"missed a real correction: {text!r}"


@pytest.mark.parametrize("text", NOT_CORRECTIONS)
def test_ordinary_turns_are_not_corrections(text):
    from memor.feedback import looks_like_correction

    assert not looks_like_correction(text), f"false positive: {text!r}"


def test_agreement_starting_with_actually_is_not_rejection():
    """The exact false positive the old list produced on the real corpus.

    "Actually we" was treated as pushback. In "Actually we can include the
    changes from yesterday as well" it introduces an addition, not a
    correction, and it was one of only two turns the old detector fired on in
    947 sampled.
    """
    from memor.feedback import looks_like_correction

    assert not looks_like_correction(
        "Actually we can include the changes that we did yesterday as well"
    )
    assert looks_like_correction("actually we stopped using that library")


def test_detector_is_selective_on_real_traffic():
    """A rate check, because both failure modes are silent.

    Firing on nothing leaves quality scores one-sided; firing on everything
    marks ordinary questions as harm. Against the hand-labelled set the
    detector must separate them cleanly rather than land somewhere in between.
    """
    from memor.feedback import looks_like_correction

    caught = sum(1 for t in CORRECTIONS if looks_like_correction(t))
    false = sum(1 for t in NOT_CORRECTIONS if looks_like_correction(t))

    assert caught == len(CORRECTIONS), f"recall {caught}/{len(CORRECTIONS)}"
    assert false == 0, f"{false} false positives out of {len(NOT_CORRECTIONS)}"


@pytest.mark.parametrize("text", KNOWN_MISSES)
def test_markerless_corrections_are_still_missed(text):
    """Pins the limit, so it stays a known gap rather than a silent one.

    These are genuine corrections with no linguistic marker -- declarative
    statements that contradict something said earlier. Telling them apart from
    ordinary facts needs the prior assistant turn, which the detector does not
    receive.

    If a future version reads the preceding turn and catches them, invert this.
    """
    from memor.feedback import looks_like_correction

    assert not looks_like_correction(text), (
        "markerless corrections are now caught -- move this case into "
        "CORRECTIONS and check the false-positive rate on real traffic"
    )


def test_an_interrupt_is_not_scored_as_rejection():
    """Interrupts are ambiguous, so they stay out of the signal.

    "[Request interrupted by user]" is common in the corpus and can mean the
    agent was wrong, or that the user changed their mind, or that they hit the
    wrong key. Scoring it as rejection would attribute harm to whichever memory
    happened to be in context.
    """
    from memor.feedback import looks_like_correction

    assert not looks_like_correction("[Request interrupted by user]")
    assert not looks_like_correction("[Request interrupted by user for tool use]")


def test_a_correction_hours_later_is_not_attributed_to_this_recall():
    """Rejection needs proximity, not just ordering.

    With the sharper detector, 85 of 170 'used' verdicts had a correction
    somewhere after them -- but the median gap was 330 minutes and the longest
    was ten days. A session that runs all day will contain pushback about
    something; blaming whichever memory was served that morning is the
    attribution bug the feedback rewrite already fixed once, arriving by a
    different route.

    The window is generous rather than tight: a correction can legitimately
    take a few turns to surface, and a missed rejection is cheaper than a
    misattributed one.
    """
    from memor.feedback import _rejected_after, _REJECTION_WINDOW_S

    served = 1_000_000.0
    prompt = "i thought we already fixed that"

    assert _rejected_after([(served + 60, prompt)], ["some reply"], served)
    assert not _rejected_after(
        [(served + _REJECTION_WINDOW_S + 60, prompt)], ["some reply"], served
    )


def test_an_undated_turn_still_counts_as_evidence():
    """Not every source stamps every turn, and dropping them poisons labels.

    A transcript with no usable timestamps would otherwise mark every memory
    'unused', which is the same failure the feedback rewrite recorded in the
    other direction. Undated turns keep counting.
    """
    from memor.feedback import _rejected_after

    assert _rejected_after([(0.0, "i thought we removed that")], [], 1_000_000.0)
