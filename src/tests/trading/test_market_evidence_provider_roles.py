from app.apps.trading.market_evidence import DEFAULT_MARKET_EVIDENCE_POLICY


def test_market_evidence_policy_serializes_independent_provider_roles():
    payload = DEFAULT_MARKET_EVIDENCE_POLICY.model_dump()

    assert payload["historical_canonical_provider"] == "yahoo"
    assert payload["live_quote_primary_provider"] == "ibkr"
    assert payload["live_quote_fallback_provider"] == "alpaca_iex"
    assert payload["gap_repair_fallback_provider"] == "alpaca_iex"
    assert payload["paper_fill_observation_provider"] == "alpaca_iex"
    assert payload["order_execution_provider"] is None
    assert "execution_provider" not in payload
    assert "shadow_bar_fallback_provider" not in payload


def test_market_evidence_legacy_accessors_are_compatibility_only():
    policy = DEFAULT_MARKET_EVIDENCE_POLICY

    assert policy.premarket_provider == policy.premarket_liquidity_provider
    assert policy.regular_bar_primary_provider == policy.historical_canonical_provider
    assert policy.shadow_bar_fallback_provider == policy.gap_repair_fallback_provider
    assert policy.execution_provider == policy.paper_fill_observation_provider
