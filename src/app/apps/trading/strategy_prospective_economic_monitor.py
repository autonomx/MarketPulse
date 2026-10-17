"""Prospective economic-SHADOW recorder.

This monitor consumes the isolated deep-recovery SHADOW state/signal stream. It
mirrors all causal state transitions for unbiased later diagnostics, while only
frozen signal events enter the economic promotion sample. For each signal it
freezes the executable entry/structural risk geometry available at decision time
and resolves direct +R/-R first-passage outcomes from finalized 1-minute bars.
It has no paper repository and can never create or authorize an order.
"""

from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .models import MarketBar
from .service import TradingMarketDataService, default_market_data_service
from .strategy_deep_recovery import DEEP_RECOVERY_RULE_VERSION, DEEP_RECOVERY_SETUP_ID
from .strategy_prospective_economic import (
    PROSPECTIVE_ECONOMIC_EVENT_TYPES,
    PROSPECTIVE_ECONOMIC_HORIZON_MINUTES,
    PROSPECTIVE_ECONOMIC_START,
    PROSPECTIVE_ECONOMIC_VERSION,
    prospective_economic_profile_fingerprint,
)
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_v2_qualification import v2_profile_fingerprint
from .trade_logging import trade_log
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from typing import Any

logger = logging.getLogger(__name__)


_STATE_KEY = "_omnix_trading_strategy_prospective_economic_monitor"
_SOURCE_STATE_EVENT_TYPE = "deep_recovery_state"
_SOURCE_EVENT_TYPE = "deep_recovery_shadow"
_CANDIDATE_EVENT_TYPE = "prospective_economic_candidate"
_SIGNAL_EVENT_TYPE = "prospective_economic_signal"
_OUTCOME_EVENT_TYPE = "prospective_economic_outcome"


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def strategy_prospective_economic_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_PROSPECTIVE_ECONOMIC_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_PROSPECTIVE_ECONOMIC_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_PROSPECTIVE_ECONOMIC_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(10.0, value)


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _datetime(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback.astimezone(timezone.utc)
    else:
        return fallback.astimezone(timezone.utc)
    if parsed.tzinfo is None:
        return fallback.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def _eligible(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.enabled
        and config.mode == "shadow"
        and config.strategy_version == "2.0.0"
        and config.config.strategy_version == "2.0.0"
        and config.archived_at is None
    )


def _event_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(
        PROSPECTIVE_ECONOMIC_START.year,
        PROSPECTIVE_ECONOMIC_START.month,
        PROSPECTIVE_ECONOMIC_START.day,
        tzinfo=timezone.utc,
    )
    return start, now.astimezone(timezone.utc) + timedelta(seconds=1)


def _first_passage(
    bars: list[MarketBar],
    *,
    entry: Decimal,
    risk: Decimal,
    target_r: Decimal,
) -> tuple[str, datetime | None]:
    target = entry + risk * target_r
    stop = entry - risk
    for bar in bars:
        # Pessimistic and consistent with the paper/backtest contract: if the
        # stop and target are both inside one 1m range, the stop wins the tie.
        if bar.low <= stop:
            return "stop", bar.end_time
        if bar.high >= target:
            return "target", bar.end_time
    return "neither", None


def _mark_r(
    bars: list[MarketBar],
    *,
    minutes: int,
    entry: Decimal,
    risk: Decimal,
) -> Decimal | None:
    # `bars` contains only full finalized one-minute bars that begin at or after
    # the signal timestamp. Taking the first N avoids both pre-entry range
    # contamination and fractional-bar lookahead when a quote arrives mid-minute.
    eligible = bars[:minutes]
    if len(eligible) < minutes:
        return None
    return (eligible[-1].close - entry) / risk


def _clip_r(value: Decimal) -> Decimal:
    return max(Decimal("-1"), min(Decimal("1"), value))


class TradingStrategyProspectiveEconomicMonitor(ScheduledTradingMonitor):
    error_event = "prospective_economic_shadow_monitor_error"

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.candidate_capture_count = 0
        self.signal_capture_count = 0
        self.outcome_capture_count = 0
        self.incomplete_outcome_count = 0

    async def _events(
        self,
        repository: TradingStrategyRepository,
        strategy_id: str,
        *,
        now: datetime,
    ) -> list[StrategyEvent]:
        start, end = _event_window(now)
        event_types = (
            _SOURCE_STATE_EVENT_TYPE,
            _SOURCE_EVENT_TYPE,
            _CANDIDATE_EVENT_TYPE,
            *PROSPECTIVE_ECONOMIC_EVENT_TYPES,
        )
        if hasattr(repository, "events_by_types_between"):
            return await asyncio.to_thread(
                repository.events_by_types_between,
                strategy_id,
                event_types=event_types,
                start_time=start,
                end_time=end,
                limit=50_000,
            )
        recent = await asyncio.to_thread(repository.recent_events, strategy_id, 50_000)
        return [
            event for event in recent
            if event.event_type in event_types and start <= event.observed_at.astimezone(timezone.utc) < end
        ]

    async def _append(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        instrument_id: str,
        event_type: str,
        state: str,
        reason_code: str,
        observed_at: datetime,
        payload: dict[str, Any],
        identity: tuple[object, ...],
    ) -> bool:
        idem = _key(config.strategy_id, PROSPECTIVE_ECONOMIC_VERSION, event_type, instrument_id, *identity)
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=idem[:32],
                run_id=None,
                instrument_id=instrument_id,
                event_type=event_type,
                state=state,
                reason_code=reason_code,
                observed_at=observed_at,
                idempotency_key=idem,
                payload=payload,
            ),
        )

    async def _capture_candidates(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        events: list[StrategyEvent],
    ) -> int:
        """Mirror every causal source state transition for unbiased diagnostics.

        These events are intentionally diagnostic-only: they never enter the
        promotion metrics/evidence fingerprint, which remain signal/outcome based.
        """
        profile = prospective_economic_profile_fingerprint(config)
        current_v2 = v2_profile_fingerprint(config.config)
        existing_sources = {
            str(event.payload.get("source_event_id"))
            for event in events
            if event.event_type == _CANDIDATE_EVENT_TYPE
            and event.payload.get("profile_fingerprint") == profile
        }
        captured = 0
        for source in events:
            if source.event_type != _SOURCE_STATE_EVENT_TYPE or source.event_id in existing_sources:
                continue
            if source.payload.get("setup_id") != DEEP_RECOVERY_SETUP_ID:
                continue
            if source.payload.get("rule_version") != DEEP_RECOVERY_RULE_VERSION:
                continue
            if source.payload.get("profile_fingerprint") != current_v2:
                continue
            observed_at = source.observed_at.astimezone(timezone.utc)
            evaluation = source.payload.get("evaluation")
            evaluation_dict = evaluation if isinstance(evaluation, dict) else {}
            payload: dict[str, Any] = {
                "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                "profile_fingerprint": profile,
                "source_event_id": source.event_id,
                "source_setup_id": DEEP_RECOVERY_SETUP_ID,
                "source_rule_version": DEEP_RECOVERY_RULE_VERSION,
                "session_date": observed_at.astimezone(_ET).date().isoformat(),
                "source_state": source.state,
                "source_reason_code": source.reason_code,
                "source_evaluation": evaluation_dict,
                "universe_id": source.payload.get("universe_id"),
                "universe_source": source.payload.get("universe_source"),
                "finalized_bar_count": source.payload.get("finalized_bar_count"),
                "diagnostic_only": True,
                "promotion_metric_eligible": False,
                "execution_authority": False,
            }
            persisted = await self._append(
                repository,
                config,
                instrument_id=source.instrument_id,
                event_type=_CANDIDATE_EVENT_TYPE,
                state=source.state,
                reason_code="PROSPECTIVE_ECONOMIC_CANDIDATE_STATE",
                observed_at=observed_at,
                payload=payload,
                identity=(source.event_id,),
            )
            captured += int(persisted)
            if persisted:
                self.candidate_capture_count += 1
        return captured

    async def _capture_signals(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        events: list[StrategyEvent],
    ) -> int:
        profile = prospective_economic_profile_fingerprint(config)
        current_v2 = v2_profile_fingerprint(config.config)
        existing_sources = {
            str(event.payload.get("source_event_id"))
            for event in events
            if event.event_type == _SIGNAL_EVENT_TYPE
            and event.payload.get("profile_fingerprint") == profile
        }
        captured = 0
        for source in events:
            if source.event_type != _SOURCE_EVENT_TYPE or source.event_id in existing_sources:
                continue
            if source.payload.get("setup_id") != DEEP_RECOVERY_SETUP_ID:
                continue
            if source.payload.get("rule_version") != DEEP_RECOVERY_RULE_VERSION:
                continue
            if source.payload.get("profile_fingerprint") != current_v2:
                continue
            execution = source.payload.get("execution")
            execution_dict = execution if isinstance(execution, dict) else {}
            evaluation = source.payload.get("evaluation")
            evaluation_dict = evaluation if isinstance(evaluation, dict) else {}
            entry = _decimal(execution_dict.get("ask")) or _decimal(execution_dict.get("last"))
            stop = _decimal(evaluation_dict.get("research_stop_price"))
            risk = entry - stop if entry is not None and stop is not None else None
            execution_eligible = bool(execution_dict.get("execution_eligible"))
            entry_time = _datetime(execution_dict.get("source_time"), source.observed_at)
            matched = bool(execution_eligible and entry is not None and risk is not None and risk > 0)
            payload: dict[str, Any] = {
                "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                "profile_fingerprint": profile,
                "source_event_id": source.event_id,
                "source_setup_id": DEEP_RECOVERY_SETUP_ID,
                "source_rule_version": DEEP_RECOVERY_RULE_VERSION,
                "session_date": entry_time.astimezone(_ET).date().isoformat(),
                "entry_time": entry_time.isoformat(),
                "execution_eligible": execution_eligible,
                "matched_signal": matched,
                "entry_price": str(entry) if entry is not None else None,
                "stop_price": str(stop) if stop is not None else None,
                "risk_per_share": str(risk) if risk is not None and risk > 0 else None,
                "target_0_5r": str(entry + risk * Decimal("0.5")) if matched and entry is not None and risk is not None else None,
                "target_0_75r": str(entry + risk * Decimal("0.75")) if matched and entry is not None and risk is not None else None,
                "target_1r": str(entry + risk) if matched and entry is not None and risk is not None else None,
                "binding_id": execution_dict.get("binding_id"),
                "execution": execution_dict,
                "source_evaluation": evaluation_dict,
                "prospective_signal_features": execution_dict.get("prospective_signal_features"),
                "execution_authority": False,
            }
            persisted = await self._append(
                repository,
                config,
                instrument_id=source.instrument_id,
                event_type=_SIGNAL_EVENT_TYPE,
                state="matched" if matched else "execution_ineligible",
                reason_code="PROSPECTIVE_ECONOMIC_SIGNAL_MATCHED" if matched else "PROSPECTIVE_ECONOMIC_SIGNAL_UNMATCHED",
                observed_at=entry_time,
                payload=payload,
                identity=(source.event_id,),
            )
            captured += int(persisted)
            if persisted:
                self.signal_capture_count += 1
        return captured

    async def _capture_outcomes(
        self,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        config: TradingStrategyConfigDocument,
        events: list[StrategyEvent],
        *,
        now: datetime,
    ) -> int:
        profile = prospective_economic_profile_fingerprint(config)
        completed = {
            str(event.payload.get("signal_event_id"))
            for event in events
            if event.event_type == _OUTCOME_EVENT_TYPE
            and event.payload.get("profile_fingerprint") == profile
        }
        captured = 0
        for signal in events:
            if signal.event_type != _SIGNAL_EVENT_TYPE or signal.event_id in completed:
                continue
            if signal.payload.get("profile_fingerprint") != profile:
                continue
            entry_time = _datetime(signal.payload.get("entry_time"), signal.observed_at)
            horizon_end = entry_time + timedelta(minutes=PROSPECTIVE_ECONOMIC_HORIZON_MINUTES)
            if now < horizon_end:
                continue
            matched = signal.payload.get("matched_signal") is True
            entry = _decimal(signal.payload.get("entry_price"))
            risk = _decimal(signal.payload.get("risk_per_share"))
            if not matched or entry is None or risk is None or risk <= 0:
                payload = {
                    "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                    "profile_fingerprint": profile,
                    "signal_event_id": signal.event_id,
                    "session_date": entry_time.astimezone(_ET).date().isoformat(),
                    "entry_time": entry_time.isoformat(),
                    "horizon_end": horizon_end.isoformat(),
                    "data_complete": False,
                    "matched_signal": False,
                    "execution_authority": False,
                }
                persisted = await self._append(
                    repository,
                    config,
                    instrument_id=signal.instrument_id,
                    event_type=_OUTCOME_EVENT_TYPE,
                    state="unmatched",
                    reason_code="PROSPECTIVE_ECONOMIC_OUTCOME_UNMATCHED_SIGNAL",
                    observed_at=horizon_end,
                    payload=payload,
                    identity=(signal.event_id,),
                )
                captured += int(persisted)
                if persisted:
                    self.outcome_capture_count += 1
                continue

            binding_raw = signal.payload.get("binding_id")
            binding_id = str(binding_raw) if binding_raw else None
            try:
                recovery_method = getattr(market_service, "recovered_bars", None)
                if callable(recovery_method):
                    response = await asyncio.to_thread(
                        recovery_method,
                        signal.instrument_id,
                        "1m",
                        500,
                        binding_id,
                        session_date=entry_time.astimezone(_ET).date(),
                        as_of=horizon_end,
                        knowledge_mode="retroactive_research",
                    )
                    outcome_bars = list(response.bars)
                else:
                    legacy_response = await asyncio.to_thread(
                        market_service.bars,
                        signal.instrument_id,
                        "1m",
                        500,
                        binding_id,
                    )
                    outcome_bars = list(legacy_response.bars)
                full_bars = sorted(
                    [
                        bar for bar in outcome_bars
                        if bar.is_final and bar.start_time >= entry_time
                    ],
                    key=lambda bar: bar.end_time,
                )
                bars = full_bars[:PROSPECTIVE_ECONOMIC_HORIZON_MINUTES]
            except Exception as exc:
                self.last_error = f"{config.strategy_id}/{signal.instrument_id}/outcome: {type(exc).__name__}: {exc}"
                continue

            complete = len(bars) >= PROSPECTIVE_ECONOMIC_HORIZON_MINUTES
            if not complete:
                # Allow delayed IEX/bar propagation to catch up. After 30 minutes
                # of grace, persist an explicit incomplete outcome so the missing
                # observation cannot disappear from execution-match diagnostics.
                if now < horizon_end + timedelta(minutes=30):
                    self.incomplete_outcome_count += 1
                    continue
                payload = {
                    "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                    "profile_fingerprint": profile,
                    "signal_event_id": signal.event_id,
                    "session_date": entry_time.astimezone(_ET).date().isoformat(),
                    "entry_time": entry_time.isoformat(),
                    "horizon_end": horizon_end.isoformat(),
                    "data_complete": False,
                    "matched_signal": True,
                    "bar_count": len(bars),
                    "latest_bar_end": bars[-1].end_time.isoformat() if bars else None,
                    "execution_authority": False,
                }
                persisted = await self._append(
                    repository,
                    config,
                    instrument_id=signal.instrument_id,
                    event_type=_OUTCOME_EVENT_TYPE,
                    state="data_incomplete",
                    reason_code="PROSPECTIVE_ECONOMIC_OUTCOME_DATA_INCOMPLETE",
                    observed_at=horizon_end,
                    payload=payload,
                    identity=(signal.event_id,),
                )
                captured += int(persisted)
                if persisted:
                    self.outcome_capture_count += 1
                continue

            resolution_time = bars[-1].end_time
            passage_05, passage_05_at = _first_passage(bars, entry=entry, risk=risk, target_r=Decimal("0.5"))
            passage_075, passage_075_at = _first_passage(bars, entry=entry, risk=risk, target_r=Decimal("0.75"))
            passage_1, passage_1_at = _first_passage(bars, entry=entry, risk=risk, target_r=Decimal("1"))
            r15 = _mark_r(bars, minutes=15, entry=entry, risk=risk)
            r30 = _mark_r(bars, minutes=30, entry=entry, risk=risk)
            r60 = _mark_r(bars, minutes=60, entry=entry, risk=risk)
            one_r_win = passage_1 == "target"
            if passage_1 == "target":
                result_60 = Decimal("1")
            elif passage_1 == "stop":
                result_60 = Decimal("-1")
            else:
                result_60 = _clip_r(r60 or Decimal("0"))
            mfe_r = max((bar.high - entry) / risk for bar in bars)
            mae_r = min((bar.low - entry) / risk for bar in bars)
            payload = {
                "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                "profile_fingerprint": profile,
                "signal_event_id": signal.event_id,
                "session_date": entry_time.astimezone(_ET).date().isoformat(),
                "entry_time": entry_time.isoformat(),
                "horizon_end": horizon_end.isoformat(),
                "resolution_time": resolution_time.isoformat(),
                "horizon_definition": "60 full finalized 1m bars beginning at or after the signal timestamp",
                "data_complete": True,
                "matched_signal": True,
                "bar_count": len(bars),
                "first_passage_0_5r": passage_05,
                "first_passage_0_5r_at": passage_05_at.isoformat() if passage_05_at else None,
                "first_passage_0_75r": passage_075,
                "first_passage_0_75r_at": passage_075_at.isoformat() if passage_075_at else None,
                "first_passage_1r": passage_1,
                "first_passage_1r_at": passage_1_at.isoformat() if passage_1_at else None,
                "one_r_before_minus_one_r": one_r_win,
                "r_15m": str(r15) if r15 is not None else None,
                "r_30m": str(r30) if r30 is not None else None,
                "r_60m_mark": str(r60) if r60 is not None else None,
                "r_result_60m": str(result_60),
                "mfe_r": str(mfe_r),
                "mae_r": str(mae_r),
                "execution_authority": False,
            }
            persisted = await self._append(
                repository,
                config,
                instrument_id=signal.instrument_id,
                event_type=_OUTCOME_EVENT_TYPE,
                state="win" if one_r_win else "loss_or_timeout",
                reason_code="PROSPECTIVE_ECONOMIC_1R_FIRST" if one_r_win else "PROSPECTIVE_ECONOMIC_1R_NOT_FIRST",
                observed_at=resolution_time,
                payload=payload,
                identity=(signal.event_id,),
            )
            captured += int(persisted)
            if persisted:
                self.outcome_capture_count += 1
        return captured

    async def _run_config(
        self,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        config: TradingStrategyConfigDocument,
        *,
        now: datetime,
    ) -> int:
        if not _eligible(config):
            return 0
        events = await self._events(repository, config.strategy_id, now=now)
        captured = await self._capture_candidates(repository, config, events)
        captured += await self._capture_signals(repository, config, events)
        # Re-read so events appended above can become outcomes in the same cycle
        # if the monitor was offline for more than the 60-minute horizon.
        if captured:
            events = await self._events(repository, config.strategy_id, now=now)
        captured += await self._capture_outcomes(repository, market_service, config, events, now=now)
        return captured

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("prospective_economic_shadow_clock_must_be_timezone_aware")
        now = now.astimezone(timezone.utc)
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        captured = 0
        for config in configs:
            try:
                captured += await self._run_config(repository, market_service, config, now=now)
            except Exception as exc:
                self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "prospective_economic_shadow_monitor_error",
                    strategy_id=config.strategy_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
        self.last_run_at = now
        return captured

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": strategy_prospective_economic_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "candidate_capture_count": self.candidate_capture_count,
            "signal_capture_count": self.signal_capture_count,
            "outcome_capture_count": self.outcome_capture_count,
            "incomplete_outcome_count": self.incomplete_outcome_count,
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "execution_authority": False,
        }


def create_trading_strategy_prospective_economic_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingStrategyProspectiveEconomicMonitor):
        return None
    monitor = TradingStrategyProspectiveEconomicMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=strategy_prospective_economic_monitor_enabled)


__all__ = [
    "TradingStrategyProspectiveEconomicMonitor",
    "create_trading_strategy_prospective_economic_monitor_task",
    "strategy_prospective_economic_monitor_enabled",
]
