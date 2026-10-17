from __future__ import annotations

from scripts.run_trading_strategy_v3_breakout_hold_holdout import (
    _status,
    development_eligible,
    select_development_champion,
)


def _row(
    variant_id: str,
    threshold: str,
    *,
    trades: int,
    expectancy: str,
    lcb: str,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "minimum_breakout_volume_ratio": threshold,
        "trade_count": trades,
        "expectancy_r": expectancy,
        "one_sided_90_lcb_r": lcb,
    }


def test_development_gate_requires_trade_count_positive_expectancy_and_positive_lcb() -> None:
    assert development_eligible(_row("ok", "1.0", trades=5, expectancy="0.30", lcb="0.01"), 5)
    assert not development_eligible(_row("few", "1.0", trades=4, expectancy="0.30", lcb="0.01"), 5)
    assert not development_eligible(_row("mean", "1.0", trades=5, expectancy="0", lcb="0.01"), 5)
    assert not development_eligible(_row("lcb", "1.0", trades=5, expectancy="0.30", lcb="0"), 5)


def test_champion_selection_is_predeclared_and_deterministic() -> None:
    rows = [
        _row("lower_lcb", "0", trades=20, expectancy="0.60", lcb="0.05"),
        _row("best_lcb_fewer", "1.5", trades=6, expectancy="0.25", lcb="0.12"),
        _row("best_lcb_more", "1.0", trades=8, expectancy="0.20", lcb="0.12"),
    ]
    champion = select_development_champion(rows, 5)
    assert champion is not None
    assert champion["variant_id"] == "best_lcb_more"

    tied = [
        _row("higher_threshold", "1.5", trades=8, expectancy="0.20", lcb="0.12"),
        _row("lower_threshold", "1.0", trades=8, expectancy="0.20", lcb="0.12"),
    ]
    champion = select_development_champion(tied, 5)
    assert champion is not None
    assert champion["variant_id"] == "lower_threshold"


def test_holdout_status_distinguishes_inconclusive_from_fail_and_pass() -> None:
    assert _status(_row("few", "1.0", trades=4, expectancy="1", lcb="0.5"), 5) == "inconclusive_low_trade_count"
    assert _status(_row("fail", "1.0", trades=5, expectancy="0.2", lcb="-0.1"), 5) == "fail"
    assert _status(_row("pass", "1.0", trades=5, expectancy="0.2", lcb="0.01"), 5) == "pass"
