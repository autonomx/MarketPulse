from __future__ import annotations

"""Diagnostic replay that tests an absolute-liquidity fallback for missing TOD RVOL.

Production gap_pullback_v1 remains unchanged. For this diagnostic only, a candidate
whose TOD RVOL is missing is allowed past that gate *only if* it already satisfies
the configured absolute premarket-dollar-volume threshold. Numeric TOD RVOL values
are not altered and must still satisfy the configured minimum.
"""

import sys
from decimal import Decimal
from pathlib import Path

import app.apps.trading.strategy_backtest as _backtest_module
from app.apps.trading.strategies.gap_pullback import evaluate_gap_pullback as _production_evaluate
from app.apps.trading.strategies.models import GapPullbackConfig
import scripts.run_trading_strategy_liquidity_sweep as _sweep


def _diagnostic_evaluate(candidate, bars, config: GapPullbackConfig | None = None):
    active = config or GapPullbackConfig()
    diagnostic_candidate = candidate
    if (
        candidate.tod_rvol is None
        and candidate.premarket_dollar_volume >= active.minimum_premarket_dollar_volume
    ):
        # The production evaluator hard-rejects None before any market structure is
        # inspected. For this experiment, represent "absolute-liquidity fallback
        # accepted" at exactly the minimum numeric threshold so every downstream
        # structure/risk rule executes unchanged. TOD RVOL is not a quality-score
        # input, so this does not inflate deterministic quality scoring.
        diagnostic_candidate = candidate.model_copy(update={"tod_rvol": active.minimum_tod_rvol})
    return _production_evaluate(diagnostic_candidate, bars, active)


def _output_dir(argv: list[str]) -> Path:
    try:
        index = argv.index("--output-dir")
        return Path(argv[index + 1])
    except (ValueError, IndexError):
        return Path("artifacts/missing-rvol-diagnostic")


def main() -> int:
    _backtest_module.evaluate_gap_pullback = _diagnostic_evaluate
    print(
        "DIAGNOSTIC POLICY: missing TOD RVOL is accepted only after the absolute "
        "premarket-dollar-volume gate passes; numeric TOD RVOL still requires >=5x."
    )
    result = _sweep.main()
    output_dir = _output_dir(sys.argv[1:])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "diagnostic-policy.md").write_text(
        "# Missing TOD RVOL diagnostic policy\n\n"
        "This artifact is **not** the production v1.1 policy. For this replay only, "
        "a candidate with missing TOD RVOL is allowed to continue when it has already "
        "passed the configured absolute premarket-dollar-volume gate. Numeric TOD RVOL "
        "still must meet the strict v1.1 minimum of 5x. All market-structure, quality, "
        "execution and risk rules are unchanged.\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
