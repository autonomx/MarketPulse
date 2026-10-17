"""Interday SHADOW experiment: the AI arms of the substrategy group.

A/B (deterministic V2 and Stoch trend capture) are produced by the canonical
strategy monitor. This monitor adds C/D: a stateful every-minute AI policy and a
stateful event-driven AI policy over the identical frozen cohort. It never
creates paper orders or protections. The linked standalone ``stoch-rsi-5min``,
``stoch-rsi-5min-early-single`` and ``gap-pullback-v2-prospective-20260825``
configurations are separate children; their deterministic monitors remain the
owners of their own evidence.
"""

from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
import hashlib
import threading
import time as _clock
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .execution_observation_plane import (
    ExecutionObservationPlane,
    default_execution_observation_plane,
)
from .gapper_dataset import GapperCandidate
from .indicator_signals import multi_timeframe_indicator_context
from .market_evidence import (
    DEFAULT_MARKET_EVIDENCE_POLICY,
    ExecutionInputGap,
    MARKET_EVIDENCE_POLICY_VERSION,
    ProviderReadiness,
    classify_provider_exception,
)
from .models import MarketBar
from .service import TradingMarketDataService, default_market_data_service
from .strategies import evaluate_gap_pullback
from .strategy_ai_shadow import (
    AI_SHADOW_POLICY_VERSION,
    AIShadowDecision,
    AIShadowPolicy,
    AIShadowPolicyAnalyzer,
    AIShadowPositionState,
    apply_fill,
    desired_fill,
    event_trigger_reasons,
    feature_snapshot,
    normalize_action_for_position,
    policy_feature_snapshot,
    simulate_ai_shadow_fill,
)
from .strategy_intraday_learning import build_intraday_learning_snapshot
from .strategy_evaluability import (
    assess_bar_coverage,
    candidate_morning_evidence_eligible,
)
from .strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import resolve_v2_shadow_archive
from .strategy_timeframes import resample_final_bars
from .strategy_session_evidence import (
    _CurrentSessionMarketDataProxy,
    _FullSessionMarketServiceProxy,
)
from .trade_logging import trade_log
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import after_regular_close

logger = logging.getLogger(__name__)


_STATE_KEY = "_omnix_trading_ai_shadow_monitor"
_EVENT_TYPES = (
    "state",
    "shadow_execution",
    "v2_shadow_replay_trade",
    "stoch_trend_execution_summary",
    "ai_shadow_decision",
    "ai_shadow_entry_intent",
    "ai_shadow_exit_intent",
    "ai_shadow_fill",
    "ai_shadow_trade",
    "ai_shadow_batch",
    "ai_shadow_data_gap",
    "ai_shadow_input_gap",
    "ai_shadow_session_summary",
    "shadow_strategy_comparison",
)
_EXECUTION_FIELDS = (
    "instrument_id",
    "binding_id",
    "provider",
    "last",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "high",
    "low",
    "bar_volume",
    "bar_start_time",
    "source_time",
    "spread_bps",
    "execution_eligible",
    "freshness_mode",
    "rejection_reasons",
    "halted",
)
_GAP_HEARTBEAT = timedelta(minutes=15)
_GAP_LOCK = threading.RLock()
MAX_DATA_GAP_HEARTBEATS = 4096
DATA_GAP_HEARTBEAT_TTL_SECONDS = 24 * 60 * 60.0
_GAP_LAST_EMITTED: OrderedDict[
    tuple[str, str, str, str, str], tuple[datetime, float]
] = OrderedDict()


def clear_data_gap_heartbeat_cache() -> None:
    """Clear duplicate-gap suppression entries for tests and controlled resets."""

    with _GAP_LOCK:
        _GAP_LAST_EMITTED.clear()


def _allow_data_gap_heartbeat(
    key: tuple[str, str, str, str, str],
    current: datetime,
) -> bool:
    now = _clock.monotonic()
    with _GAP_LOCK:
        stale = [
            stale_key
            for stale_key, (_observed_at, touched_at) in _GAP_LAST_EMITTED.items()
            if now - touched_at > DATA_GAP_HEARTBEAT_TTL_SECONDS
        ]
        for stale_key in stale:
            _GAP_LAST_EMITTED.pop(stale_key, None)
        previous = _GAP_LAST_EMITTED.get(key)
        if previous is not None and timedelta(0) <= current - previous[0] < _GAP_HEARTBEAT:
            _GAP_LAST_EMITTED.move_to_end(key)
            return False
        _GAP_LAST_EMITTED[key] = (current, now)
        _GAP_LAST_EMITTED.move_to_end(key)
        while len(_GAP_LAST_EMITTED) > MAX_DATA_GAP_HEARTBEATS:
            _GAP_LAST_EMITTED.popitem(last=False)
        return True


def _events_with_data_gap_aliases(events: list[StrategyEvent]) -> list[StrategyEvent]:
    output = list(events)
    output.extend(
        event.model_copy(update={"event_type": "ai_shadow_data_gap"})
        for event in events
        if event.event_type == "ai_shadow_input_gap"
    )
    return output


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ai_shadow_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_AI_SHADOW_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_AI_SHADOW_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_AI_SHADOW_INTERVAL_SECONDS", "15"))
    except ValueError:
        value = 15.0
    return max(5.0, value)


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _eligible(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.strategy_id == MANAGED_FINVIZ_SHADOW_STRATEGY_ID
        and config.enabled
        and config.mode == "shadow"
        and config.active_universe_id is None
        and config.config.strategy_version == "2.0.0"
        and config.config.universe_discovery_source == "finviz"
        and config.config.universe_discovery_count == 5
    )


def _execution_payload(observation: Any) -> dict[str, Any]:
    payload = {field: getattr(observation, field, None) for field in _EXECUTION_FIELDS}
    spread = getattr(observation, "spread_bps", None)
    payload["spread_bps"] = spread
    bid = payload.get("bid")
    ask = payload.get("ask")
    last = payload.get("last")
    payload["observation_quality"] = (
        "book"
        if bid is not None and ask is not None
        else "price_only"
        if last is not None
        else "unavailable"
    )
    return payload


def _position_from_latest_fill(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> AIShadowPositionState:
    candidates = [
        event
        for event in events
        if event.event_type == "ai_shadow_fill"
        and event.instrument_id == instrument_id
        and event.payload.get("policy") == policy
        and isinstance(event.payload.get("position_after"), dict)
    ]
    if not candidates:
        return AIShadowPositionState(policy=policy, instrument_id=instrument_id)
    latest = max(candidates, key=lambda event: (event.observed_at, event.event_id))
    try:
        return AIShadowPositionState.model_validate(latest.payload["position_after"])
    except Exception:
        return AIShadowPositionState(policy=policy, instrument_id=instrument_id)


def _previous_decision(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> StrategyEvent | None:
    matches = [
        event
        for event in events
        if event.event_type == "ai_shadow_decision"
        and event.instrument_id == instrument_id
        and event.payload.get("policy") == policy
    ]
    return max(matches, key=lambda event: (event.observed_at, event.event_id)) if matches else None


def _payload_datetime(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = fallback
    if parsed.tzinfo is None:
        raise ValueError("ai shadow persisted execution clock must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _latest_entry_intent(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> StrategyEvent | None:
    matches = [
        event
        for event in events
        if event.event_type == "ai_shadow_entry_intent"
        and event.instrument_id == instrument_id
        and event.payload.get("policy") == policy
    ]
    return max(matches, key=lambda event: (event.observed_at, event.event_id)) if matches else None


def _pending_entry_intent(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> StrategyEvent | None:
    latest = _latest_entry_intent(
        events,
        policy=policy,
        instrument_id=instrument_id,
    )
    return latest if latest is not None and latest.state == "pending_execution" else None


def _latest_exit_intent(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> StrategyEvent | None:
    matches = [
        event
        for event in events
        if event.event_type == "ai_shadow_exit_intent"
        and event.instrument_id == instrument_id
        and event.payload.get("policy") == policy
    ]
    return max(matches, key=lambda event: (event.observed_at, event.event_id)) if matches else None


def _pending_exit_intent(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> StrategyEvent | None:
    latest = _latest_exit_intent(
        events,
        policy=policy,
        instrument_id=instrument_id,
    )
    return latest if latest is not None and latest.state == "pending_execution" else None


def _completed_trade_exists(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
) -> bool:
    for event in events:
        if event.instrument_id != instrument_id or event.payload.get("policy") != policy:
            continue
        if event.event_type == "ai_shadow_trade" and event.state == "closed":
            return True
        if (
            event.event_type == "ai_shadow_fill"
            and event.state == "filled"
            and isinstance(event.payload.get("closed_position"), dict)
        ):
            return True
    return False


def _active_position_count(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
) -> int:
    instrument_ids = {
        event.instrument_id
        for event in events
        if event.event_type == "ai_shadow_fill"
        and event.payload.get("policy") == policy
        and event.instrument_id != "__universe__"
    }
    return sum(
        1
        for instrument_id in instrument_ids
        if _position_from_latest_fill(
            events,
            policy=policy,
            instrument_id=instrument_id,
        ).is_long
    )


def _started_trade_count(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
) -> int:
    trade_ids = {
        str(event.payload.get("trade_id"))
        for event in events
        if event.event_type == "ai_shadow_fill"
        and event.state == "filled"
        and event.payload.get("policy") == policy
        and event.payload.get("side") == "buy"
        and event.payload.get("trade_id")
    }
    return len(trade_ids)


def _decision_exists(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
    observed_at: datetime,
) -> bool:
    target = observed_at.astimezone(timezone.utc)
    return any(
        event.event_type == "ai_shadow_decision"
        and event.instrument_id == instrument_id
        and event.payload.get("policy") == policy
        and event.observed_at.astimezone(timezone.utc) == target
        for event in events
    )


def _action_changes(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    instrument_id: str,
    start: datetime,
    end: datetime,
) -> tuple[int, int, int]:
    decisions = sorted(
        [
            event
            for event in events
            if event.event_type == "ai_shadow_decision"
            and event.instrument_id == instrument_id
            and event.payload.get("policy") == policy
            and start <= event.observed_at <= end
        ],
        key=lambda event: event.observed_at,
    )
    actions = [str(event.payload.get("effective_action") or event.payload.get("decision", {}).get("action") or "") for event in decisions]
    changes = sum(1 for left, right in zip(actions, actions[1:]) if left and right and left != right)
    allocated_tokens = sum(int(event.payload.get("allocated_tokens") or 0) for event in decisions)
    return len(decisions), changes, allocated_tokens


def _policy_action_stats(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
) -> tuple[int, int, Decimal | None]:
    decisions = sorted(
        [
            event
            for event in events
            if event.event_type == "ai_shadow_decision"
            and event.payload.get("policy") == policy
        ],
        key=lambda event: (event.instrument_id, event.observed_at, event.event_id),
    )
    if not decisions:
        return 0, 0, None

    previous_by_instrument: dict[str, str] = {}
    transitions = 0
    changes = 0
    for event in decisions:
        action = str(
            event.payload.get("effective_action")
            or (
                event.payload.get("decision", {}).get("action")
                if isinstance(event.payload.get("decision"), dict)
                else ""
            )
            or ""
        )
        previous = previous_by_instrument.get(event.instrument_id)
        if previous is not None and action:
            transitions += 1
            if previous != action:
                changes += 1
        if action:
            previous_by_instrument[event.instrument_id] = action

    stability = (
        Decimal("1") - Decimal(changes) / Decimal(transitions)
        if transitions > 0
        else Decimal("1")
    )
    return len(decisions), changes, stability


def _max_drawdown(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    equity = Decimal("0")
    peak = Decimal("0")
    worst = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _batch_request_members(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    members: list[dict[str, str]] = []
    for row in rows:
        candidate = row["candidate"]
        observed_at = row["observed_at"]
        assert isinstance(observed_at, datetime)
        members.append(
            {
                "instrument_id": str(candidate.instrument_id),
                "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            }
        )
    members.sort(key=lambda item: (item["instrument_id"], item["observed_at"]))
    return members


def _batch_request_signature(members: list[dict[str, str]]) -> str:
    return _key(
        *(
            f"{member['instrument_id']}@{member['observed_at']}"
            for member in members
        )
    )


def _batch_checkpoint_exists(
    events: list[StrategyEvent],
    *,
    policy: AIShadowPolicy,
    observed_at: datetime,
    request_signature: str,
) -> bool:
    target = observed_at.astimezone(timezone.utc)
    return any(
        event.event_type == "ai_shadow_batch"
        and event.state in {"complete", "error"}
        and event.observed_at.astimezone(timezone.utc) == target
        and isinstance(event.payload, dict)
        and event.payload.get("policy") == policy
        and event.payload.get("request_signature") == request_signature
        for event in events
    )


def _mfe_mae(
    bars: list[MarketBar],
    *,
    entry_time: datetime,
    exit_time: datetime,
    entry_price: Decimal,
) -> tuple[Decimal | None, Decimal | None]:
    window = [
        bar
        for bar in bars
        if bar.is_final
        and bar.session == "regular"
        and bar.end_time >= entry_time
        and bar.start_time <= exit_time
    ]
    if not window or entry_price <= 0:
        return None, None
    mfe = max((bar.high / entry_price - Decimal("1")) * Decimal("100") for bar in window)
    mae = min((bar.low / entry_price - Decimal("1")) * Decimal("100") for bar in window)
    return mfe, mae


class TradingAIShadowMonitor(ScheduledTradingMonitor):
    error_event = "ai_shadow_monitor_error"

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        analyzer_factory: Callable[[], AIShadowPolicyAnalyzer] = AIShadowPolicyAnalyzer,
        execution_plane: ExecutionObservationPlane | None = None,
        now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.analyzer_factory = analyzer_factory
        self.execution_plane = execution_plane or default_execution_observation_plane()
        self.now_factory = now_factory
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.minute_llm_call_count = 0
        self.event_llm_call_count = 0
        self.decision_count = 0
        self.fill_count = 0
        self.trade_count = 0
        self.data_gap_count = 0
        self.total_token_count = 0

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
        if event_type in {"ai_shadow_input_gap", "ai_shadow_data_gap"}:
            policy = str(payload.get("policy") or "")
            key = (config.strategy_id, instrument_id, event_type, reason_code, policy)
            current = observed_at.astimezone(timezone.utc)
            if not _allow_data_gap_heartbeat(key, current):
                return False
            payload = dict(payload)
            payload["duplicate_gap_heartbeat_seconds"] = int(
                _GAP_HEARTBEAT.total_seconds()
            )
            payload["gap_sampling"] = "state_or_periodic_heartbeat"
        idem = _key(
            config.strategy_id,
            AI_SHADOW_POLICY_VERSION,
            instrument_id,
            event_type,
            *identity,
        )
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=idem[:32],
                run_id=f"ai-shadow-{observed_at.astimezone(timezone.utc).strftime('%Y%m%d')}",
                instrument_id=instrument_id,
                event_type=event_type,
                state=state,
                reason_code=reason_code,
                observed_at=observed_at,
                idempotency_key=idem,
                payload=payload,
            ),
        )

    async def _session_events(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        session_date,
        now: datetime,
    ) -> list[StrategyEvent]:
        start_et = datetime(
            session_date.year,
            session_date.month,
            session_date.day,
            tzinfo=_ET,
        )
        end = min(now + timedelta(seconds=1), start_et + timedelta(days=1))
        if hasattr(repository, "events_by_types_between"):
            return await asyncio.to_thread(
                repository.events_by_types_between,
                config.strategy_id,
                event_types=_EVENT_TYPES,
                start_time=start_et.astimezone(timezone.utc),
                end_time=end.astimezone(timezone.utc),
                limit=20_000,
            )
        recent = await asyncio.to_thread(repository.recent_events, config.strategy_id, 20_000)
        return [
            event
            for event in recent
            if event.event_type in _EVENT_TYPES
            and start_et.astimezone(timezone.utc)
            <= event.observed_at.astimezone(timezone.utc)
            < end.astimezone(timezone.utc)
        ]

    async def _apply_decision(
        self,
        *,
        policy: AIShadowPolicy,
        decision: AIShadowDecision,
        row: dict[str, Any],
        candidate,
        bars: list[MarketBar],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        events: list[StrategyEvent],
        result,
        batch_result,
        trigger_reasons: tuple[str, ...],
        record_decision: bool = True,
        execution_override: dict[str, Any] | None = None,
        decision_completed_at: datetime | None = None,
        reference_price_override: Decimal | None = None,
    ) -> None:
        observed_at = row["observed_at"]
        assert isinstance(observed_at, datetime)
        position = _position_from_latest_fill(
            events,
            policy=policy,
            instrument_id=candidate.instrument_id,
        )
        effective_action, normalization_reason = normalize_action_for_position(
            decision.action,
            position,
        )
        normalization_reasons: list[str] = []
        if normalization_reason is not None:
            normalization_reasons.append(normalization_reason)

        observed_et = observed_at.astimezone(_ET).time()
        force_flat = position.is_long and observed_et >= config.risk.force_flat_et
        if force_flat:
            effective_action = "exit"
            normalization_reasons.append("AI_SHADOW_FORCE_FLAT_OVERRIDE")
        elif effective_action in {"enter", "add"}:
            blocked_reason: str | None = None
            if config.risk.kill_switch:
                blocked_reason = "AI_SHADOW_KILL_SWITCH"
            elif observed_et > config.risk.last_entry_et:
                blocked_reason = "AI_SHADOW_ENTRY_WINDOW_CLOSED"
            elif (
                effective_action == "enter"
                and config.risk.one_trade_per_symbol_per_day
                and _completed_trade_exists(
                    events,
                    policy=policy,
                    instrument_id=candidate.instrument_id,
                )
            ):
                blocked_reason = "AI_SHADOW_ONE_TRADE_PER_SYMBOL"
            elif (
                effective_action == "enter"
                and _started_trade_count(events, policy=policy)
                >= config.risk.max_trades_per_day
            ):
                blocked_reason = "AI_SHADOW_MAX_TRADES_PER_DAY"
            elif (
                effective_action == "enter"
                and _active_position_count(events, policy=policy)
                >= config.risk.max_positions
            ):
                blocked_reason = "AI_SHADOW_MAX_POSITIONS"
            if blocked_reason is not None:
                normalization_reasons.append(blocked_reason)
                effective_action = "hold" if position.is_long else "skip"

        previous = _previous_decision(
            events,
            policy=policy,
            instrument_id=candidate.instrument_id,
        )
        previous_action = None
        if previous is not None:
            previous_action = previous.payload.get("effective_action")
            if previous_action is None and isinstance(previous.payload.get("decision"), dict):
                previous_action = previous.payload["decision"].get("action")

        allocation = (
            batch_result.total_tokens // max(1, len(batch_result.decisions))
            if batch_result is not None
            else 0
        )
        decision_payload: dict[str, Any] = {
            "policy_version": AI_SHADOW_POLICY_VERSION,
            "policy": policy,
            "universe_id": row["universe_id"],
            "decision": decision.model_dump(mode="json"),
            "effective_action": effective_action,
            "model_action": decision.action,
            "action_normalization_reasons": normalization_reasons,
            "force_flat_override": force_flat,
            "trigger_reasons": list(trigger_reasons),
            "feature_snapshot": row["feature_snapshot"],
            "previous_action": previous_action,
            "position_before": position.model_dump(mode="json"),
            "provider": batch_result.provider if batch_result is not None else "system",
            "model": batch_result.model if batch_result is not None else None,
            "allocated_tokens": allocation,
            "research_only": True,
            "execution_authority": False,
        }
        if record_decision:
            persisted = await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_decision",
                state=effective_action,
                reason_code=(
                    "AI_SHADOW_FORCE_FLAT"
                    if force_flat
                    else "AI_SHADOW_MINUTE_DECISION"
                    if policy == "minute"
                    else "AI_SHADOW_EVENT_DECISION"
                ),
                observed_at=observed_at,
                payload=decision_payload,
                identity=(policy, observed_at.astimezone(timezone.utc).isoformat()),
            )
            if persisted:
                self.decision_count += 1
                events.append(
                    StrategyEvent(
                        strategy_id=config.strategy_id,
                        event_id=_key(policy, candidate.instrument_id, observed_at)[:32],
                        run_id="ai-shadow-local",
                        instrument_id=candidate.instrument_id,
                        event_type="ai_shadow_decision",
                        state=effective_action,
                        reason_code="AI_SHADOW_DECISION",
                        observed_at=observed_at,
                        idempotency_key=_key(policy, candidate.instrument_id, observed_at),
                        payload=decision_payload,
                    )
                )

        effective_decision = decision.model_copy(update={"action": effective_action})
        completed_at = decision_completed_at or self.now_factory()
        if completed_at.tzinfo is None:
            raise ValueError("ai shadow decision completion must be timezone-aware")
        completed_at = completed_at.astimezone(timezone.utc)
        pending_exit = _pending_exit_intent(
            events,
            policy=policy,
            instrument_id=candidate.instrument_id,
        )
        if effective_action == "exit" and position.is_long and pending_exit is None:
            intent_payload = {
                "policy_version": AI_SHADOW_POLICY_VERSION,
                "policy": policy,
                "trade_id": position.trade_id,
                "exit_decision_at": observed_at,
                "execution_actionable_at": completed_at,
                "requested_units": str(position.normalized_units),
                "model_action": decision.action,
                "thesis": decision.thesis,
                "reason": decision.reason,
                "position_at_request": position.model_dump(mode="json"),
                "research_only": True,
                "execution_authority": False,
            }
            intent_key = _key(
                policy,
                candidate.instrument_id,
                position.trade_id or "open-position",
                "exit-requested",
            )
            intent_persisted = await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_exit_intent",
                state="pending_execution",
                reason_code="AI_SHADOW_EXIT_REQUESTED",
                observed_at=observed_at,
                payload=intent_payload,
                identity=(
                    policy,
                    position.trade_id or candidate.instrument_id,
                    "exit-requested",
                ),
            )
            if intent_persisted:
                pending_exit = StrategyEvent(
                    strategy_id=config.strategy_id,
                    event_id=intent_key[:32],
                    run_id="ai-shadow-local",
                    instrument_id=candidate.instrument_id,
                    event_type="ai_shadow_exit_intent",
                    state="pending_execution",
                    reason_code="AI_SHADOW_EXIT_REQUESTED",
                    observed_at=observed_at,
                    idempotency_key=intent_key,
                    payload=intent_payload,
                )
                events.append(pending_exit)

        side, units = desired_fill(effective_decision, position)
        if side is None or units <= 0:
            return

        execution: dict[str, Any]
        pending_entry = _pending_entry_intent(
            events,
            policy=policy,
            instrument_id=candidate.instrument_id,
        )
        if execution_override is not None:
            execution = dict(execution_override)
        else:
            selection = self.execution_plane.first_causal_after(
                candidate.instrument_id,
                decision_completed_at=completed_at,
                actionable_at=completed_at,
                market_snapshot_as_of=observed_at,
                binding_id=candidate.binding_id,
            )
            if selection is None:
                if side == "buy" and pending_entry is None:
                    intent_payload = {
                        "policy_version": AI_SHADOW_POLICY_VERSION,
                        "policy": policy,
                        "entry_decision_at": observed_at,
                        "execution_actionable_at": completed_at,
                        "requested_units": str(units),
                        "decision": effective_decision.model_dump(mode="json"),
                        "reference_price": str(
                            reference_price_override
                            if reference_price_override is not None
                            else result.current_price
                        ),
                        "position_at_request": position.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    }
                    entry_key = _key(
                        policy,
                        candidate.instrument_id,
                        observed_at.astimezone(timezone.utc).isoformat(),
                        "entry-requested",
                    )
                    entry_persisted = await self._append(
                        repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="ai_shadow_entry_intent",
                        state="pending_execution",
                        reason_code="AI_SHADOW_ENTRY_WAITING_FOR_CAUSAL_QUOTE",
                        observed_at=completed_at,
                        payload=intent_payload,
                        identity=(
                            policy,
                            observed_at.astimezone(timezone.utc).isoformat(),
                            "entry-requested",
                        ),
                    )
                    if entry_persisted:
                        pending_entry = StrategyEvent(
                            strategy_id=config.strategy_id,
                            event_id=entry_key[:32],
                            run_id="ai-shadow-local",
                            instrument_id=candidate.instrument_id,
                            event_type="ai_shadow_entry_intent",
                            state="pending_execution",
                            reason_code="AI_SHADOW_ENTRY_WAITING_FOR_CAUSAL_QUOTE",
                            observed_at=completed_at,
                            idempotency_key=entry_key,
                            payload=intent_payload,
                        )
                        events.append(pending_entry)
                await self._append(
                    repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="ai_shadow_fill",
                    state="unfilled",
                    reason_code="AI_SHADOW_CAUSAL_EXECUTION_PENDING",
                    observed_at=completed_at,
                    payload={
                        "policy": policy,
                        "decision_at": observed_at,
                        "decision_completed_at": completed_at,
                        "side": side,
                        "requested_units": str(units),
                        "position_before": position.model_dump(mode="json"),
                        "position_after": position.model_dump(mode="json"),
                        "detail": (
                            "No shared execution-plane observation was recorded "
                            "after the decision became actionable."
                        ),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(
                        policy,
                        observed_at.astimezone(timezone.utc).isoformat(),
                        "causal-execution-pending",
                    ),
                )
                return
            execution = _execution_payload(selection.observation)
            execution["causal_execution_selection"] = selection.model_dump(mode="json")

        if side == "buy":
            spread = execution.get("spread_bps")
            spread_value = Decimal(str(spread)) if spread is not None else None
            allowed = (
                execution.get("execution_eligible") is True
                and execution.get("halted") is not True
                and spread_value is not None
                and spread_value <= config.risk.max_spread_bps
            )
            price_only_research = bool(
                execution.get("last") is not None
                and (execution.get("bid") is None or execution.get("ask") is None)
                and execution.get("halted") is not True
            )
            if not allowed and not price_only_research:
                if pending_entry is not None:
                    await self._append(
                        repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="ai_shadow_entry_intent",
                        state="rejected",
                        reason_code="AI_SHADOW_ENTRY_EXECUTION_VETO",
                        observed_at=(
                            source_time
                            if isinstance(source_time := execution.get("source_time"), datetime)
                            else completed_at
                        ),
                        payload={
                            "policy": policy,
                            "entry_decision_at": pending_entry.payload.get("entry_decision_at"),
                            "execution_actionable_at": pending_entry.payload.get("execution_actionable_at"),
                            "execution": execution,
                            "research_only": True,
                            "execution_authority": False,
                        },
                        identity=(
                            policy,
                            str(pending_entry.payload.get("entry_decision_at") or observed_at),
                            "entry-rejected",
                        ),
                    )
                await self._append(
                    repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="ai_shadow_fill",
                    state="vetoed",
                    reason_code="AI_SHADOW_ENTRY_EXECUTION_VETO",
                    observed_at=observed_at,
                    payload={
                        "policy": policy,
                        "decision_at": observed_at,
                        "side": side,
                        "requested_units": str(units),
                        "execution": execution,
                        "position_before": position.model_dump(mode="json"),
                        "position_after": position.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(policy, observed_at.astimezone(timezone.utc).isoformat(), "fill"),
                )
                return

        simulation = simulate_ai_shadow_fill(
            execution,
            side=side,
            instrument_id=candidate.instrument_id,
            binding_id=candidate.binding_id,
            decision_at=completed_at,
            requested_units=units,
            reference_price=(
                reference_price_override
                if reference_price_override is not None
                else result.current_price
            ),
            allow_degraded_price_only=(side == "buy"),
            degraded_spread_bps=config.risk.max_spread_bps,
        )
        trade_id = position.trade_id or _key(
            policy,
            candidate.instrument_id,
            observed_at.astimezone(timezone.utc).isoformat(),
            "trade",
        )[:24]
        after = apply_fill(position, simulation, trade_id=trade_id)
        fill_time = simulation.source_time or observed_at
        closed_trade = position.is_long and not after.is_long and simulation.should_fill
        persisted_after = (
            AIShadowPositionState(policy=policy, instrument_id=candidate.instrument_id)
            if closed_trade
            else after
        )
        fill_payload = {
            "policy_version": AI_SHADOW_POLICY_VERSION,
            "policy": policy,
            "trade_id": trade_id,
            "decision_at": (
                pending_entry.payload.get("entry_decision_at")
                if side == "buy" and pending_entry is not None
                else pending_exit.payload.get("exit_decision_at")
                if side == "sell" and pending_exit is not None
                else observed_at
            ),
            "execution_actionable_at": completed_at,
            "side": side,
            "requested_units": str(units),
            "simulation": simulation.model_dump(mode="json"),
            "position_before": position.model_dump(mode="json"),
            "position_after": persisted_after.model_dump(mode="json"),
            "closed_position": after.model_dump(mode="json") if closed_trade else None,
            "degraded_execution": after.degraded_fill_count > 0,
            "research_only": True,
            "execution_authority": False,
        }
        fill_persisted = await self._append(
            repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="ai_shadow_fill",
            state="filled" if simulation.should_fill else "unfilled",
            reason_code=(
                "AI_SHADOW_FILL_SIMULATED"
                if simulation.should_fill
                else "AI_SHADOW_FILL_NOT_EXECUTABLE"
            ),
            observed_at=fill_time,
            payload=fill_payload,
            identity=(policy, observed_at.astimezone(timezone.utc).isoformat(), "fill"),
        )
        if side == "buy" and pending_entry is not None:
            await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_entry_intent",
                state="executed" if simulation.should_fill else "rejected",
                reason_code=(
                    "AI_SHADOW_ENTRY_FILLED"
                    if simulation.should_fill
                    else "AI_SHADOW_ENTRY_NOT_EXECUTABLE"
                ),
                observed_at=fill_time,
                payload={
                    "policy_version": AI_SHADOW_POLICY_VERSION,
                    "policy": policy,
                    "entry_decision_at": pending_entry.payload.get("entry_decision_at"),
                    "execution_actionable_at": pending_entry.payload.get("execution_actionable_at"),
                    "fill_reason": simulation.fill_reason,
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(
                    policy,
                    str(pending_entry.payload.get("entry_decision_at") or observed_at),
                    "entry-complete",
                ),
            )

        if fill_persisted and simulation.should_fill:
            self.fill_count += 1
            events.append(
                StrategyEvent(
                    strategy_id=config.strategy_id,
                    event_id=_key(policy, candidate.instrument_id, observed_at, "fill")[:32],
                    run_id="ai-shadow-local",
                    instrument_id=candidate.instrument_id,
                    event_type="ai_shadow_fill",
                    state="filled",
                    reason_code="AI_SHADOW_FILL_SIMULATED",
                    observed_at=fill_time,
                    idempotency_key=_key(policy, candidate.instrument_id, observed_at, "fill"),
                    payload=fill_payload,
                )
            )

        if closed_trade and side == "sell":
            exit_payload = {
                "policy_version": AI_SHADOW_POLICY_VERSION,
                "policy": policy,
                "trade_id": trade_id,
                "exit_decision_at": (
                    pending_exit.payload.get("exit_decision_at")
                    if pending_exit is not None
                    else observed_at
                ),
                "exit_fill_at": fill_time,
                "fill_event_state": "filled",
                "research_only": True,
                "execution_authority": False,
            }
            await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_exit_intent",
                state="exited",
                reason_code="AI_SHADOW_EXIT_FILLED",
                observed_at=fill_time,
                payload=exit_payload,
                identity=(policy, trade_id, "exit-filled"),
            )

        if closed_trade:
            assert after.entry_time is not None
            assert after.first_entry_price is not None
            net_return = (
                after.realized_pnl / after.total_buy_notional * Decimal("100")
                if after.total_buy_notional > 0
                else None
            )
            reference_return = (
                (after.realized_pnl + after.execution_drag_dollars)
                / after.total_reference_buy_notional
                * Decimal("100")
                if after.total_reference_buy_notional > 0
                else None
            )
            drag_pct = (
                after.execution_drag_dollars
                / after.total_reference_buy_notional
                * Decimal("100")
                if after.total_reference_buy_notional > 0
                else None
            )
            mfe, mae = _mfe_mae(
                bars,
                entry_time=after.entry_time,
                exit_time=fill_time,
                entry_price=after.first_entry_price,
            )
            decision_count, action_changes, tokens = _action_changes(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
                start=after.entry_time,
                end=fill_time,
            )
            trade_payload = {
                "policy_version": AI_SHADOW_POLICY_VERSION,
                "policy": policy,
                "trade_id": trade_id,
                "entry_time": after.entry_time,
                "exit_time": fill_time,
                "hold_minutes": str(
                    Decimal(str((fill_time - after.entry_time).total_seconds())) / Decimal("60")
                ),
                "first_entry_price": str(after.first_entry_price),
                "total_buy_notional": str(after.total_buy_notional),
                "realized_pnl_per_normalized_position": str(after.realized_pnl),
                "reference_return_pct": str(reference_return) if reference_return is not None else None,
                "net_execution_return_pct": str(net_return) if net_return is not None else None,
                "execution_drag_pct": str(drag_pct) if drag_pct is not None else None,
                "mfe_pct": str(mfe) if mfe is not None else None,
                "mae_pct": str(mae) if mae is not None else None,
                "fill_count": after.fill_count,
                "degraded_fill_count": after.degraded_fill_count,
                "degraded_execution": after.degraded_fill_count > 0,
                "decision_count": decision_count,
                "action_change_count": action_changes,
                "decision_stability": (
                    str(Decimal("1") - Decimal(action_changes) / Decimal(max(1, decision_count - 1)))
                    if decision_count > 1
                    else "1"
                ),
                "allocated_llm_tokens": tokens,
                "research_only": True,
                "execution_authority": False,
            }
            trade_persisted = await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_trade",
                state="closed",
                reason_code="AI_SHADOW_TRADE_CLOSED",
                observed_at=fill_time,
                payload=trade_payload,
                identity=(policy, trade_id, "closed"),
            )
            if trade_persisted:
                self.trade_count += 1

    async def _filter_morning_evidence_rows(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for row in rows:
            candidate = row["candidate"]
            observed_at = row["observed_at"]
            assert isinstance(observed_at, datetime)
            if isinstance(candidate, GapperCandidate):
                morning_ok, reasons = candidate_morning_evidence_eligible(
                    candidate,
                    config.config,
                )
            else:
                morning_ok, reasons = True, ()
            if morning_ok:
                prepared.append(row)
                continue
            persisted = await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_input_gap",
                state="unavailable",
                reason_code="AI_SHADOW_MORNING_EVIDENCE_GAP",
                observed_at=observed_at,
                payload={
                    "policy": policy,
                    "universe_id": row.get("universe_id"),
                    "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                    "morning_evidence_reason_codes": list(reasons),
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(
                    policy,
                    observed_at.astimezone(timezone.utc).isoformat(),
                    "morning_evidence",
                    *reasons,
                ),
            )
            if persisted:
                self.data_gap_count += 1
            trade_log(
                "auto_trading",
                "ai_shadow_input_gap",
                strategy_id=config.strategy_id,
                policy=policy,
                instrument_id=candidate.instrument_id,
                reason_code="AI_SHADOW_MORNING_EVIDENCE_GAP",
                morning_evidence_reason_codes=list(reasons),
                execution_authority=False,
            )
        return prepared

    async def _minute_cohort_watermark_ready(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
    ) -> bool:
        if policy != "minute":
            return True
        universe_id = str(rows[0].get("universe_id") or "")
        try:
            universe = repository.get_universe(universe_id)
        except Exception:
            universe = None
        expected_ids = {
            candidate.instrument_id
            for candidate in universe.candidates
            if candidate_morning_evidence_eligible(candidate, config.config)[0]
        } if universe is not None else set()
        observed_by_id = {
            row["candidate"].instrument_id: row["observed_at"]
            for row in rows
        }
        row_ids = set(observed_by_id)
        watermarks = {
            observed.astimezone(timezone.utc)
            for observed in observed_by_id.values()
            if isinstance(observed, datetime)
        }
        cohort_complete = not expected_ids or row_ids == expected_ids
        common_watermark = len(watermarks) == 1
        if cohort_complete and common_watermark:
            return True

        observed_at = max(watermarks, default=datetime.now(timezone.utc))
        persisted = await self._append(
            repository,
            config,
            instrument_id="__universe__",
            event_type="ai_shadow_input_gap",
            state="pending",
            reason_code="AI_SHADOW_COHORT_WATERMARK_PENDING",
            observed_at=observed_at,
            payload={
                "policy": policy,
                "universe_id": universe_id or None,
                "expected_instrument_ids": sorted(expected_ids),
                "available_instrument_ids": sorted(row_ids),
                "cohort_complete": cohort_complete,
                "common_finalized_minute": common_watermark,
                "candidate_watermarks": {
                    instrument_id: value.astimezone(timezone.utc).isoformat()
                    for instrument_id, value in sorted(observed_by_id.items())
                    if isinstance(value, datetime)
                },
                "research_only": True,
                "execution_authority": False,
            },
            identity=(
                policy,
                universe_id,
                tuple(sorted(expected_ids)),
                tuple(
                    sorted(
                        (
                            instrument_id,
                            value.astimezone(timezone.utc).isoformat(),
                        )
                        for instrument_id, value in observed_by_id.items()
                        if isinstance(value, datetime)
                    )
                ),
                "cohort_watermark_pending",
            ),
        )
        if persisted:
            self.data_gap_count += 1
        return False

    async def _filter_execution_input_rows(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
    ) -> list[dict[str, Any]]:
        valid_rows: list[dict[str, Any]] = []
        for row in rows:
            candidate = row["candidate"]
            observed_at = row["observed_at"]
            assert isinstance(observed_at, datetime)
            feature = row.get("feature_snapshot") or {}
            execution = feature.get("execution") if isinstance(feature, dict) else None
            missing_execution = (
                not isinstance(execution, dict)
                or execution.get("last") is None
            )
            coverage = assess_bar_coverage(
                list(row.get("bars") or []),
                session_date=observed_at.astimezone(_ET).date(),
                observed_at=observed_at,
                provider="configured_history",
            )
            if not missing_execution and coverage.ready:
                valid_rows.append(row)
                continue

            readiness = None
            if missing_execution:
                try:
                    market_service.execution_observation(
                        candidate.instrument_id,
                        candidate.binding_id,
                    )
                except Exception as exc:
                    readiness = classify_provider_exception(
                        exc,
                        provider=DEFAULT_MARKET_EVIDENCE_POLICY.execution_provider,
                        observed_at=observed_at,
                    )
            if readiness is None:
                readiness = ProviderReadiness(
                    provider=DEFAULT_MARKET_EVIDENCE_POLICY.execution_provider,
                    state="READY" if not missing_execution else "QUOTE_MISSING",
                    ready=not missing_execution,
                    observed_at=observed_at,
                    detail=None,
                )
            gap = ExecutionInputGap(
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
                provider=DEFAULT_MARKET_EVIDENCE_POLICY.execution_provider,
                readiness=readiness,
                reason_code=(
                    "AI_SHADOW_EXECUTION_INPUT_GAP"
                    if missing_execution
                    else "AI_SHADOW_BAR_COVERAGE_GAP"
                ),
                observed_at=observed_at,
            )
            persisted = await self._append(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_shadow_input_gap",
                state="unavailable",
                reason_code=gap.reason_code,
                observed_at=observed_at,
                payload={
                    "policy": policy,
                    "gap": gap.model_dump(mode="json"),
                    "bar_coverage": coverage.model_dump(mode="json"),
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(
                    policy,
                    observed_at.astimezone(timezone.utc).isoformat(),
                    gap.reason_code,
                ),
            )
            if persisted:
                self.data_gap_count += 1
            trade_log(
                "auto_trading",
                "ai_shadow_input_gap",
                strategy_id=config.strategy_id,
                policy=policy,
                instrument_id=candidate.instrument_id,
                reason_code=gap.reason_code,
                provider_readiness=readiness.model_dump(mode="json"),
                bar_coverage=coverage.model_dump(mode="json"),
                execution_authority=False,
            )
        return valid_rows

    async def _prepare_policy_rows(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
    ) -> list[dict[str, Any]]:
        if rows and all(
            not isinstance(row.get("candidate"), GapperCandidate)
            for row in rows
        ):
            return rows
        prepared = await self._filter_morning_evidence_rows(
            policy=policy,
            rows=rows,
            config=config,
            repository=repository,
        )
        if not prepared or not await self._minute_cohort_watermark_ready(
            policy=policy,
            rows=prepared,
            config=config,
            repository=repository,
        ):
            return []
        return await self._filter_execution_input_rows(
            policy=policy,
            rows=prepared,
            config=config,
            repository=repository,
            market_service=market_service,
        )

    async def _run_policy(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        events: list[StrategyEvent],
    ) -> None:
        rows = await self._prepare_policy_rows(
            policy=policy,
            rows=rows,
            config=config,
            repository=repository,
            market_service=market_service,
        )
        if not rows:
            return

        due: list[dict[str, Any]] = []
        reasons_by_id: dict[str, tuple[str, ...]] = {}
        for row in rows:
            candidate = row["candidate"]
            observed_at = row["observed_at"]
            assert isinstance(observed_at, datetime)
            observed_et = observed_at.astimezone(_ET).time()
            if observed_et >= config.risk.force_flat_et:
                continue

            position = _position_from_latest_fill(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
            )
            pending_exit = _pending_exit_intent(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
            )
            pending_entry = _pending_entry_intent(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
            )
            if position.is_long and pending_exit is not None:
                retry = AIShadowDecision(
                    instrument_id=candidate.instrument_id,
                    action="exit",
                    confidence=100,
                    market_regime="unresolved",
                    expected_horizon_minutes=1,
                    thesis="Durable exit intent remains authoritative.",
                    reason="Retry execution without another LLM decision.",
                    invalidation_price=None,
                )
                actionable_at = _payload_datetime(
                    pending_exit.payload.get("execution_actionable_at"),
                    pending_exit.observed_at,
                )
                await self._apply_decision(
                    policy=policy,
                    decision=retry,
                    row=row,
                    candidate=candidate,
                    bars=row["bars"],
                    config=config,
                    repository=repository,
                    market_service=market_service,
                    events=events,
                    result=row["learning"],
                    batch_result=None,
                    trigger_reasons=("durable_exit_retry",),
                    record_decision=False,
                    decision_completed_at=actionable_at,
                )
                continue
            if not position.is_long and pending_entry is not None:
                entry_block_reason: str | None = None
                if config.risk.kill_switch:
                    entry_block_reason = "AI_SHADOW_KILL_SWITCH"
                elif observed_et > config.risk.last_entry_et:
                    entry_block_reason = "AI_SHADOW_ENTRY_WINDOW_CLOSED"
                elif (
                    config.risk.one_trade_per_symbol_per_day
                    and _completed_trade_exists(
                        events,
                        policy=policy,
                        instrument_id=candidate.instrument_id,
                    )
                ):
                    entry_block_reason = "AI_SHADOW_ONE_TRADE_PER_SYMBOL"
                elif _started_trade_count(events, policy=policy) >= config.risk.max_trades_per_day:
                    entry_block_reason = "AI_SHADOW_MAX_TRADES_PER_DAY"
                elif _active_position_count(events, policy=policy) >= config.risk.max_positions:
                    entry_block_reason = "AI_SHADOW_MAX_POSITIONS"
                if entry_block_reason is not None:
                    await self._append(
                        repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="ai_shadow_entry_intent",
                        state="cancelled",
                        reason_code=entry_block_reason,
                        observed_at=observed_at,
                        payload={
                            "policy_version": AI_SHADOW_POLICY_VERSION,
                            "policy": policy,
                            "entry_decision_at": pending_entry.payload.get("entry_decision_at"),
                            "execution_actionable_at": pending_entry.payload.get("execution_actionable_at"),
                            "detail": "Pending entry was cancelled before execution because the deterministic risk boundary changed.",
                            "research_only": True,
                            "execution_authority": False,
                        },
                        identity=(
                            policy,
                            str(pending_entry.payload.get("entry_decision_at") or pending_entry.observed_at),
                            "entry-cancelled",
                        ),
                    )
                    continue
                raw_decision = pending_entry.payload.get("decision")
                retry = (
                    AIShadowDecision.model_validate(raw_decision)
                    if isinstance(raw_decision, dict)
                    else AIShadowDecision(
                        instrument_id=candidate.instrument_id,
                        action="enter",
                        confidence=100,
                        market_regime="unresolved",
                        expected_horizon_minutes=1,
                        thesis="Durable entry intent is waiting for causal execution.",
                        reason="Retry execution without another LLM decision.",
                        invalidation_price=None,
                    )
                )
                actionable_at = _payload_datetime(
                    pending_entry.payload.get("execution_actionable_at"),
                    pending_entry.observed_at,
                )
                await self._apply_decision(
                    policy=policy,
                    decision=retry,
                    row=row,
                    candidate=candidate,
                    bars=row["bars"],
                    config=config,
                    repository=repository,
                    market_service=market_service,
                    events=events,
                    result=row["learning"],
                    batch_result=None,
                    trigger_reasons=("durable_entry_retry",),
                    record_decision=False,
                    decision_completed_at=actionable_at,
                    reference_price_override=_decimal(
                        pending_entry.payload.get("reference_price")
                    ),
                )
                continue
            if not position.is_long:
                if config.risk.kill_switch or observed_et > config.risk.last_entry_et:
                    continue
                if (
                    config.risk.one_trade_per_symbol_per_day
                    and _completed_trade_exists(
                        events,
                        policy=policy,
                        instrument_id=candidate.instrument_id,
                    )
                ):
                    continue

            if _decision_exists(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
                observed_at=observed_at,
            ):
                continue
            previous = _previous_decision(
                events,
                policy=policy,
                instrument_id=candidate.instrument_id,
            )
            previous_feature = (
                previous.payload.get("feature_snapshot")
                if previous is not None
                and isinstance(previous.payload.get("feature_snapshot"), dict)
                else None
            )
            previous_decision = (
                previous.payload.get("decision")
                if previous is not None
                and isinstance(previous.payload.get("decision"), dict)
                else None
            )
            if policy == "event":
                reasons = event_trigger_reasons(
                    row["feature_snapshot"],
                    previous_feature,
                    prior_decision=previous_decision,
                )
                if not reasons:
                    continue
            else:
                reasons = ("completed_1m_bar",)

            analyzer_row = {
                "instrument_id": candidate.instrument_id,
                "observed_at": observed_at.isoformat(),
                "trigger_reasons": list(reasons),
                "feature_snapshot": row["feature_snapshot"],
                "previous_decision": previous_decision,
            }
            due.append({**row, "analyzer_row": analyzer_row})
            reasons_by_id[candidate.instrument_id] = reasons

        if not due:
            return

        observed_at = max(row["observed_at"] for row in due)
        assert isinstance(observed_at, datetime)
        request_members = _batch_request_members(due)
        request_signature = _batch_request_signature(request_members)
        requested_instrument_ids = [
            member["instrument_id"] for member in request_members
        ]
        # A persisted complete/error batch checkpoints the exact causal request,
        # not merely the latest minute. Symbols can finalize asynchronously; a
        # later due set at the same max minute must still receive its decision.
        if _batch_checkpoint_exists(
            events,
            policy=policy,
            observed_at=observed_at,
            request_signature=request_signature,
        ):
            return

        analyzer = self.analyzer_factory()
        if policy == "minute":
            self.minute_llm_call_count += 1
        else:
            self.event_llm_call_count += 1
        try:
            batch = await asyncio.to_thread(
                analyzer.assess,
                policy=policy,
                rows=[row["analyzer_row"] for row in due],
            )
        except Exception as exc:
            await self._append(
                repository,
                config,
                instrument_id="__universe__",
                event_type="ai_shadow_batch",
                state="error",
                reason_code="AI_SHADOW_LLM_BATCH_ERROR",
                observed_at=observed_at,
                payload={
                    "policy": policy,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                    "request_signature": request_signature,
                    "requested_instrument_ids": requested_instrument_ids,
                    "requested_observations": request_members,
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(
                    policy,
                    observed_at.astimezone(timezone.utc).isoformat(),
                    request_signature,
                    "error",
                ),
            )
            trade_log(
                "auto_trading",
                "ai_shadow_llm_error",
                strategy_id=config.strategy_id,
                policy=policy,
                error_type=type(exc).__name__,
                detail=str(exc),
                execution_authority=False,
            )
            return

        self.total_token_count += batch.total_tokens
        await self._append(
            repository,
            config,
            instrument_id="__universe__",
            event_type="ai_shadow_batch",
            state="complete",
            reason_code="AI_SHADOW_LLM_BATCH_COMPLETE",
            observed_at=observed_at,
            payload={
                "policy": policy,
                "provider": batch.provider,
                "model": batch.model,
                "decision_count": len(batch.decisions),
                "input_tokens": batch.input_tokens,
                "output_tokens": batch.output_tokens,
                "total_tokens": batch.total_tokens,
                "usage_source": batch.usage_source,
                "request_signature": request_signature,
                "requested_instrument_ids": requested_instrument_ids,
                "requested_observations": request_members,
                "research_only": True,
                "execution_authority": False,
            },
            identity=(
                policy,
                observed_at.astimezone(timezone.utc).isoformat(),
                request_signature,
                "batch",
            ),
        )

        by_id = {row["candidate"].instrument_id: row for row in due}
        decision_completed_at = self.now_factory()
        if decision_completed_at.tzinfo is None:
            raise ValueError("ai shadow decision completion must be timezone-aware")
        for decision in batch.decisions:
            row = cast(dict[str, Any], by_id.get(decision.instrument_id))
            if row is None:
                continue
            await self._apply_decision(
                policy=policy,
                decision=decision,
                row=row,
                candidate=row["candidate"],
                bars=row["bars"],
                config=config,
                repository=repository,
                market_service=market_service,
                events=events,
                result=row["learning"],
                batch_result=batch,
                trigger_reasons=reasons_by_id.get(decision.instrument_id, ()),
                decision_completed_at=decision_completed_at,
            )

    async def _force_flat_open_positions(
        self,
        *,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        events: list[StrategyEvent],
        now: datetime,
    ) -> None:
        now_et = now.astimezone(_ET)
        if now_et.time() < config.risk.force_flat_et or after_regular_close(now_et):
            return
        for policy in ("minute", "event"):
            for row in rows:
                candidate = row["candidate"]
                position = _position_from_latest_fill(
                    events,
                    policy=policy,
                    instrument_id=candidate.instrument_id,
                )
                if not position.is_long:
                    continue
                synthetic = AIShadowDecision(
                    instrument_id=candidate.instrument_id,
                    action="exit",
                    confidence=100,
                    market_regime="unresolved",
                    expected_horizon_minutes=1,
                    thesis="Force-flat safety boundary.",
                    reason="The configured regular-session force-flat time has been reached.",
                    invalidation_price=None,
                )
                forced_row = dict(row)
                forced_row["observed_at"] = now
                pending_exit = _pending_exit_intent(
                    events,
                    policy=policy,
                    instrument_id=candidate.instrument_id,
                )
                await self._apply_decision(
                    policy=policy,
                    decision=synthetic,
                    row=forced_row,
                    candidate=candidate,
                    bars=row["bars"],
                    config=config,
                    repository=repository,
                    market_service=market_service,
                    events=events,
                    result=row["learning"],
                    batch_result=None,
                    trigger_reasons=(
                        ("durable_exit_retry",)
                        if pending_exit is not None
                        else ("force_flat",)
                    ),
                    record_decision=pending_exit is None,
                    decision_completed_at=(
                        _payload_datetime(
                            pending_exit.payload.get("execution_actionable_at"),
                            pending_exit.observed_at,
                        )
                        if pending_exit is not None
                        else now
                    ),
                )

    async def _mark_incomplete_open_trades(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        session_date,
        now: datetime,
    ) -> None:
        if not after_regular_close(now):
            return
        for policy in ("minute", "event"):
            instrument_ids = {
                event.instrument_id
                for event in events
                if event.event_type == "ai_shadow_fill"
                and event.payload.get("policy") == policy
                and event.instrument_id != "__universe__"
            }
            for instrument_id in sorted(instrument_ids):
                position = _position_from_latest_fill(
                    events,
                    policy=policy,
                    instrument_id=instrument_id,
                )
                if not position.is_long or not position.trade_id:
                    continue
                already_recorded = any(
                    event.event_type == "ai_shadow_trade"
                    and event.payload.get("policy") == policy
                    and event.payload.get("trade_id") == position.trade_id
                    for event in events
                )
                if already_recorded:
                    continue
                pending_exit = _pending_exit_intent(
                    events,
                    policy=policy,
                    instrument_id=instrument_id,
                )
                if pending_exit is not None:
                    await self._append(
                        repository,
                        config,
                        instrument_id=instrument_id,
                        event_type="ai_shadow_exit_intent",
                        state="unresolved",
                        reason_code="AI_SHADOW_EXIT_UNRESOLVED_AT_SESSION_END",
                        observed_at=now,
                        payload={
                            "policy_version": AI_SHADOW_POLICY_VERSION,
                            "policy": policy,
                            "trade_id": position.trade_id,
                            "exit_decision_at": pending_exit.payload.get("exit_decision_at"),
                            "position": position.model_dump(mode="json"),
                            "detail": (
                                "Exit remained authoritative, but no executable "
                                "sell observation was captured before session end."
                            ),
                            "research_only": True,
                            "execution_authority": False,
                        },
                        identity=(policy, position.trade_id, "exit-unresolved"),
                    )
                await self._append(
                    repository,
                    config,
                    instrument_id=instrument_id,
                    event_type="ai_shadow_trade",
                    state="incomplete",
                    reason_code="AI_SHADOW_FORCE_FLAT_EXECUTION_INCOMPLETE",
                    observed_at=now,
                    payload={
                        "policy_version": AI_SHADOW_POLICY_VERSION,
                        "policy": policy,
                        "trade_id": position.trade_id,
                        "session_date": session_date.isoformat(),
                        "entry_time": position.entry_time,
                        "position": position.model_dump(mode="json"),
                        "data_complete": False,
                        "net_execution_return_pct": None,
                        "detail": (
                            "The normalized SHADOW position remained open after "
                            "the force-flat boundary because no executable exit "
                            "fill was captured. No return is synthesized."
                        ),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(policy, position.trade_id, "incomplete"),
                )

    async def _session_summary(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        session_date,
        now: datetime,
    ) -> None:
        events = _events_with_data_gap_aliases(events)
        if not after_regular_close(now):
            return
        for policy in ("minute", "event"):
            all_trades = sorted(
                [
                    event
                    for event in events
                    if event.event_type == "ai_shadow_trade"
                    and event.payload.get("policy") == policy
                ],
                key=lambda event: event.observed_at,
            )
            closed_trades = [event for event in all_trades if event.state == "closed"]
            trades = [
                event for event in closed_trades
                if event.payload.get("degraded_execution") is not True
            ]
            hypothetical_trades = [
                event for event in closed_trades
                if event.payload.get("degraded_execution") is True
            ]
            incomplete_trades = [
                event for event in all_trades if event.state == "incomplete"
            ]
            returns = [
                Decimal(str(event.payload["net_execution_return_pct"]))
                for event in trades
                if event.payload.get("net_execution_return_pct") is not None
            ]
            drags = [
                Decimal(str(event.payload["execution_drag_pct"]))
                for event in trades
                if event.payload.get("execution_drag_pct") is not None
            ]
            stabilities = [
                Decimal(str(event.payload["decision_stability"]))
                for event in trades
                if event.payload.get("decision_stability") is not None
            ]
            total_decisions, total_action_changes, overall_stability = _policy_action_stats(
                events,
                policy=policy,
            )
            policy_batches = [
                event
                for event in events
                if event.event_type == "ai_shadow_batch"
                and event.payload.get("policy") == policy
            ]
            complete_batches = [event for event in policy_batches if event.state == "complete"]
            error_batches = [event for event in policy_batches if event.state == "error"]
            data_gaps = [
                event for event in events if event.event_type == "ai_shadow_data_gap"
            ]
            payload = {
                "policy_version": AI_SHADOW_POLICY_VERSION,
                "policy": policy,
                "session_date": session_date.isoformat(),
                "trade_count": len(trades),
                "hypothetical_trade_count": len(hypothetical_trades),
                "total_closed_trade_count": len(closed_trades),
                "incomplete_trade_count": len(incomplete_trades),
                "win_count": sum(1 for value in returns if value > 0),
                "loss_count": sum(1 for value in returns if value < 0),
                "mean_net_return_pct": (
                    str(sum(returns, Decimal("0")) / Decimal(len(returns)))
                    if returns
                    else None
                ),
                "trade_sequence_max_drawdown_pct": (
                    str(_max_drawdown(returns)) if returns else None
                ),
                "mean_execution_drag_pct": (
                    str(sum(drags, Decimal("0")) / Decimal(len(drags)))
                    if drags
                    else None
                ),
                "total_decisions": total_decisions,
                "total_action_changes": total_action_changes,
                "overall_decision_stability": (
                    str(overall_stability) if overall_stability is not None else None
                ),
                "median_not_reported": True,
                "mean_trade_decision_stability": (
                    str(sum(stabilities, Decimal("0")) / Decimal(len(stabilities)))
                    if stabilities
                    else None
                ),
                "trade_allocated_llm_tokens": sum(
                    int(event.payload.get("allocated_llm_tokens") or 0)
                    for event in trades
                ),
                "llm_call_count": len(complete_batches),
                "llm_error_count": len(error_batches),
                "llm_total_tokens": sum(
                    int(event.payload.get("total_tokens") or 0)
                    for event in complete_batches
                ),
                "market_data_gap_event_count": len(data_gaps),
                "research_only": True,
                "execution_authority": False,
            }
            await self._append(
                repository,
                config,
                instrument_id="__universe__",
                event_type="ai_shadow_session_summary",
                state="complete",
                reason_code="AI_SHADOW_SESSION_SUMMARY",
                observed_at=now,
                payload=payload,
                identity=(
                    policy,
                    session_date.isoformat(),
                    _key(
                        [event.event_id for event in all_trades],
                        [
                            event.event_id
                            for event in events
                            if event.event_type == "ai_shadow_decision"
                            and event.payload.get("policy") == policy
                        ],
                        [event.event_id for event in policy_batches],
                        [event.event_id for event in data_gaps],
                    ),
                    "summary",
                ),
            )

    async def _comparison_summary(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        session_date,
        cohort_candidate_count: int,
        now: datetime,
    ) -> None:
        events = _events_with_data_gap_aliases(events)
        if not after_regular_close(now):
            return

        deterministic_replays = sorted(
            [
                event for event in events if event.event_type == "v2_shadow_replay_trade"
            ],
            key=lambda event: str(event.payload.get("entry_time") or event.observed_at.isoformat()),
        )
        deterministic_r = [
            value
            for event in deterministic_replays
            if (value := _decimal(event.payload.get("r_result"))) is not None
        ]
        deterministic_r_drawdown = _max_drawdown(deterministic_r)
        entry_ready_events = [
            event
            for event in events
            if event.event_type == "state" and event.state == "entry_ready"
        ]
        deterministic_shadow_execution = [
            event
            for event in events
            if event.event_type == "shadow_execution"
            and event.payload.get("strategy_version") == "2.0.0"
        ]
        deterministic_live_spreads: list[Decimal] = []
        deterministic_live_eligible = 0
        for event in deterministic_shadow_execution:
            execution = event.payload.get("execution")
            if not isinstance(execution, dict):
                continue
            if execution.get("execution_eligible") is True:
                deterministic_live_eligible += 1
            spread = _decimal(execution.get("spread_bps"))
            if spread is not None:
                deterministic_live_spreads.append(spread)

        stoch_events = sorted(
            [
                event for event in events if event.event_type == "stoch_trend_execution_summary"
            ],
            key=lambda event: event.observed_at,
        )
        stoch_net: list[Decimal] = []
        stoch_drag: list[Decimal] = []
        for event in stoch_events:
            policy_payload = event.payload.get("policy")
            if not isinstance(policy_payload, dict) or policy_payload.get("complete") is not True:
                continue
            value = _decimal(policy_payload.get("net_execution_return_pct"))
            if value is not None:
                stoch_net.append(value)
            drag = _decimal(policy_payload.get("execution_drag_pct"))
            if drag is not None:
                stoch_drag.append(drag)

        def ai_arm(policy: AIShadowPolicy) -> dict[str, Any]:
            all_trades = sorted(
                [
                    event
                    for event in events
                    if event.event_type == "ai_shadow_trade"
                    and event.payload.get("policy") == policy
                ],
                key=lambda event: event.observed_at,
            )
            closed_trades = [event for event in all_trades if event.state == "closed"]
            trades = [
                event for event in closed_trades
                if event.payload.get("degraded_execution") is not True
            ]
            hypothetical_trades = [
                event for event in closed_trades
                if event.payload.get("degraded_execution") is True
            ]
            incomplete_trades = [
                event for event in all_trades if event.state == "incomplete"
            ]
            returns = [
                value
                for event in trades
                if (value := _decimal(event.payload.get("net_execution_return_pct"))) is not None
            ]
            maes = [
                value
                for event in trades
                if (value := _decimal(event.payload.get("mae_pct"))) is not None
            ]
            stabilities = [
                value
                for event in trades
                if (value := _decimal(event.payload.get("decision_stability"))) is not None
            ]
            drags = [
                value
                for event in trades
                if (value := _decimal(event.payload.get("execution_drag_pct"))) is not None
            ]
            policy_batches = [
                event
                for event in events
                if event.event_type == "ai_shadow_batch"
                and event.payload.get("policy") == policy
            ]
            batches = [event for event in policy_batches if event.state == "complete"]
            error_batches = [event for event in policy_batches if event.state == "error"]
            total_decisions, total_action_changes, overall_stability = _policy_action_stats(
                events,
                policy=policy,
            )
            trade_decision_count = sum(
                int(event.payload.get("decision_count") or 0) for event in trades
            )
            trade_action_changes = sum(
                int(event.payload.get("action_change_count") or 0) for event in trades
            )
            return {
                "trade_count": len(trades),
                "hypothetical_trade_count": len(hypothetical_trades),
                "total_closed_trade_count": len(closed_trades),
                "incomplete_trade_count": len(incomplete_trades),
                "win_count": sum(1 for value in returns if value > 0),
                "mean_net_execution_return_pct": (
                    str(sum(returns, Decimal("0")) / Decimal(len(returns)))
                    if returns
                    else None
                ),
                "trade_sequence_max_drawdown_pct": (
                    str(_max_drawdown(returns)) if returns else None
                ),
                "worst_mae_pct": str(min(maes)) if maes else None,
                "mean_execution_drag_pct": (
                    str(sum(drags, Decimal("0")) / Decimal(len(drags)))
                    if drags
                    else None
                ),
                "total_decisions": total_decisions,
                "total_action_changes": total_action_changes,
                "overall_decision_stability": (
                    str(overall_stability) if overall_stability is not None else None
                ),
                "trade_decision_count": trade_decision_count,
                "trade_action_change_count": trade_action_changes,
                "mean_trade_decision_stability": (
                    str(sum(stabilities, Decimal("0")) / Decimal(len(stabilities)))
                    if stabilities
                    else None
                ),
                "llm_call_count": len(batches),
                "llm_error_count": len(error_batches),
                "llm_total_tokens": sum(
                    int(event.payload.get("total_tokens") or 0) for event in batches
                ),
            }

        data_gaps = [
            event for event in events if event.event_type == "ai_shadow_data_gap"
        ]
        payload = {
            "policy_version": AI_SHADOW_POLICY_VERSION,
            "session_date": session_date.isoformat(),
            "comparison_basis": "same frozen Finviz Top-5 cohort; causal SHADOW evidence",
            "shared_data_quality": {
                "cohort_candidate_count": cohort_candidate_count,
                "market_data_gap_event_count": len(data_gaps),
                "symbols_with_market_data_gaps": sorted(
                    {
                        event.instrument_id
                        for event in data_gaps
                        if event.instrument_id != "__universe__"
                    }
                ),
            },
            "arm_a_deterministic_v2": {
                "entry_ready_event_count": len(entry_ready_events),
                "live_shadow_execution_observation_count": len(deterministic_shadow_execution),
                "live_execution_eligible_signal_count": deterministic_live_eligible,
                "mean_live_entry_spread_bps": (
                    str(
                        sum(deterministic_live_spreads, Decimal("0"))
                        / Decimal(len(deterministic_live_spreads))
                    )
                    if deterministic_live_spreads
                    else None
                ),
                "replay_trade_count": len(deterministic_replays),
                "mean_r_result": (
                    str(sum(deterministic_r, Decimal("0")) / Decimal(len(deterministic_r)))
                    if deterministic_r
                    else None
                ),
                "trade_sequence_max_drawdown_r": (
                    str(deterministic_r_drawdown)
                    if deterministic_r_drawdown is not None
                    else None
                ),
                "return_unit": "R",
                "execution_model": "canonical_v2_postclose_replay_with_assumed_spread",
                "assumed_spread_bps_values": sorted(
                    {
                        str(value)
                        for event in deterministic_replays
                        if (
                            value := _decimal(event.payload.get("assumed_spread_bps"))
                        )
                        is not None
                    }
                ),
            },
            "arm_b_stoch_trend": {
                "completed_trade_count": len(stoch_net),
                "mean_net_execution_return_pct": (
                    str(sum(stoch_net, Decimal("0")) / Decimal(len(stoch_net)))
                    if stoch_net
                    else None
                ),
                "trade_sequence_max_drawdown_pct": (
                    str(_max_drawdown(stoch_net)) if stoch_net else None
                ),
                "mean_execution_drag_pct": (
                    str(sum(stoch_drag, Decimal("0")) / Decimal(len(stoch_drag)))
                    if stoch_drag
                    else None
                ),
                "return_unit": "percent",
                "execution_model": "point_in_time_bid_ask_plus_paper_execution_v2",
            },
            "arm_c_ai_every_minute": {
                **ai_arm("minute"),
                "execution_model": "point_in_time_bid_ask_plus_paper_execution_v2",
            },
            "arm_d_ai_event_driven": {
                **ai_arm("event"),
                "execution_model": "point_in_time_bid_ask_plus_paper_execution_v2",
            },
            "cross_arm_return_units_harmonized": False,
            "cross_arm_execution_models_harmonized": False,
            "ranking_deferred_until_risk_normalized": True,
            "research_only": True,
            "execution_authority": False,
        }
        identity_signature = _key(
            len(deterministic_replays),
            len(deterministic_shadow_execution),
            len(stoch_net),
            payload["shared_data_quality"],
            payload["arm_c_ai_every_minute"],
            payload["arm_d_ai_event_driven"],
        )
        await self._append(
            repository,
            config,
            instrument_id="__universe__",
            event_type="shadow_strategy_comparison",
            state="snapshot",
            reason_code="SHADOW_STRATEGY_COMPARISON_SNAPSHOT",
            observed_at=now,
            payload=payload,
            identity=(session_date.isoformat(), identity_signature),
        )

    async def _run_config(
        self,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        *,
        now: datetime,
    ) -> None:
        market_service = cast(TradingMarketDataService, _CurrentSessionMarketDataProxy(
            market_service,
            session_date=now.astimezone(_ET).date(),
            observed_at=now,
        ))
        market_service = cast(TradingMarketDataService, _FullSessionMarketServiceProxy(
            market_service,
            session_date=now.astimezone(_ET).date(),
            observed_at=now,
            allow_shadow_fallback=True,
        ))
        universe = await asyncio.to_thread(
            resolve_v2_shadow_archive,
            config,
            repository,
            now=now,
        )
        if universe is None:
            return
        events = await self._session_events(
            repository,
            config,
            session_date=universe.session_date,
            now=now,
        )

        rows: list[dict[str, Any]] = []
        for rank, candidate in enumerate(universe.candidates, start=1):
            try:
                response = await asyncio.to_thread(
                    market_service.bars,
                    candidate.instrument_id,
                    "1m",
                    500,
                    candidate.binding_id,
                )
                bars = sorted(
                    [bar for bar in response.bars if bar.is_final and bar.end_time <= now],
                    key=lambda bar: bar.end_time,
                )
                structure = list(resample_final_bars(bars, config.config.structure_interval))
                if not structure:
                    continue
                result = evaluate_gap_pullback(candidate, structure, config.config)
                observed_at = structure[-1].end_time
                observed_et = observed_at.astimezone(_ET).time()
                if observed_et < config.config.entry_start_et:
                    continue
                learning = build_intraday_learning_snapshot(candidate, result, bars)
                indicators = multi_timeframe_indicator_context(
                    [bar for bar in bars if bar.session == "regular"]
                )
                execution_envelope = self.execution_plane.latest(
                    candidate.instrument_id,
                    as_of=now,
                )
                if (
                    execution_envelope is not None
                    and execution_envelope.observation.binding_id == candidate.binding_id
                ):
                    execution = _execution_payload(execution_envelope.observation)
                    execution["execution_plane_recorded_at"] = execution_envelope.recorded_at
                else:
                    latest_bar = bars[-1]
                    execution = {
                        "provider": latest_bar.provider or "configured_history",
                        "binding_id": candidate.binding_id,
                        "bid": None,
                        "ask": None,
                        "last": latest_bar.close,
                        "spread_bps": None,
                        "execution_eligible": False,
                        "halted": None,
                        "source_time": latest_bar.end_time,
                        "freshness_mode": "fallback",
                        "observation_quality": "bar_close_fallback",
                        "rejection_reasons": (
                            "EXECUTION_PLANE_OBSERVATION_UNAVAILABLE",
                            "RESEARCH_BAR_CLOSE_FALLBACK",
                        ),
                    }
                positions = {
                    policy: _position_from_latest_fill(
                        events,
                        policy=policy,
                        instrument_id=candidate.instrument_id,
                    )
                    for policy in ("minute", "event")
                }
                rows.append(
                    {
                        "candidate": candidate,
                        "bars": bars,
                        "deterministic": result,
                        "learning": learning,
                        "indicators": indicators,
                        "execution": execution,
                        "observed_at": observed_at,
                        "universe_id": universe.universe_id,
                        "rank": rank,
                        "positions": positions,
                    }
                )
            except Exception as exc:
                self.last_error = (
                    f"{config.strategy_id}/{candidate.instrument_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                gap_at = now.replace(second=0, microsecond=0)
                persisted_gap = await self._append(
                    repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="ai_shadow_data_gap",
                    state="unavailable",
                    reason_code="AI_SHADOW_MARKET_CONTEXT_UNAVAILABLE",
                    observed_at=gap_at,
                    payload={
                        "universe_id": universe.universe_id,
                        "morning_discovery_rank": candidate.discovery_rank,
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                        "minute_bucket": gap_at.isoformat(),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(
                        universe.universe_id,
                        candidate.instrument_id,
                        gap_at.isoformat(),
                        type(exc).__name__,
                    ),
                )
                if persisted_gap:
                    self.data_gap_count += 1
                continue

        if not rows:
            return

        for policy in ("minute", "event"):
            for row in rows:
                position = row["positions"][policy]
                row.setdefault("feature_by_policy", {})
                shared_snapshot = feature_snapshot(
                    candidate=row["candidate"],
                    deterministic=row["deterministic"],
                    learning=row["learning"],
                    indicators=row["indicators"],
                    bars=row["bars"],
                    execution=row["execution"],
                    cohort_rank=int(row["rank"]),
                    position=position,
                )
                row["feature_by_policy"][policy] = policy_feature_snapshot(
                    shared_snapshot,
                    policy=policy,
                )

        policy_batches: dict[AIShadowPolicy, list[dict[str, Any]]] = {
            "minute": [],
            "event": [],
        }
        for batch_policy in cast(tuple[AIShadowPolicy, ...], ("minute", "event")):
            for row in rows:
                copied = dict(row)
                copied["feature_snapshot"] = row["feature_by_policy"][batch_policy]
                policy_batches[batch_policy].append(copied)

        # The two AI arms are independent SHADOW namespaces. Run their model
        # calls concurrently so event-driven analysis cannot make the every-
        # minute arm miss the next finalized minute merely due to serial LLM
        # latency.
        await asyncio.gather(
            self._run_policy(
                policy="minute",
                rows=policy_batches["minute"],
                config=config,
                repository=repository,
                market_service=market_service,
                events=events,
            ),
            self._run_policy(
                policy="event",
                rows=policy_batches["event"],
                config=config,
                repository=repository,
                market_service=market_service,
                events=events,
            ),
        )

        # Refresh events after ordinary policy decisions/fills before a possible
        # force-flat retry or session summary.
        events = await self._session_events(
            repository,
            config,
            session_date=universe.session_date,
            now=now,
        )
        await self._force_flat_open_positions(
            rows=rows,
            config=config,
            repository=repository,
            market_service=market_service,
            events=events,
            now=now,
        )
        events = await self._session_events(
            repository,
            config,
            session_date=universe.session_date,
            now=now,
        )
        await self._mark_incomplete_open_trades(
            config=config,
            repository=repository,
            events=events,
            session_date=universe.session_date,
            now=now,
        )
        events = await self._session_events(
            repository,
            config,
            session_date=universe.session_date,
            now=now,
        )
        await self._session_summary(
            config=config,
            repository=repository,
            events=events,
            session_date=universe.session_date,
            now=now,
        )
        events = await self._session_events(
            repository,
            config,
            session_date=universe.session_date,
            now=now,
        )
        await self._comparison_summary(
            config=config,
            repository=repository,
            events=events,
            session_date=universe.session_date,
            cohort_candidate_count=len(universe.candidates),
            now=now,
        )

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("ai_shadow_monitor_clock_must_be_timezone_aware")
        now = now.astimezone(timezone.utc)
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        for config in configs:
            if not _eligible(config):
                continue
            try:
                await self._run_config(
                    config,
                    repository,
                    market_service,
                    now=now,
                )
            except Exception as exc:
                self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "ai_shadow_monitor_error",
                    strategy_id=config.strategy_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
        self.last_run_at = now
        return self.decision_count

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": ai_shadow_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "minute_llm_call_count": self.minute_llm_call_count,
            "event_llm_call_count": self.event_llm_call_count,
            "decision_count": self.decision_count,
            "fill_count": self.fill_count,
            "trade_count": self.trade_count,
            "data_gap_count": self.data_gap_count,
            "total_token_count": self.total_token_count,
            "execution_authority": False,
        }


def create_trading_ai_shadow_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingAIShadowMonitor):
        return None
    monitor = TradingAIShadowMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=ai_shadow_monitor_enabled)


__all__ = [
    "TradingAIShadowMonitor",
    "ai_shadow_monitor_enabled",
    "create_trading_ai_shadow_monitor_task",
]
