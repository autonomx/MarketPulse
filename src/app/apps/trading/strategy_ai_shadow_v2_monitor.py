from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
import hashlib
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, cast

from app.runtime.features import FeatureContext

from pydantic import BaseModel, ConfigDict

from .research.coordinator import create_trading_research_request, run_trading_research
from .research.repository import TradingResearchRepository, default_research_repository
from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .service import TradingMarketDataService, default_market_data_service
from .strategy_ai_shadow import simulate_ai_shadow_fill
from .strategy_ai_shadow_v2 import (
    AI_SHADOW_V2_VERSION,
    AIShadowV2AlphaDecision,
    AIShadowV2Analyzer,
    AIShadowV2Arm,
    CatalystIntelligenceAnalyzer,
    CatalystIntelligenceSnapshot,
    MarketStructureSnapshot,
    StructuredAlphaTrigger,
    alpha_prompt_snapshot,
    build_market_structure_snapshot,
    derive_catalyst_influence,
    deterministic_risk_geometry as _base_deterministic_risk_geometry,
    evaluate_opportunity_episode,
    evidence_fingerprint,
    trigger_satisfied,
)
from .strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_execution import observe_shadow_execution
from .strategy_shadow_universe import resolve_v2_shadow_archive
from .strategy_session_evidence import _CurrentSessionMarketDataProxy
from .trade_logging import trade_log
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import after_regular_close

logger = logging.getLogger(__name__)

_STATE_KEY = "_omnix_trading_ai_shadow_v2_monitor"
_ARMS: tuple[AIShadowV2Arm, ...] = (
    "morning_control", "morning_catalyst",
    "full_session_control", "full_session_catalyst",
)
_EVENT_TYPES = (
    "ai_v2_catalyst_snapshot", "ai_v2_research_refresh", "ai_v2_decision",
    "ai_v2_fill", "ai_v2_opportunity_episode", "ai_v2_session_summary",
    "ai_v2_catalyst_freeze", "ai_v2_catalyst_lift_summary",
    "ai_v2_alpha_error", "ai_v2_alpha_policy_veto", "ai_v2_decision_outcome",
    "ai_v2_arm_unavailable",
)
_PREMARKET_START_ET = time(6, 0)
_REGULAR_SESSION_START_ET = time(9, 30)
_FULL_SESSION_LAST_ENTRY_ET = time(15, 30)
AI_SHADOW_V2_MAX_STRUCTURAL_RISK_PCT = Decimal("8")


def deterministic_risk_geometry(
    decision,
    *,
    entry_reference: Decimal,
    estimated_cost_bps: Decimal,
    minimum_net_r: Decimal,
):
    geometry = _base_deterministic_risk_geometry(
        decision,
        entry_reference=entry_reference,
        estimated_cost_bps=estimated_cost_bps,
        minimum_net_r=minimum_net_r,
    )
    if geometry.risk_per_share is None or entry_reference <= 0:
        return geometry
    risk_pct = geometry.risk_per_share / entry_reference * Decimal("100")
    if risk_pct <= AI_SHADOW_V2_MAX_STRUCTURAL_RISK_PCT:
        return geometry
    return geometry.model_copy(
        update={
            "valid": False,
            "reason": "maximum_structural_risk_pct_exceeded",
        }
    )


class V2PositionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm: AIShadowV2Arm
    instrument_id: str
    units: Decimal = Decimal("0")
    average_cost: Decimal | None = None
    trade_id: str | None = None
    entry_time: datetime | None = None
    realized_pnl: Decimal = Decimal("0")

    @property
    def is_long(self) -> bool:
        return self.units > 0


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ai_shadow_v2_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_AI_SHADOW_V2_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_AI_SHADOW_V2_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        return max(5.0, float(_env_str("OMNIX_TRADING_AI_SHADOW_V2_INTERVAL_SECONDS", "15")))
    except ValueError:
        return 15.0


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def _eligible(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.strategy_id == MANAGED_FINVIZ_SHADOW_STRATEGY_ID
        and config.enabled and config.mode == "shadow"
        and config.active_universe_id is None
        and config.config.strategy_version == "2.0.0"
        and config.config.universe_discovery_source == "finviz"
        and config.config.universe_discovery_count == 5
    )


def _is_catalyst(arm: AIShadowV2Arm) -> bool:
    return arm.endswith("_catalyst")


def _entry_end(config: TradingStrategyConfigDocument, arm: AIShadowV2Arm) -> time:
    return config.risk.last_entry_et if arm.startswith("morning_") else min(
        _FULL_SESSION_LAST_ENTRY_ET, config.risk.force_flat_et
    )


def _position(events: list[StrategyEvent], arm: AIShadowV2Arm, instrument_id: str) -> V2PositionState:
    values = [
        e for e in events
        if e.event_type == "ai_v2_fill" and e.instrument_id == instrument_id
        and e.payload.get("arm") == arm and isinstance(e.payload.get("position_after"), dict)
    ]
    if not values:
        return V2PositionState(arm=arm, instrument_id=instrument_id)
    try:
        return V2PositionState.model_validate(
            max(values, key=lambda e: (e.observed_at, e.event_id)).payload["position_after"]
        )
    except Exception:
        return V2PositionState(arm=arm, instrument_id=instrument_id)


def _previous_decision(events: list[StrategyEvent], arm: AIShadowV2Arm, instrument_id: str) -> StrategyEvent | None:
    values = [
        e for e in events
        if e.event_type == "ai_v2_decision" and e.instrument_id == instrument_id
        and e.payload.get("arm") == arm
    ]
    return max(values, key=lambda e: (e.observed_at, e.event_id)) if values else None


def _decision_exists(events: list[StrategyEvent], arm: AIShadowV2Arm, instrument_id: str, at: datetime) -> bool:
    at = at.astimezone(timezone.utc)
    return any(
        e.event_type == "ai_v2_decision" and e.instrument_id == instrument_id
        and e.payload.get("arm") == arm and e.observed_at.astimezone(timezone.utc) == at
        for e in events
    )


def _started_trade_exists(events: list[StrategyEvent], arm: AIShadowV2Arm, instrument_id: str) -> bool:
    return any(
        e.event_type == "ai_v2_fill" and e.instrument_id == instrument_id
        and e.payload.get("arm") == arm and e.payload.get("side") == "buy" and e.state == "filled"
        for e in events
    )


def _started_trade_count(events: list[StrategyEvent], arm: AIShadowV2Arm) -> int:
    return len({
        str(e.payload.get("trade_id")) for e in events
        if e.event_type == "ai_v2_fill" and e.payload.get("arm") == arm
        and e.payload.get("side") == "buy" and e.state == "filled" and e.payload.get("trade_id")
    })


def _active_position_count(events: list[StrategyEvent], arm: AIShadowV2Arm) -> int:
    ids = {
        e.instrument_id for e in events
        if e.event_type == "ai_v2_fill" and e.payload.get("arm") == arm
        and e.instrument_id != "__universe__"
    }
    return sum(_position(events, arm, instrument_id).is_long for instrument_id in ids)


def _latest_snapshot(events: list[StrategyEvent], instrument_id: str) -> CatalystIntelligenceSnapshot | None:
    values = [
        e for e in events
        if e.event_type == "ai_v2_catalyst_snapshot" and e.instrument_id == instrument_id
        and isinstance(e.payload.get("snapshot"), dict)
    ]
    if not values:
        return None
    try:
        return CatalystIntelligenceSnapshot.model_validate(
            max(values, key=lambda e: (e.observed_at, e.event_id)).payload["snapshot"]
        )
    except Exception:
        logger.debug("suppressed error in %s", "_latest_snapshot", exc_info=True)
        return None


def _latest_refresh(events: list[StrategyEvent], instrument_id: str) -> StrategyEvent | None:
    values = [e for e in events if e.event_type == "ai_v2_research_refresh" and e.instrument_id == instrument_id]
    return max(values, key=lambda e: (e.observed_at, e.event_id)) if values else None


def _research_selection_is_due(
    *,
    candidate,
    last_refresh: StrategyEvent | None,
    now: datetime,
) -> bool:
    """Allow one research harvest when a candidate enters today's universe.

    Frozen morning candidates are researched during premarket. A candidate
    first observed after the regular session opens is researched once on its
    intraday selection. The durable refresh event makes this a per-symbol,
    per-session decision instead of a recurring timer.
    """

    if last_refresh is not None:
        return False
    if now.tzinfo is None:
        raise ValueError("research selection clock must be timezone-aware")

    now_et = now.astimezone(_ET)
    local_time = now_et.time()
    if not _PREMARKET_START_ET <= local_time <= _FULL_SESSION_LAST_ENTRY_ET:
        return False
    if local_time < _REGULAR_SESSION_START_ET:
        return True

    observed_at = getattr(candidate, "observed_at", None)
    if observed_at is None or observed_at.tzinfo is None:
        return False
    observed_et = observed_at.astimezone(_ET)
    return (
        observed_et.date() == now_et.date()
        and observed_et.time() >= _REGULAR_SESSION_START_ET
        and observed_at <= now
    )


def _historical_episodes(repository: TradingStrategyRepository, strategy_id: str) -> list[dict[str, Any]]:
    from .strategy_ai_shadow_v2_roadmap_policy import _historical_episodes_policy

    return _historical_episodes_policy(repository, strategy_id)


def _persistence_calibration(episodes: list[dict[str, Any]], persistence: str) -> tuple[Decimal | None, int]:
    from .strategy_ai_shadow_v2_roadmap_policy import _persistence_calibration_policy

    return _persistence_calibration_policy(episodes, persistence)


def _setup_calibration(episodes: list[dict[str, Any]], persistence: str) -> dict[str, dict[str, Any]]:
    from .strategy_ai_shadow_v2_roadmap_policy import _setup_calibration_policy

    return _setup_calibration_policy(episodes, persistence)


def _apply_fill(state: V2PositionState, side: str, units: Decimal, price: Decimal, at: datetime, trade_id: str) -> V2PositionState:
    if side == "buy":
        old_notional = (state.average_cost or Decimal("0")) * state.units
        new_units = state.units + units
        return state.model_copy(update={
            "units": new_units,
            "average_cost": (old_notional + price * units) / new_units,
            "trade_id": state.trade_id or trade_id,
            "entry_time": state.entry_time or at,
        })
    sold = min(units, state.units)
    realized = (price - state.average_cost) * sold if state.average_cost is not None else Decimal("0")
    remaining = state.units - sold
    return state.model_copy(update={
        "units": remaining,
        "average_cost": state.average_cost if remaining > 0 else None,
        "realized_pnl": state.realized_pnl + realized,
    })


class TradingAIShadowV2Monitor(ScheduledTradingMonitor):
    error_event = "ai_shadow_v2_monitor_error"

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        research_repository_factory: Callable[[], TradingResearchRepository] = default_research_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        alpha_analyzer_factory: Callable[[], AIShadowV2Analyzer] = AIShadowV2Analyzer,
        catalyst_analyzer_factory: Callable[[], CatalystIntelligenceAnalyzer] = CatalystIntelligenceAnalyzer,
        now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.research_repository_factory = research_repository_factory
        self.market_service_factory = market_service_factory
        self.alpha_analyzer_factory = alpha_analyzer_factory
        self.catalyst_analyzer_factory = catalyst_analyzer_factory
        self.now_factory = now_factory
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.alpha_call_count = 0
        self.catalyst_refresh_count = 0
        self.decision_count = 0
        self.fill_count = 0
        self.episode_count = 0

    async def _append(
        self, repository: TradingStrategyRepository, config: TradingStrategyConfigDocument,
        *, instrument_id: str, event_type: str, state: str, reason_code: str,
        observed_at: datetime, payload: dict[str, Any], identity: tuple[object, ...],
    ) -> bool:
        idem = _key(config.strategy_id, AI_SHADOW_V2_VERSION, instrument_id, event_type, *identity)
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id, event_id=idem[:32],
                run_id=f"ai-shadow-v2-{observed_at.astimezone(timezone.utc).strftime('%Y%m%d')}",
                instrument_id=instrument_id, event_type=event_type, state=state,
                reason_code=reason_code, observed_at=observed_at, idempotency_key=idem, payload=payload,
            ),
        )

    async def _events(
        self, repository: TradingStrategyRepository, config: TradingStrategyConfigDocument,
        *, session_date, now: datetime,
    ) -> list[StrategyEvent]:
        start_et = datetime(session_date.year, session_date.month, session_date.day, tzinfo=_ET)
        end = min(now + timedelta(seconds=1), start_et + timedelta(days=1))
        if hasattr(repository, "events_by_types_between"):
            return await asyncio.to_thread(
                repository.events_by_types_between, config.strategy_id,
                event_types=_EVENT_TYPES, start_time=start_et.astimezone(timezone.utc),
                end_time=end.astimezone(timezone.utc), limit=30_000,
            )
        if not hasattr(repository, "recent_events"):
            return []
        recent = await asyncio.to_thread(repository.recent_events, config.strategy_id, 30_000)
        return [
            e for e in recent if e.event_type in _EVENT_TYPES
            and start_et.astimezone(timezone.utc) <= e.observed_at.astimezone(timezone.utc) < end.astimezone(timezone.utc)
        ]

    async def _refresh_catalyst_core(
        self, *, candidate, config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        research_repository: TradingResearchRepository,
        events: list[StrategyEvent], now: datetime,
        history: list[dict[str, Any]],
    ) -> CatalystIntelligenceSnapshot:
        current = _latest_snapshot(events, candidate.instrument_id)
        last_refresh = _latest_refresh(events, candidate.instrument_id)
        comprehensive = current is None
        due = _research_selection_is_due(candidate=candidate, last_refresh=last_refresh, now=now)
        if due:
            request = create_trading_research_request(
                instrument_id=candidate.instrument_id, strategy_id=config.strategy_id,
                decision_context_at=now, deadline_seconds=45 if comprehensive else 25,
                max_steps=8 if comprehensive else 4, max_queries=5 if comprehensive else 2,
                max_sources=20 if comprehensive else 10, max_extracts=8 if comprehensive else 4,
            )
            state, detail = "complete", ""
            try:
                await asyncio.to_thread(
                    run_trading_research, request, repository=research_repository, run_shadow_ai=True
                )
            except Exception as exc:
                state, detail = "partial", f"{type(exc).__name__}: {exc}"
            self.catalyst_refresh_count += 1
            await self._append(
                strategy_repository, config, instrument_id=candidate.instrument_id,
                event_type="ai_v2_research_refresh", state=state, reason_code="AI_V2_RESEARCH_REFRESH",
                observed_at=now, payload={
                    "mode": "comprehensive" if comprehensive else "incremental",
                    "detail": detail, "research_only": True, "execution_authority": False,
                }, identity=(candidate.instrument_id, now.astimezone(_ET).date().isoformat(), "research"),
            )

        report = await asyncio.to_thread(research_repository.latest_report_as_of, candidate.instrument_id, now)
        evidence = await asyncio.to_thread(research_repository.list_evidence_as_of, candidate.instrument_id, now, 200)
        fingerprint = evidence_fingerprint(evidence)
        if current is not None and current.evidence_fingerprint == fingerprint:
            return current

        snapshot = await asyncio.to_thread(
            self.catalyst_analyzer_factory().assess,
            instrument_id=candidate.instrument_id, as_of=now, report=report, evidence=evidence,
            morning_context={
                "previous_close": str(candidate.previous_close),
                "premarket_price": str(candidate.premarket_price),
                "gap_pct": str(candidate.gap_pct),
                "market_cap": str(candidate.market_cap) if candidate.market_cap is not None else None,
                "float_shares": str(candidate.float_shares) if candidate.float_shares is not None else None,
                "premarket_volume": str(candidate.premarket_volume),
                "premarket_dollar_volume": str(candidate.premarket_dollar_volume),
            },
        )
        rate, sample = _persistence_calibration(history, snapshot.intraday_persistence_class)
        if rate is not None or sample:
            snapshot = snapshot.model_copy(update={
                "influence": derive_catalyst_influence(
                    persistence_class=snapshot.intraday_persistence_class,
                    primary_source_verified=snapshot.primary_source_verified,
                    supply_pressure=snapshot.supply_pressure,
                    promotional_risk=snapshot.promotional_risk,
                    gap_already_prices_in_news=snapshot.gap_already_prices_in_news,
                    empirical_persistence_rate=rate, empirical_sample_size=sample,
                )
            })
        await self._append(
            strategy_repository, config, instrument_id=candidate.instrument_id,
            event_type="ai_v2_catalyst_snapshot", state=snapshot.intraday_persistence_class,
            reason_code="AI_V2_CATALYST_INTELLIGENCE_SNAPSHOT", observed_at=now,
            payload={"snapshot": snapshot.model_dump(mode="json"), "research_only": True, "execution_authority": False},
            identity=(candidate.instrument_id, snapshot.evidence_fingerprint),
        )
        return snapshot

    async def _refresh_catalyst(
        self,
        *,
        candidate,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        research_repository: TradingResearchRepository,
        events: list[StrategyEvent],
        now: datetime,
        history: list[dict[str, Any]],
    ) -> CatalystIntelligenceSnapshot:
        from .strategy_ai_shadow_v2_roadmap_policy import _refresh_catalyst_policy

        return await _refresh_catalyst_policy(
            self,
            candidate=candidate,
            config=config,
            strategy_repository=strategy_repository,
            research_repository=research_repository,
            events=events,
            now=now,
            history=history,
            original=self._refresh_catalyst_core,
        )

    async def _microstructure(self, market_service: TradingMarketDataService, candidate) -> dict[str, Any] | None:
        try:
            obs = await asyncio.to_thread(
                market_service.execution_observation, candidate.instrument_id, candidate.binding_id
            )
        except Exception:
            logger.debug("suppressed error in %s", "TradingAIShadowV2Monitor._microstructure", exc_info=True)
            return None
        bid, ask = getattr(obs, "bid", None), getattr(obs, "ask", None)
        if bid is None or ask is None:
            return None
        spread = getattr(obs, "spread_bps", None)
        return {"bid": str(bid), "ask": str(ask), "spread_bps": str(spread) if spread is not None else None}

    async def _apply_decision(
        self, *, arm: AIShadowV2Arm, decision: AIShadowV2AlphaDecision,
        row: dict[str, Any], config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository, events: list[StrategyEvent],
        trigger_reasons: tuple[str, ...],
    ) -> None:
        at, structure = row["observed_at"], row["structure"]
        assert isinstance(at, datetime) and isinstance(structure, MarketStructureSnapshot)
        position = _position(events, arm, decision.instrument_id)
        state, policy_reasons = decision.state, []
        if position.is_long and state in {"avoid", "watch", "armed", "enter"}:
            state, policy_reasons = "manage", ["AI_V2_LONG_STATE_NORMALIZED"]
        if not position.is_long and state in {"manage", "exit"}:
            state, policy_reasons = "avoid", ["AI_V2_FLAT_STATE_NORMALIZED"]

        geometry = deterministic_risk_geometry(
            decision, entry_reference=structure.current_price,
            estimated_cost_bps=config.risk.max_spread_bps + Decimal("10"),
            minimum_net_r=config.config.reward_multiple,
        )
        if state == "enter" and not geometry.valid:
            state = "armed" if decision.trigger is not None else "watch"
            policy_reasons.append(f"AI_V2_GEOMETRY_VETO:{geometry.reason}")

        if state == "enter":
            et = at.astimezone(_ET).time()
            if config.risk.kill_switch or et > _entry_end(config, arm):
                state, policy_reasons = "watch", policy_reasons + ["AI_V2_ENTRY_WINDOW_OR_KILL_SWITCH"]
            elif config.risk.one_trade_per_symbol_per_day and _started_trade_exists(events, arm, decision.instrument_id):
                state, policy_reasons = "watch", policy_reasons + ["AI_V2_ONE_TRADE_PER_SYMBOL"]
            elif _started_trade_count(events, arm) >= config.risk.max_trades_per_day:
                state, policy_reasons = "watch", policy_reasons + ["AI_V2_MAX_TRADES_PER_DAY"]
            elif _active_position_count(events, arm) >= config.risk.max_positions:
                state, policy_reasons = "watch", policy_reasons + ["AI_V2_MAX_POSITIONS"]

        payload = {
            "version": AI_SHADOW_V2_VERSION, "arm": arm, "alpha_state": decision.state,
            "effective_state": state, "decision": decision.model_dump(mode="json"),
            "geometry": geometry.model_dump(mode="json"), "feature_snapshot": row["feature_by_arm"][arm],
            "trigger_reasons": list(trigger_reasons), "policy_reasons": policy_reasons,
            "position_before": position.model_dump(mode="json"),
            "research_only": True, "execution_authority": False,
        }
        if await self._append(
            repository, config, instrument_id=decision.instrument_id,
            event_type="ai_v2_decision", state=state, reason_code="AI_V2_ALPHA_DECISION",
            observed_at=at, payload=payload, identity=(arm, at.astimezone(timezone.utc).isoformat()),
        ):
            self.decision_count += 1

        if state not in {"enter", "exit"}:
            return
        side, units = ("buy", Decimal("1")) if state == "enter" else ("sell", position.units)
        if units <= 0:
            return
        execution = None
        detail = ""
        try:
            execution = (await asyncio.to_thread(
                observe_shadow_execution, row["market_service"],
                instrument_id=decision.instrument_id, binding_id=row["candidate"].binding_id,
            )).execution
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"

        spread = Decimal(str(execution.get("spread_bps"))) if execution and execution.get("spread_bps") is not None else None
        allowed = bool(
            execution and execution.get("execution_eligible") is True
            and execution.get("halted") is not True and spread is not None
            and spread <= config.risk.max_spread_bps
        )
        if not allowed:
            await self._append(
                repository, config, instrument_id=decision.instrument_id,
                event_type="ai_v2_fill", state="vetoed",
                reason_code="AI_V2_EXECUTION_VETO" if execution else "AI_V2_EXECUTION_EVIDENCE_UNAVAILABLE",
                observed_at=at, payload={
                    "arm": arm, "side": side, "detail": detail, "execution": execution,
                    "position_before": position.model_dump(mode="json"),
                    "position_after": position.model_dump(mode="json"),
                    "research_only": True, "execution_authority": False,
                }, identity=(arm, at.isoformat(), "fill"),
            )
            return

        simulation = simulate_ai_shadow_fill(
            cast(dict[str, Any], execution), side=cast(Any, side), instrument_id=decision.instrument_id,
            binding_id=row["candidate"].binding_id, decision_at=at, requested_units=units,
            reference_price=structure.current_price, allow_degraded_price_only=False,
        )
        trade_id = position.trade_id or _key(arm, decision.instrument_id, at, "trade")[:24]
        after = position
        if simulation.should_fill and simulation.fill_price is not None:
            after = _apply_fill(
                position, side, simulation.filled_units, simulation.fill_price,
                simulation.source_time or at, trade_id,
            )
        fill_payload = {
            "arm": arm, "trade_id": trade_id, "side": side,
            "simulation": simulation.model_dump(mode="json"),
            "position_before": position.model_dump(mode="json"),
            "position_after": after.model_dump(mode="json"),
            "research_only": True, "execution_authority": False,
        }
        persisted = await self._append(
            repository, config, instrument_id=decision.instrument_id,
            event_type="ai_v2_fill", state="filled" if simulation.should_fill else "unfilled",
            reason_code="AI_V2_FILL_SIMULATED" if simulation.should_fill else "AI_V2_FILL_NOT_EXECUTABLE",
            observed_at=simulation.source_time or at, payload=fill_payload,
            identity=(arm, at.isoformat(), "fill"),
        )
        if persisted and simulation.should_fill:
            self.fill_count += 1
            events.append(StrategyEvent(
                strategy_id=config.strategy_id, event_id=_key(arm, decision.instrument_id, at, "local")[:32],
                run_id="ai-shadow-v2-local", instrument_id=decision.instrument_id,
                event_type="ai_v2_fill", state="filled", reason_code="AI_V2_FILL_SIMULATED",
                observed_at=simulation.source_time or at,
                idempotency_key=_key(arm, decision.instrument_id, at, "local"), payload=fill_payload,
            ))

    async def _run_arm_core(
        self, *, arm: AIShadowV2Arm, rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument, repository: TradingStrategyRepository,
        events: list[StrategyEvent],
    ) -> None:
        due, reasons_by_id = [], {}
        for row in rows:
            candidate, at = row["candidate"], row["observed_at"]
            assert isinstance(at, datetime)
            position = _position(events, arm, candidate.instrument_id)
            if not position.is_long and at.astimezone(_ET).time() > _entry_end(config, arm):
                continue
            if at.astimezone(_ET).time() >= config.risk.force_flat_et:
                continue
            if _decision_exists(events, arm, candidate.instrument_id, at):
                continue
            previous = _previous_decision(events, arm, candidate.instrument_id)
            previous_decision = previous.payload.get("decision") if previous and isinstance(previous.payload.get("decision"), dict) else None
            reasons: list[str] = ["initial"] if previous is None else []
            if previous_decision and previous_decision.get("state") == "armed":
                try:
                    trigger = StructuredAlphaTrigger.model_validate(previous_decision.get("trigger"))
                    prior = None
                    # An armed previous decision came from a previous event.
                    assert previous is not None
                    feature = previous.payload.get("feature_snapshot")
                    if isinstance(feature, dict) and isinstance(feature.get("market_structure"), dict):
                        prior = MarketStructureSnapshot.model_validate(feature["market_structure"])
                    if trigger_satisfied(trigger, structure=row["structure"], previous_structure=prior):
                        reasons.append("armed_trigger_satisfied")
                except Exception:
                    logger.debug("suppressed error in %s", "TradingAIShadowV2Monitor._run_arm_core", exc_info=True)
            if previous is not None and at - previous.observed_at.astimezone(at.tzinfo) >= timedelta(minutes=5):
                reasons.append("five_minute_heartbeat")
            if not reasons:
                continue
            due.append({
                "row": row,
                "analyzer": {
                    "instrument_id": candidate.instrument_id, "observed_at": at.isoformat(),
                    "feature_snapshot": row["feature_by_arm"][arm],
                    "previous_alpha_context": {
                        "state": previous_decision.get("state") if previous_decision else None,
                        "setup_family": previous_decision.get("setup_family") if previous_decision else None,
                        "thesis": previous_decision.get("thesis") if previous_decision else None,
                        "trigger": previous_decision.get("trigger") if previous_decision else None,
                    },
                    "trigger_reasons": reasons,
                },
            })
            reasons_by_id[candidate.instrument_id] = tuple(reasons)
        if not due:
            return
        self.alpha_call_count += 1
        decisions = await asyncio.to_thread(
            self.alpha_analyzer_factory().assess, arm=arm, rows=[item["analyzer"] for item in due]
        )
        by_id = {item["row"]["candidate"].instrument_id: item["row"] for item in due}
        for decision in decisions:
            if decision.instrument_id in by_id:
                await self._apply_decision(
                    arm=arm, decision=decision, row=by_id[decision.instrument_id], config=config,
                    repository=repository, events=events,
                    trigger_reasons=reasons_by_id.get(decision.instrument_id, ()),
                )

    async def _run_arm(
        self,
        *,
        arm: AIShadowV2Arm,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
    ) -> None:
        from . import strategy_ai_shadow_provider as reliability

        now = reliability.time.monotonic()
        circuit = reliability._CIRCUIT
        if circuit.is_open(now):
            self.last_error = (
                "ai_shadow_v2_provider_circuit_open:"
                f"retry_after_seconds={circuit.retry_after(now)};"
                f"failure_count={circuit.failure_count}"
            )
            return None
        from .strategy_ai_shadow_v2_roadmap_policy import _run_arm_policy

        await _run_arm_policy(
            self,
            arm=arm,
            rows=rows,
            config=config,
            repository=repository,
            events=events,
        )

    async def _force_flat(
        self, *, rows: list[dict[str, Any]], config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository, events: list[StrategyEvent], now: datetime,
    ) -> None:
        if now.astimezone(_ET).time() < config.risk.force_flat_et:
            return
        for arm in _ARMS:
            for row in rows:
                candidate = row["candidate"]
                if not _position(events, arm, candidate.instrument_id).is_long:
                    continue
                decision = AIShadowV2AlphaDecision(
                    instrument_id=candidate.instrument_id, setup_family="unresolved", state="exit",
                    quality_score=100, extension_risk="low", thesis="Deterministic force-flat boundary.",
                )
                forced = dict(row)
                forced["observed_at"] = now
                await self._apply_decision(
                    arm=arm, decision=decision, row=forced, config=config, repository=repository,
                    events=events, trigger_reasons=("force_flat",),
                )

    async def _label_episodes_core(
        self, *, rows: list[dict[str, Any]], config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository, events: list[StrategyEvent], now: datetime,
    ) -> None:
        if not after_regular_close(now):
            return
        existing = {
            str(e.payload.get("episode_id")) for e in events if e.event_type == "ai_v2_opportunity_episode"
        }
        rows_by_id = {row["candidate"].instrument_id: row for row in rows}
        for arm in _ARMS:
            for instrument_id, row in rows_by_id.items():
                decisions = sorted([
                    e for e in events if e.event_type == "ai_v2_decision"
                    and e.instrument_id == instrument_id and e.payload.get("arm") == arm
                ], key=lambda e: e.observed_at)
                groups, active = [], []
                for event in decisions:
                    state = str(event.payload.get("effective_state") or "")
                    if state in {"watch", "armed", "enter", "manage"}:
                        active.append(event)
                    elif active:
                        groups.append(active); active = []
                if active:
                    groups.append(active)
                for index, group in enumerate(groups):
                    first = group[0]
                    decision = first.payload.get("decision")
                    feature = first.payload.get("feature_snapshot")
                    if not isinstance(decision, dict) or not isinstance(feature, dict):
                        continue
                    structure_payload = feature.get("market_structure")
                    if not isinstance(structure_payload, dict):
                        continue
                    structure = MarketStructureSnapshot.model_validate(structure_payload)
                    episode_id = _key(arm, instrument_id, first.observed_at.isoformat(), index)[:28]
                    if episode_id in existing:
                        continue
                    persistence = None
                    if _is_catalyst(arm):
                        catalyst_payload = feature.get("catalyst_intelligence")
                        if isinstance(catalyst_payload, dict):
                            persistence = catalyst_payload.get("intraday_persistence_class")
                    try:
                        outcome = evaluate_opportunity_episode(
                            arm=arm, instrument_id=instrument_id, episode_id=episode_id,
                            setup_family=cast(Any, str(decision.get("setup_family") or "unresolved")),
                            started_at=first.observed_at, ended_at=max(group[-1].observed_at, row["bars"][-1].end_time),
                            entry_price=structure.current_price,
                            invalidation_price=Decimal(str(decision["invalidation_price"])) if decision.get("invalidation_price") is not None else None,
                            target_1=Decimal(str(decision["target_1"])) if decision.get("target_1") is not None else None,
                            bars=row["bars"], entered=any(e.payload.get("effective_state") == "enter" for e in group),
                            catalyst_persistence_class=persistence,
                        )
                    except Exception:
                        logger.debug("suppressed error in %s", "TradingAIShadowV2Monitor._label_episodes_core", exc_info=True)
                        continue
                    if await self._append(
                        repository, config, instrument_id=instrument_id,
                        event_type="ai_v2_opportunity_episode",
                        state="positive" if outcome.positive_opportunity else "negative",
                        reason_code="AI_V2_OPPORTUNITY_EPISODE", observed_at=now,
                        payload={"arm": arm, "episode_id": episode_id, "outcome": outcome.model_dump(mode="json"),
                                 "research_only": True, "execution_authority": False},
                        identity=(arm, episode_id),
                    ):
                        self.episode_count += 1

    async def _label_episodes(
        self,
        *,
        rows: list[dict[str, Any]],
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        now: datetime,
    ) -> None:
        from .strategy_ai_shadow_v2_roadmap_policy import _label_episodes_policy

        await _label_episodes_policy(
            self,
            rows=rows,
            config=config,
            repository=repository,
            events=events,
            now=now,
        )

    async def _summary_core(
        self, *, config: TradingStrategyConfigDocument, repository: TradingStrategyRepository,
        events: list[StrategyEvent], session_date, now: datetime,
    ) -> None:
        if not after_regular_close(now):
            return

        def arm_metrics(arm: AIShadowV2Arm) -> dict[str, Any]:
            episodes = [
                e.payload["outcome"] for e in events
                if e.event_type == "ai_v2_opportunity_episode" and e.payload.get("arm") == arm
                and isinstance(e.payload.get("outcome"), dict)
            ]
            positive = [row for row in episodes if row.get("positive_opportunity") is True]
            entered = [row for row in episodes if row.get("entered") is True]
            captured = [row for row in positive if row.get("entered") is True]
            false_entries = [row for row in entered if row.get("positive_opportunity") is False]
            missed = [row for row in positive if row.get("entered") is not True]
            missed_r = [Decimal(str(row["peak_r"])) for row in missed if row.get("peak_r") is not None]
            return {
                "opportunity_episode_count": len(episodes),
                "positive_opportunity_count": len(positive),
                "entered_episode_count": len(entered),
                "good_entry_recall": str(Decimal(len(captured)) / Decimal(len(positive))) if positive else None,
                "entry_precision": str(Decimal(len(entered) - len(false_entries)) / Decimal(len(entered))) if entered else None,
                "false_entry_count": len(false_entries),
                "missed_positive_count": len(missed),
                "missed_positive_mean_peak_r": str(sum(missed_r, Decimal("0")) / Decimal(len(missed_r))) if missed_r else None,
            }

        payload = {
            "version": AI_SHADOW_V2_VERSION, "session_date": session_date.isoformat(),
            "arms": {arm: arm_metrics(arm) for arm in _ARMS},
            "comparison": {
                "catalyst_blind_control_retained": True,
                "same_market_observations_within_each_window": True,
                "risk_controls_shared": True, "execution_controls_shared": True,
                "trade_count_not_primary_kpi": True,
            },
            "research_only": True, "execution_authority": False,
        }
        await self._append(
            repository, config, instrument_id="__universe__", event_type="ai_v2_session_summary",
            state="complete", reason_code="AI_V2_SESSION_SUMMARY", observed_at=now, payload=payload,
            identity=(session_date.isoformat(), _key(payload["arms"])),
        )

    async def _summary(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        session_date,
        now: datetime,
    ) -> None:
        from .strategy_ai_shadow_v2_roadmap_policy import _summary_policy

        await _summary_policy(
            self,
            config=config,
            repository=repository,
            events=events,
            session_date=session_date,
            now=now,
        )

    async def _run_config(
        self, config: TradingStrategyConfigDocument, repository: TradingStrategyRepository,
        research_repository: TradingResearchRepository, market_service: TradingMarketDataService,
        *, now: datetime,
    ) -> None:
        market_service = cast(TradingMarketDataService, _CurrentSessionMarketDataProxy(
            market_service,
            session_date=now.astimezone(_ET).date(),
            observed_at=now,
        ))
        universe = await asyncio.to_thread(resolve_v2_shadow_archive, config, repository, now=now)
        if universe is None:
            return
        events = await self._events(repository, config, session_date=universe.session_date, now=now)
        history = await asyncio.to_thread(_historical_episodes, repository, config.strategy_id)

        async def refresh(candidate):
            try:
                value = await self._refresh_catalyst(
                    candidate=candidate, config=config, strategy_repository=repository,
                    research_repository=research_repository, events=events, now=now, history=history,
                )
                return candidate.instrument_id, value, None
            except Exception as exc:
                return candidate.instrument_id, None, exc

        catalyst_by_id: dict[str, CatalystIntelligenceSnapshot] = {}
        for instrument_id, value, error in await asyncio.gather(*(refresh(c) for c in universe.candidates)):
            if value is not None:
                catalyst_by_id[instrument_id] = value
            if error is not None:
                self.last_error = f"{config.strategy_id}/{instrument_id}/catalyst: {type(error).__name__}: {error}"

        events = await self._events(repository, config, session_date=universe.session_date, now=now)
        rows: list[dict[str, Any]] = []
        for candidate in universe.candidates:
            try:
                response = await asyncio.to_thread(
                    market_service.bars, candidate.instrument_id, "1m", 500, candidate.binding_id
                )
                bars = sorted(
                    [bar for bar in response.bars if bar.is_final and bar.end_time <= now],
                    key=lambda bar: bar.end_time,
                )
                structure = build_market_structure_snapshot(bars)
                if structure.observed_at.astimezone(_ET).time() < config.risk.entry_start_et:
                    continue
                micro = await self._microstructure(market_service, candidate)
                morning = {
                    "previous_close": str(candidate.previous_close),
                    "premarket_price": str(candidate.premarket_price),
                    "gap_pct": str(candidate.gap_pct),
                    "premarket_volume": str(candidate.premarket_volume),
                    "premarket_dollar_volume": str(candidate.premarket_dollar_volume),
                    "tod_rvol": str(candidate.tod_rvol) if candidate.tod_rvol is not None else None,
                    "market_cap": str(candidate.market_cap) if candidate.market_cap is not None else None,
                    "float_shares": str(candidate.float_shares) if candidate.float_shares is not None else None,
                    "discovery_rank": candidate.discovery_rank,
                }
                catalyst = catalyst_by_id.get(candidate.instrument_id) or _latest_snapshot(events, candidate.instrument_id)
                feature_by_arm: dict[AIShadowV2Arm, dict[str, Any]] = {}
                for arm in _ARMS:
                    feature = alpha_prompt_snapshot(
                        instrument_id=candidate.instrument_id, structure=structure, morning=morning,
                        catalyst=catalyst, include_catalyst=_is_catalyst(arm), trusted_microstructure=micro,
                    )
                    if _is_catalyst(arm) and catalyst is not None:
                        feature["empirical_setup_calibration"] = _setup_calibration(
                            history, catalyst.intraday_persistence_class
                        )
                    feature_by_arm[arm] = feature
                rows.append({
                    "candidate": candidate, "bars": bars, "structure": structure,
                    "observed_at": structure.observed_at, "market_service": market_service,
                    "catalyst": catalyst, "feature_by_arm": feature_by_arm,
                })
            except Exception as exc:
                self.last_error = f"{config.strategy_id}/{candidate.instrument_id}/market: {type(exc).__name__}: {exc}"
        if not rows:
            return

        await asyncio.gather(*(
            self._run_arm(arm=arm, rows=rows, config=config, repository=repository, events=events)
            for arm in _ARMS
        ))
        events = await self._events(repository, config, session_date=universe.session_date, now=now)
        await self._force_flat(rows=rows, config=config, repository=repository, events=events, now=now)
        events = await self._events(repository, config, session_date=universe.session_date, now=now)
        await self._label_episodes(rows=rows, config=config, repository=repository, events=events, now=now)
        events = await self._events(repository, config, session_date=universe.session_date, now=now)
        await self._summary(config=config, repository=repository, events=events, session_date=universe.session_date, now=now)

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("ai_shadow_v2_monitor_clock_must_be_timezone_aware")
        now = now.astimezone(timezone.utc)
        repository = self.strategy_repository_factory()
        research_repository = self.research_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        for config in configs:
            if not _eligible(config):
                continue
            try:
                await self._run_config(config, repository, research_repository, market_service, now=now)
            except Exception as exc:
                self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading", "ai_shadow_v2_monitor_error", strategy_id=config.strategy_id,
                    error_type=type(exc).__name__, detail=str(exc), execution_authority=False,
                )
        self.last_run_at = now
        return self.decision_count

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": ai_shadow_v2_monitor_enabled(), "running": self.scheduled,
            "version": AI_SHADOW_V2_VERSION, "arms": list(_ARMS),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error, "alpha_call_count": self.alpha_call_count,
            "catalyst_refresh_count": self.catalyst_refresh_count,
            "decision_count": self.decision_count, "fill_count": self.fill_count,
            "episode_count": self.episode_count, "execution_authority": False,
        }


def create_trading_ai_shadow_v2_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingAIShadowV2Monitor):
        return None
    monitor = TradingAIShadowV2Monitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=ai_shadow_v2_monitor_enabled)


__all__ = [
    "TradingAIShadowV2Monitor", "V2PositionState",
    "ai_shadow_v2_monitor_enabled", "create_trading_ai_shadow_v2_monitor_task",
]
