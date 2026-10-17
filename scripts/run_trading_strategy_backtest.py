from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_range_backtest import (
    StrategyRangeBacktestRequest,
    run_strategy_range_backtest,
)
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument


_ET = ZoneInfo("America/New_York")
_STRICT_PREMARKET_DOLLAR_VOLUME = Decimal("10000000")


def _decimal(value: str) -> Decimal:
    return Decimal(value)


def strict_v11_strategy(
    *,
    minimum_premarket_dollar_volume: Decimal = _STRICT_PREMARKET_DOLLAR_VOLUME,
) -> TradingStrategyConfigDocument:
    config = GapPullbackConfig(
        strategy_id="gap_pullback_v1",
        strategy_version="1.1.0",
        structure_interval="5m",
        execution_interval="1m",
        universe_scan_time_et=time(9, 20),
        auto_archive_daily_universe=True,
        universe_archive_grace_minutes=10,
        universe_discovery_count=50,
        minimum_gap_pct=Decimal("20"),
        minimum_price=Decimal("0.50"),
        maximum_price=Decimal("20"),
        minimum_premarket_dollar_volume=minimum_premarket_dollar_volume,
        minimum_tod_rvol=Decimal("5"),
        maximum_spread_bps=Decimal("150"),
        preferred_float_min_shares=Decimal("2000000"),
        preferred_float_max_shares=Decimal("30000000"),
        float_preference_mode="score",
        require_catalyst_evidence=True,
        reject_dilution_flags=(
            "registered_offering",
            "atm",
            "warrants",
            "convertible",
            "equity_line",
        ),
        opening_impulse_min_pct=Decimal("8"),
        pullback_min_pct=Decimal("15"),
        pullback_max_pct=Decimal("55"),
        pullback_volume_max_ratio=Decimal("0.70"),
        higher_low_buffer_bps=Decimal("20"),
        breakout_volume_ratio=Decimal("1.25"),
        pivot_left_bars=2,
        pivot_right_bars=2,
        volume_lookback_bars=10,
        require_breakout_hold=True,
        breakout_hold_bars=1,
        breakout_hold_tolerance_bps=Decimal("25"),
        minimum_quality_score=7,
        stop_buffer_bps=Decimal("15"),
        reward_multiple=Decimal("2"),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
    )
    risk = StrategyRiskProfile(
        risk_per_trade_pct=Decimal("0.35"),
        max_daily_loss_pct=Decimal("1.5"),
        max_open_risk_pct=Decimal("1.0"),
        max_positions=3,
        max_trades_per_day=5,
        max_trade_value=Decimal("25000"),
        one_trade_per_symbol_per_day=True,
        max_spread_bps=Decimal("150"),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
        force_flat_et=time(15, 55),
        kill_switch=False,
    )
    return TradingStrategyConfigDocument(
        strategy_id="actions-gap-pullback-v11",
        account_id="actions-backtest",
        strategy_kind="gap_pullback_v1",
        strategy_version="1.1.0",
        mode="off",
        config=config,
        risk=risk,
        enabled=False,
    )


def _dates(start_raw: str | None, end_raw: str | None, lookback_days: int) -> tuple[date, date]:
    today_et = datetime.now(_ET).date()
    end = date.fromisoformat(end_raw) if end_raw else today_et - timedelta(days=1)
    start = date.fromisoformat(start_raw) if start_raw else end - timedelta(days=lookback_days - 1)
    if end < start:
        raise ValueError("end date must be on or after start date")
    return start, end


def _trade_rows(result) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in result.days:
        if day.result is None:
            continue
        for trade in day.result.trades:
            rows.append(
                {
                    "session_date": day.session_date.isoformat(),
                    "instrument_id": trade.instrument_id,
                    "discovery_rank": trade.discovery_rank,
                    "quality_score": trade.quality_score,
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "entry_price": str(trade.entry_price),
                    "exit_price": str(trade.exit_price),
                    "stop_price": str(trade.stop_price),
                    "target_price": str(trade.target_price),
                    "quantity": str(trade.entry_fill_quantity),
                    "exit_reason": trade.exit_reason,
                    "r_multiple": str(trade.r_multiple),
                    "mfe_r": str(trade.mfe_r),
                    "mae_r": str(trade.mae_r),
                    "hold_minutes": str(trade.hold_minutes),
                    "entry_slippage_bps": str(trade.entry_slippage_bps),
                    "exit_slippage_bps": str(trade.exit_slippage_bps),
                }
            )
    return rows


def _rejection_counts(result) -> Counter[str]:
    counts: Counter[str] = Counter()
    for day in result.days:
        if day.result is None:
            continue
        for decision in day.result.candidate_decisions:
            reason = decision.rejection_reason or ("TRIGGERED" if decision.triggered else decision.state.upper())
            counts[str(reason)] += 1
    return counts


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "session_date",
        "instrument_id",
        "discovery_rank",
        "quality_score",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "stop_price",
        "target_price",
        "quantity",
        "exit_reason",
        "r_multiple",
        "mfe_r",
        "mae_r",
        "hold_minutes",
        "entry_slippage_bps",
        "exit_slippage_bps",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, result, strategy: TradingStrategyConfigDocument) -> None:
    pnl = "N/A" if result.pnl is None else str(result.pnl)
    return_pct = "N/A" if result.return_pct is None else f"{result.return_pct}%"
    expectancy = "N/A" if result.expectancy_r is None else f"{result.expectancy_r}R"
    rejection_counts = _rejection_counts(result)
    lines = [
        "# Omnix gap-pullback backtest",
        "",
        f"- Strategy: `{result.strategy_kind}` `{result.strategy_version}`",
        f"- Profile: strict v1.1 ({strategy.config.structure_interval} structure / {strategy.config.execution_interval} execution)",
        f"- Premarket dollar-volume gate: ${strategy.config.minimum_premarket_dollar_volume}",
        f"- Period: {result.start_date} through {result.end_date}",
        f"- Universe mode: `{result.universe_mode}`",
        f"- Result quality: **{result.result_quality}**",
        f"- Sessions: {result.covered_sessions}/{result.requested_trading_sessions} covered ({result.exact_sessions} exact, {result.reconstructed_sessions} reconstructed)",
        f"- Candidates: {result.candidate_count}",
        f"- Triggers / trades: {result.trigger_count} / {result.trade_count}",
        f"- Wins / losses: {result.win_count} / {result.loss_count}",
        f"- P&L: {pnl}",
        f"- Return: {return_pct}",
        f"- Expectancy: {expectancy}",
        "",
        "## Candidate outcomes",
        "",
    ]
    if rejection_counts:
        lines.extend(
            f"- `{reason}`: {count}"
            for reason, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))
        )
    else:
        lines.append("- No candidate decisions were recorded.")
    lines.extend(
        [
            "",
            "## Fidelity",
            "",
            "Reconstructed sessions use current active listings and Alpaca IEX partial-market historical evidence. Historical catalyst, dilution/supply, float, and true historical spread evidence are not available through reconstruction; the engine reports the resulting fidelity adjustments per day.",
            "",
            "## Strict v1.1 entry gates",
            "",
            f"Gap >=20%; price $0.50-$20; premarket dollar volume >=${strategy.config.minimum_premarket_dollar_volume}; TOD RVOL >=5x; preferred float 2M-30M; spread <=150 bps; catalyst evidence required; configured dilution vetoes; opening impulse >=8%; 15-55% pullback; sell-volume ratio <=0.70; higher low; VWAP reclaim; B1 break; breakout volume >=1.25x; one-bar breakout hold; quality >=7; entries 09:35-11:30 ET.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the strict Omnix gap-pullback strategy backtest.")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--assumed-spread-bps", default="40")
    parser.add_argument("--max-hold-minutes", type=int, default=90)
    parser.add_argument("--reconstruction-max-age-days", type=int, default=30)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument(
        "--minimum-premarket-dollar-volume",
        default=str(_STRICT_PREMARKET_DOLLAR_VOLUME),
        help="Backtest-only override for the strict v1.1 premarket dollar-volume gate.",
    )
    parser.add_argument(
        "--universe-mode",
        choices=("reconstructed_only", "captured_only", "captured_or_reconstructed"),
        default="reconstructed_only",
    )
    parser.add_argument("--output-dir", default="artifacts/trading-backtest")
    parser.add_argument("--require-covered-session", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start, end = _dates(args.start_date or None, args.end_date or None, args.lookback_days)
    minimum_premarket_dollar_volume = _decimal(args.minimum_premarket_dollar_volume)
    if minimum_premarket_dollar_volume < 0:
        raise ValueError("minimum premarket dollar volume cannot be negative")
    strategy = strict_v11_strategy(
        minimum_premarket_dollar_volume=minimum_premarket_dollar_volume,
    )
    request = StrategyRangeBacktestRequest(
        start_date=start,
        end_date=end,
        initial_cash=_decimal(args.initial_cash),
        assumed_spread_bps=_decimal(args.assumed_spread_bps),
        max_hold_minutes=args.max_hold_minutes,
        universe_scan_time_et=time(9, 20),
        universe_mode=args.universe_mode,
        reconstruction_max_age_days=args.reconstruction_max_age_days,
        max_sessions=args.max_sessions,
    )

    # GitHub-hosted runners do not have the user's local PostgreSQL universe archive.
    # Reconstructed mode is therefore the useful Actions default; captured modes can
    # be used later when a portable point-in-time dataset artifact is supplied.
    result = run_strategy_range_backtest(strategy, (), request)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    rows = _trade_rows(result)
    _write_csv(output_dir / "trades.csv", rows)
    _write_markdown(output_dir / "summary.md", result, strategy)
    (output_dir / "strategy-config.json").write_text(
        strategy.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (output_dir / "candidate-outcomes.json").write_text(
        json.dumps(dict(_rejection_counts(result)), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print((output_dir / "summary.md").read_text(encoding="utf-8"))
    if args.require_covered_session and result.covered_sessions == 0:
        print("No sessions were covered; refusing to treat this as a successful backtest.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
