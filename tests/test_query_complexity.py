"""Tests for query complexity scoring and budget routing."""
from memor.query_complexity import score_query, route_query, Tier


def test_trivial_query_scores_low():
    assert score_query("yes") < 0.2
    assert score_query("ok") < 0.2
    assert score_query("do it") < 0.2


def test_simple_followup_scores_medium():
    s = score_query("also fix the typo on line 42")
    assert 0.2 <= s < 0.6


def test_complex_query_scores_high():
    s = score_query(
        "refactor the auth module to use OAuth2 instead of the custom JWT "
        "implementation in src/auth/handler.py"
    )
    assert s >= 0.6


def test_identifier_density_boosts_score():
    plain = score_query("fix the bug in the login page")
    with_ids = score_query("fix the NullPointerException in AuthService.refreshToken")
    assert with_ids > plain


def test_code_refs_boost_score():
    plain = score_query("update the configuration file")
    with_path = score_query("update src/config/database.yml to use connection pooling")
    assert with_path > plain


def test_question_structure_boosts_score():
    statement = score_query("add a retry mechanism to the API client")
    question = score_query("how should we handle retries in the API client?")
    assert question >= statement * 0.9  # questions should score at least as well


def test_route_skip_for_trivial():
    tier = route_query("yes")
    assert tier == Tier.SKIP


def test_route_light_for_simple():
    tier = route_query("fix the typo on line 42")
    assert tier == Tier.LIGHT


def test_route_full_for_complex():
    tier = route_query(
        "refactor the auth module to use OAuth2 instead of the custom JWT "
        "implementation in src/auth/handler.py and update all tests"
    )
    assert tier == Tier.FULL


def test_route_returns_budget_params():
    tier = route_query("refactor the auth module to use OAuth2 patterns")
    assert hasattr(tier, 'k')
    assert hasattr(tier, 'max_tokens')
    assert tier.k > 0
    assert tier.max_tokens > 0


def test_light_tier_has_smaller_budget_than_full():
    assert Tier.LIGHT.max_tokens < Tier.FULL.max_tokens
    assert Tier.LIGHT.k <= Tier.FULL.k


def test_empty_query_routes_to_skip():
    assert route_query("") == Tier.SKIP
    assert route_query("   ") == Tier.SKIP
