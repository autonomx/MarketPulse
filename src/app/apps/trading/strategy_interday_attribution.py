from __future__ import annotations

"""Map existing strategy evidence into the interday parent attribution funnel."""

from datetime import date
from typing import Iterable

from .strategy_dynamic_discovery import AttributionStage, INTERDAY_TRADING_STRATEGY_ID
from .strategy_repository import StrategyEvent, TradingStrategyRepository
from collections.abc import Mapping


_IGNORE_TYPES = {
    "interday_discovery_event",
    "interday_dynamic_candidate",
    "interday_candidate_attribution",
    "interday_discovery_daily_report",
    "interday_discovery_qualification",
    "interday_discovery_replay",
}


def attribution_stage_for_strategy_event(event: StrategyEvent) -> AttributionStage | None:
    if event.event_type in _IGNORE_TYPES:
        return None
    lowered = event.event_type.lower()
    state = str(event.state or "").lower()
    payload = event.payload

    if event.event_type == "entry_order_submitted":
        return AttributionStage.TRADED
    if event.event_type == "shadow_execution":
        execution = payload.get("execution")
        if isinstance(execution, dict) and execution.get("execution_eligible") is False:
            return None
        return AttributionStage.EXECUTION_ELIGIBLE
    if state == "entry_ready" or "entry_ready" in lowered or "qualified" in lowered:
        return AttributionStage.QUALIFIED
    if event.event_type == "ai_v2_fill" and payload.get("side") == "buy" and state == "filled":
        return AttributionStage.TRADED
    if event.event_type in {"ai_v2_decision", "ai_shadow_decision", "intraday_llm_decision"}:
        decision = payload.get("decision")
        effective = payload.get("effective_action")
        if effective in {"buy", "enter", "long"}:
            return AttributionStage.SIGNALLED
        if isinstance(decision, dict) and decision.get("state") in {"armed", "enter"}:
            return AttributionStage.SIGNALLED
        if isinstance(decision, dict) and decision.get("action") in {"buy", "enter", "long"}:
            return AttributionStage.SIGNALLED
        return AttributionStage.CHARACTERIZED
    if "catalyst" in lowered or "research" in lowered:
        return AttributionStage.RESEARCHED
    if event.event_type == "state" and state in {"entry_armed", "setup_armed", "long_active"}:
        return AttributionStage.SIGNALLED
    return None


def _arm_for_event(event: StrategyEvent, source_strategy_id: str) -> str | None:
    arm = event.payload.get("arm")
    if isinstance(arm, str) and arm:
        return arm
    if source_strategy_id == "stoch-rsi-5min":
        return "stoch-rsi-5min"
    if source_strategy_id == "stoch-rsi-5min-early-single":
        return "stoch-rsi-5min-early-single"
    if source_strategy_id == "gap-pullback-v2-prospective-20260825":
        return "gap-pullback-v2-prospective-20260825"
    if source_strategy_id == INTERDAY_TRADING_STRATEGY_ID:
        policy = event.payload.get("policy") or event.payload.get("strategy_arm")
        return str(policy) if isinstance(policy, str) and policy else None
    return source_strategy_id


def bridge_strategy_events(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
    events_by_strategy: Mapping[str, Iterable[StrategyEvent]],
) -> int:
    """Persist monotonic, event-level attribution through the discovery owner."""

    from .strategy_dynamic_discovery_runtime import _bridge_strategy_events_complete

    return _bridge_strategy_events_complete(
        repository,
        session_date=session_date,
        events_by_strategy=events_by_strategy,
    )


__all__ = ["attribution_stage_for_strategy_event", "bridge_strategy_events"]
