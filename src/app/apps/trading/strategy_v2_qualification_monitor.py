from __future__ import annotations
from app.config.env import env_str as _env_str

import asyncio
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .market_evidence import MARKET_EVIDENCE_POLICY_VERSION
from .paper import PaperExecutionPolicy
from .providers.errors import ProviderContractError, ProviderDataUnavailableError
from .strategy_backtest import GapPullbackBacktestResult, freeze_backtest_session, run_gap_pullback_backtest
from .strategy_evaluability import assess_session_evaluability
from .strategy_historical_bars import alpaca_historical_session_bars
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import resolve_v2_evidence_archive_for_session
from .strategy_v2_qualification import (
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_EVENT_TYPES,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
    v2_profile_fingerprint,
    v2_qualification_profile_fingerprint,
)
from .trade_logging import trade_log
from .us_equity_calendar import regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_STATE_KEY = "_omnix_trading_strategy_v2_qualification_monitor"
_REPLAY_SPREAD_BPS = Decimal("150")
_REPLAY_INITIAL_CASH = Decimal("100000")
_REPLAY_LOOKBACK_DAYS = 7
_REPLAY_CLOSE_GRACE_MINUTES = 10
BarLoader = Callable[..., dict[str, list[Any]]]


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def strategy_v2_qualification_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_V2_QUALIFICATION_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_V2_QUALIFICATION", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_V2_QUALIFICATION_INTERVAL_SECONDS", "300"))
    except ValueError:
        value = 300.0
    return max(60.0, value)


def _eligible_strategy(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.enabled
        and config.mode in {"shadow", "auto_paper"}
        and config.config.strategy_version == "2.0.0"
        and v2_qualification_profile_fingerprint(config.config) is not None
    )


def _trading_session(session_date: date) -> bool:
    return session_date >= V2_PROSPECTIVE_START and session_date.weekday() < 5 and session_date not in regular_holidays(session_date.year)


def _session_finalized(session_date: date, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("v2 qualification clock must be timezone-aware")
    now_et = now.astimezone(_ET)
    close = regular_close_time(session_date)
    finalized = datetime.combine(session_date, close, tzinfo=_ET) + timedelta(minutes=_REPLAY_CLOSE_GRACE_MINUTES)
    return now_et >= finalized


def _event_id(*parts: object) -> tuple[str, str]:
    raw = "|".join(str(part) for part in parts)
    idem = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return idem[:32], idem


def _qualification_events(
    repository: TradingStrategyRepository,
    strategy_id: str,
    *,
    start_time: datetime,
    end_time: datetime,
) -> list[StrategyEvent]:
    if hasattr(repository, "events_by_types_between"):
        return repository.events_by_types_between(
            strategy_id,
            event_types=V2_QUALIFICATION_EVENT_TYPES,
            start_time=start_time,
            end_time=end_time,
            limit=20_000,
        )
    return [
        event
        for event in repository.recent_events(strategy_id, 20_000)
        if event.event_type in V2_QUALIFICATION_EVENT_TYPES
        and start_time <= event.observed_at.astimezone(timezone.utc) < end_time
    ]


def _already_replayed(events: list[StrategyEvent], session_date: date, profile_fingerprint: str) -> bool:
    """Any terminal v2 replay assessment checkpoints the session exactly once."""

    session = session_date.isoformat()
    return any(
        event.event_type == "v2_shadow_replay_session"
        and event.payload.get("qualification_version") == V2_QUALIFICATION_VERSION
        and event.payload.get("replay_version") == V2_REPLAY_VERSION
        and event.payload.get("profile_fingerprint") == profile_fingerprint
        and event.payload.get("market_evidence_policy_version") == MARKET_EVIDENCE_POLICY_VERSION
        and event.payload.get("session_date") == session
        and event.payload.get("status") in {
            "completed",
            "completed_no_trigger",
            "zero_candidate_scan",
            "partial_data",
            "not_evaluable_data",
            "provider_unavailable",
        }
        for event in events
    )


def _persist_session_assessment(
    *,
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    session_date: date,
    observed_at: datetime,
    universe,
    profile_fingerprint: str,
    status: str,
    qualification_eligible: bool,
    source_member_count: int,
    accounted_source_member_count: int,
    materialized_candidate_count: int,
    evaluable_candidate_count: int,
    unevaluable_candidate_count: int,
    source_failure_count: int,
    reason_codes: tuple[str, ...],
    dataset_fingerprint: str | None = None,
    trigger_count: int = 0,
    trade_count: int = 0,
    execution_policy_version: str | None = None,
) -> None:
    event_id, idem = _event_id(
        V2_REPLAY_VERSION,
        config.strategy_id,
        session_date.isoformat(),
        profile_fingerprint,
        universe.source_fingerprint,
        status,
        "session",
    )
    repository.append_event(
        StrategyEvent(
            strategy_id=config.strategy_id,
            event_id=event_id,
            run_id=None,
            instrument_id=f"strategy:{config.strategy_id}",
            event_type="v2_shadow_replay_session",
            state="replayed" if qualification_eligible else "not_evaluable",
            reason_code=(
                "V2_SHADOW_REPLAY_COMPLETED"
                if qualification_eligible
                else "V2_SHADOW_REPLAY_NOT_QUALIFICATION_ELIGIBLE"
            ),
            observed_at=observed_at,
            idempotency_key=idem,
            payload={
                "qualification_version": V2_QUALIFICATION_VERSION,
                "replay_version": V2_REPLAY_VERSION,
                "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                "session_date": session_date.isoformat(),
                "status": status,
                "qualification_eligible": qualification_eligible,
                "universe_id": universe.universe_id,
                "universe_source": "auto_archive_shadow",
                "universe_fingerprint": universe.source_fingerprint,
                "profile_fingerprint": profile_fingerprint,
                "dataset_fingerprint": dataset_fingerprint,
                "source_member_count": source_member_count,
                "accounted_source_member_count": accounted_source_member_count,
                "candidate_count": materialized_candidate_count,
                "evaluable_candidate_count": evaluable_candidate_count,
                "unevaluable_candidate_count": unevaluable_candidate_count,
                "source_failure_count": source_failure_count,
                "reason_codes": list(reason_codes),
                "trigger_count": trigger_count,
                "trade_count": trade_count,
                "assumed_spread_bps": str(_REPLAY_SPREAD_BPS),
                "execution_policy_version": execution_policy_version,
                "execution_authority": False,
            },
        )
    )


def replay_v2_shadow_session(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    session_date: date,
    *,
    observed_at: datetime | None = None,
    bar_loader=alpaca_historical_session_bars,
) -> GapPullbackBacktestResult | None:
    """Replay one captured prospective V2 session without converting data gaps to zero trades."""

    if not _eligible_strategy(config) or not _trading_session(session_date):
        return None
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("v2 qualification replay observed_at must be timezone-aware")
    if not _session_finalized(session_date, observed):
        return None

    profile_fingerprint = v2_profile_fingerprint(config.config)
    events = _qualification_events(
        repository,
        config.strategy_id,
        start_time=datetime.combine(V2_PROSPECTIVE_START, time.min, tzinfo=timezone.utc),
        end_time=observed.astimezone(timezone.utc) + timedelta(seconds=1),
    )
    if _already_replayed(events, session_date, profile_fingerprint):
        return None

    universe = resolve_v2_evidence_archive_for_session(config, repository, session_date=session_date)
    if universe is None:
        return None

    observed_utc = observed.astimezone(timezone.utc)
    evaluability = assess_session_evaluability(universe, config.config)
    if not evaluability.qualification_eligible:
        _persist_session_assessment(
            config=config,
            repository=repository,
            session_date=session_date,
            observed_at=observed_utc,
            universe=universe,
            profile_fingerprint=profile_fingerprint,
            status=evaluability.status,
            qualification_eligible=False,
            source_member_count=evaluability.source_member_count,
            accounted_source_member_count=evaluability.accounted_source_member_count,
            materialized_candidate_count=evaluability.materialized_candidate_count,
            evaluable_candidate_count=evaluability.evaluable_candidate_count,
            unevaluable_candidate_count=evaluability.unevaluable_candidate_count,
            source_failure_count=evaluability.source_failure_count,
            reason_codes=evaluability.reason_codes,
        )
        trade_log(
            "auto_trading",
            "v2_shadow_replay_not_evaluable",
            strategy_id=config.strategy_id,
            session_date=session_date,
            universe_id=universe.universe_id,
            status=evaluability.status,
            qualification_eligible=False,
            reason_codes=evaluability.reason_codes,
            execution_authority=False,
        )
        return None

    bars_by_instrument = bar_loader(universe.candidates, session_date)
    dataset = freeze_backtest_session(
        session_date=session_date,
        universe=universe,
        bars_by_instrument=bars_by_instrument,
    )
    result = run_gap_pullback_backtest(
        dataset,
        config.config,
        PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
        assumed_spread_bps=_REPLAY_SPREAD_BPS,
        max_hold_minutes=config.config.v2_max_hold_minutes,
        max_concurrent_positions=config.risk.max_positions,
        risk_profile=config.risk,
        initial_cash=_REPLAY_INITIAL_CASH,
    )

    for index, trade in enumerate(result.trades):
        event_id, idem = _event_id(
            V2_REPLAY_VERSION,
            config.strategy_id,
            session_date.isoformat(),
            profile_fingerprint,
            trade.instrument_id,
            trade.entry_time.astimezone(timezone.utc).isoformat(),
            index,
        )
        repository.append_event(
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=event_id,
                run_id=None,
                instrument_id=trade.instrument_id,
                event_type="v2_shadow_replay_trade",
                state="replayed",
                reason_code="V2_SHADOW_REPLAY_TRADE",
                observed_at=observed_utc,
                idempotency_key=idem,
                payload={
                    "qualification_version": V2_QUALIFICATION_VERSION,
                    "replay_version": V2_REPLAY_VERSION,
                    "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                    "session_date": session_date.isoformat(),
                    "universe_id": universe.universe_id,
                    "universe_source": "auto_archive_shadow",
                    "universe_fingerprint": universe.source_fingerprint,
                    "profile_fingerprint": profile_fingerprint,
                    "dataset_fingerprint": result.dataset_fingerprint,
                    "assumed_spread_bps": str(_REPLAY_SPREAD_BPS),
                    "execution_policy_version": result.execution_policy_version,
                    "entry_time": trade.entry_time.astimezone(timezone.utc).isoformat(),
                    "exit_time": trade.exit_time.astimezone(timezone.utc).isoformat(),
                    "exit_reason": trade.exit_reason,
                    "r_result": str(trade.r_multiple),
                    "mfe_r": str(trade.mfe_r),
                    "mae_r": str(trade.mae_r),
                    "execution_authority": False,
                },
            )
        )

    status = "completed_no_trigger" if result.summary.trigger_count == 0 else "completed"
    _persist_session_assessment(
        config=config,
        repository=repository,
        session_date=session_date,
        observed_at=observed_utc,
        universe=universe,
        profile_fingerprint=profile_fingerprint,
        status=status,
        qualification_eligible=True,
        source_member_count=evaluability.source_member_count,
        accounted_source_member_count=evaluability.accounted_source_member_count,
        materialized_candidate_count=evaluability.materialized_candidate_count,
        evaluable_candidate_count=evaluability.evaluable_candidate_count,
        unevaluable_candidate_count=evaluability.unevaluable_candidate_count,
        source_failure_count=evaluability.source_failure_count,
        reason_codes=evaluability.reason_codes,
        dataset_fingerprint=result.dataset_fingerprint,
        trigger_count=result.summary.trigger_count,
        trade_count=result.summary.trade_count,
        execution_policy_version=result.execution_policy_version,
    )
    trade_log(
        "auto_trading",
        "v2_shadow_replay_completed",
        strategy_id=config.strategy_id,
        session_date=session_date,
        universe_id=universe.universe_id,
        status=status,
        qualification_eligible=True,
        profile_fingerprint=profile_fingerprint,
        dataset_fingerprint=result.dataset_fingerprint,
        trigger_count=result.summary.trigger_count,
        trade_count=result.summary.trade_count,
        assumed_spread_bps=_REPLAY_SPREAD_BPS,
        execution_authority=False,
    )
    return result


class TradingStrategyV2QualificationMonitor(ScheduledTradingMonitor):
    """Evidence-only prospective V2 replay monitor across SHADOW/AUTO PAPER; never creates orders."""

    error_event = "v2_shadow_replay_monitor_error"

    def __init__(self, *, interval_seconds: float | None = None) -> None:
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.replay_count = 0

    async def run_once(self) -> int:
        repository = default_strategy_repository()
        configs = await asyncio.to_thread(repository.list_configs, active_only=False)
        now = datetime.now(timezone.utc)
        replays = 0
        for config in configs:
            if not _eligible_strategy(config):
                continue
            for offset in range(_REPLAY_LOOKBACK_DAYS + 1):
                session_date = now.astimezone(_ET).date() - timedelta(days=offset)
                if not _trading_session(session_date) or not _session_finalized(session_date, now):
                    continue
                try:
                    result = await asyncio.to_thread(
                        replay_v2_shadow_session,
                        config,
                        repository,
                        session_date,
                        observed_at=now,
                    )
                    if result is not None:
                        replays += 1
                except (ProviderContractError, ProviderDataUnavailableError, OSError, ValueError) as exc:
                    self.last_error = f"{config.strategy_id}/{session_date}: {type(exc).__name__}: {exc}"
                    trade_log(
                        "auto_trading",
                        "v2_shadow_replay_error",
                        strategy_id=config.strategy_id,
                        session_date=session_date,
                        error_type=type(exc).__name__,
                        detail=str(exc),
                        execution_authority=False,
                    )
        self.replay_count += replays
        self.last_run_at = datetime.now(timezone.utc)
        return replays


def create_trading_strategy_v2_qualification_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingStrategyV2QualificationMonitor):
        return None
    monitor = TradingStrategyV2QualificationMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=strategy_v2_qualification_monitor_enabled)


__all__ = [
    "TradingStrategyV2QualificationMonitor",
    "create_trading_strategy_v2_qualification_monitor_task",
    "replay_v2_shadow_session",
    "strategy_v2_qualification_monitor_enabled",
]
