from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from scripts.trade.run_interday_winner_shadow_replay import (
    DecisionDependencyView,
    _gap_pullback_outcome_final_before_gap,
    _portfolio_dependency_cutoff,
    _summary_rows,
)


UTC = timezone.utc
SESSION = date(2026, 9, 16)
OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=UTC)


def _view(*, evaluable: bool, blocked_minute: int | None) -> DecisionDependencyView:
    blocked = (
        OPEN + timedelta(minutes=blocked_minute)
        if blocked_minute is not None
        else None
    )
    return DecisionDependencyView(
        bars=(),
        decision_evaluable=evaluable,
        reset_required=False,
        blocked_after=blocked,
        status="full_session_complete" if evaluable else "unresolved_dependency",
        reason_codes=() if evaluable else ("SESSION_DEPENDS_ON_UNRESOLVED_GAP",),
    )


def test_portfolio_uses_earliest_unresolved_boundary_across_symbols() -> None:
    views = {
        "equity:US:MEDS": _view(evaluable=False, blocked_minute=90),
        "equity:US:QCLS": _view(evaluable=True, blocked_minute=None),
        "equity:US:RETO": _view(evaluable=False, blocked_minute=150),
    }

    assert _portfolio_dependency_cutoff(views) == OPEN + timedelta(minutes=90)


def test_portfolio_boundary_fails_closed_when_dependency_has_no_provable_cutoff() -> None:
    views = {
        "equity:US:MEDS": _view(evaluable=False, blocked_minute=None),
        "equity:US:QCLS": _view(evaluable=True, blocked_minute=None),
    }

    assert _portfolio_dependency_cutoff(views) is None


def test_gap_pullback_risk_rejection_before_gap_is_terminal_for_this_replay_policy() -> None:
    cutoff = OPEN + timedelta(minutes=120)
    config = SimpleNamespace(last_entry_et=time(11, 30))
    decision = SimpleNamespace(
        state="risk_rejected",
        rejection_reason="MAX_POSITIONS",
    )

    final, reason = _gap_pullback_outcome_final_before_gap(
        None, decision, cutoff, config=config
    )

    assert final is True
    assert reason == "GAP_PULLBACK_TERMINAL_DECISION_BEFORE_GAP"


def test_gap_pullback_truncated_eod_exit_is_still_not_terminal() -> None:
    cutoff = OPEN + timedelta(minutes=120)
    config = SimpleNamespace(last_entry_et=time(11, 30))
    trade = SimpleNamespace(exit_time=cutoff, exit_reason="eod")

    final, reason = _gap_pullback_outcome_final_before_gap(
        trade, None, cutoff, config=config
    )

    assert final is False
    assert reason == "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"


def test_arm_summary_requires_entire_cohort_session_to_be_evaluable() -> None:
    observations = [
        {
            "arm": "deterministic-v2",
            "session_date": SESSION,
            "status": "completed",
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "normalized_pnl": Decimal("100"),
        },
        {
            "arm": "deterministic-v2",
            "session_date": SESSION,
            "status": "data_unavailable",
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "normalized_pnl": Decimal("0"),
        },
    ]

    arm_summary, _daily = _summary_rows(observations, [SESSION])
    deterministic = next(
        row for row in arm_summary if row["arm"] == "deterministic-v2"
    )

    assert deterministic["completed_trades"] == 1
    assert deterministic["evaluable_sessions"] == 0
