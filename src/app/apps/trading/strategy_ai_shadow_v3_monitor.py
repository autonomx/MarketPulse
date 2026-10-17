"""Canonical AI Shadow v3 runtime.

Unlike v2, this monitor is composed explicitly and is never modified by installer
monkey patches. It is SHADOW-only and has no paper/order repository dependency.
"""

from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
import hashlib
import time as monotonic_time
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, cast

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .execution import ExecutionObservation
from .execution_observation_plane import (
    CausalExecutionSelection,
    ExecutionObservationPlane,
    default_execution_observation_plane,
)
from .llm_reliability_metrics import (
    LLMReliabilityLedger,
    classify_llm_failure,
)
from .market_data_recovery import RecoveredBarSeries, RecoveryAttempt
from .service import TradingMarketDataService, default_market_data_service
from .strategy_ai_shadow import simulate_ai_shadow_fill
from .strategy_ai_shadow_v3 import (
    AI_SHADOW_V3_POLICY_VERSION,
    AIShadowV3Analyzer,
    V3FeatureSnapshot,
    agreement_cohort,
    build_authoritative_runner_geometry,
    build_v3_feature_snapshot,
    compare_runner_geometry_challenger,
    trigger_condition_from_decision,
)
from .strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import resolve_v2_shadow_archive
from .trade_logging import trade_log
from .trigger_plan import (
    TriggerMarketSnapshot,
    TriggerPlan,
    TriggerPlanOrigin,
    TriggerPlanRepository,
    create_trigger_plan_id,
    default_trigger_plan_repository,
    evaluate_armed_trigger,
    transition_trigger_plan,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET

logger = logging.getLogger(__name__)


_STATE_KEY = "_omnix_trading_ai_shadow_v3_monitor"
_EVENT_TYPES = (
    "ai_v3_decision",
    "ai_v3_alpha_error",
    "ai_v3_trigger",
    "ai_v3_fill",
    "ai_v3_geometry_challenger",
    "ai_v3_agreement_cohort",
)
_LAST_ENTRY_ET = time(15, 30)


def _ibkr_live_quotes_authorized() -> bool:
    """The IBKR live-data gate in Settings; off (or unreadable) keeps IEX alone."""
    try:
        from .ibkr_settings import load_ibkr_settings

        settings = load_ibkr_settings()[0]
    except Exception:
        logger.debug("suppressed error in %s", "_ibkr_live_quotes_authorized", exc_info=True)
        return False
    return bool(settings.enabled and settings.live_authority_enabled)


def _causal_selection(
    plane: ExecutionObservationPlane,
    instrument_id: str,
    *,
    decision_completed_at: datetime,
    actionable_at: datetime,
    market_snapshot_as_of: datetime,
    binding_id: str | None,
    ibkr_live: bool,
) -> CausalExecutionSelection | None:
    """The first quote after the decision, from IBKR's live NBBO when authorized.

    IEX's own book is thin for small caps: it can sit unchanged for minutes and
    is far wider than the market. The source is fixed by policy, never chosen by
    price, and each source still contributes only its first post-decision quote.
    """
    timing = dict(
        decision_completed_at=decision_completed_at,
        actionable_at=actionable_at,
        market_snapshot_as_of=market_snapshot_as_of,
    )
    if ibkr_live:
        live = plane.first_causal_after(instrument_id, purpose="LIVE_DATA", **timing)
        if live is not None and live.provider == "ibkr":
            return live
    return plane.first_causal_after(instrument_id, binding_id=binding_id, **timing)


def _iex_book_spread_only(observation: ExecutionObservation, max_spread_bps: Decimal) -> bool:
    """The quote fails only on the spread of IEX's own (non-NBBO) book.

    Every other gate (stale or delayed data, halts, session) still rejects.
    """
    if observation.provider != "alpaca_iex" or observation.halted is True:
        return False
    if not observation.market_data_eligible:
        return False
    if set(observation.rejection_reasons) - {"SPREAD_TOO_WIDE"}:
        return False
    spread = observation.spread_bps
    return spread is not None and (
        "SPREAD_TOO_WIDE" in observation.rejection_reasons or spread > max_spread_bps
    )


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ai_shadow_v3_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_AI_SHADOW_V3_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_AI_SHADOW_V3_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_AI_SHADOW_V3_INTERVAL_SECONDS", "15"))
    except ValueError:
        value = 15.0
    return max(5.0, value)


def _key(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _eligible(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.strategy_id == MANAGED_FINVIZ_SHADOW_STRATEGY_ID
        and config.enabled
        and config.mode == "shadow"
        and config.active_universe_id is None
        and config.config.universe_discovery_source == "finviz"
    )


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _session_start_end(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(_ET)
    start = datetime(local.year, local.month, local.day, tzinfo=_ET)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _vwap(bars) -> Decimal | None:
    total_volume = sum((bar.volume for bar in bars), Decimal("0"))
    if total_volume <= 0:
        return None
    notional = sum(
        (
            ((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume
            for bar in bars
        ),
        Decimal("0"),
    )
    return notional / total_volume


def _trigger_snapshot(
    bars,
    *,
    observed_at: datetime,
) -> TriggerMarketSnapshot | None:
    regular = sorted(
        [
            bar
            for bar in bars
            if bar.is_final and bar.session == "regular" and bar.end_time <= observed_at
        ],
        key=lambda bar: bar.end_time,
    )
    if not regular:
        return None
    current = regular[-1]
    prior = regular[:-1]
    prior10 = prior[-10:]
    average_prior_volume = (
        sum((bar.volume for bar in prior10), Decimal("0")) / Decimal(len(prior10))
        if prior10
        else None
    )
    volume_ratio = (
        current.volume / average_prior_volume
        if average_prior_volume is not None and average_prior_volume > 0
        else None
    )
    return TriggerMarketSnapshot(
        observed_at=current.end_time,
        current_price=current.close,
        session_high=max(bar.high for bar in regular),
        session_vwap=_vwap(regular),
        volume_ratio=volume_ratio,
        previous_price=prior[-1].close if prior else None,
        previous_session_high=max((bar.high for bar in prior), default=None),
        previous_session_vwap=_vwap(prior) if prior else None,
        bar_high=current.high,
        bar_low=current.low,
        bar_close=current.close,
    )


def _trigger_quality_ready(
    plan: TriggerPlan,
    snapshot: V3FeatureSnapshot,
) -> tuple[bool, tuple[str, ...]]:
    required = ["recent_structure"]
    if plan.trigger.trigger_type == "vwap_reclaim":
        required.append("session_vwap")
    if plan.trigger.trigger_type == "new_session_high":
        required.append("session_extrema")
    failed = []
    for name in required:
        certificate = snapshot.certificate(name)
        if certificate is None or certificate.status == "INVALID":
            failed.append(name)
    return not failed, tuple(f"TRIGGER_FEATURE_INVALID:{name}" for name in failed)


class TradingAIShadowV3Monitor(ScheduledTradingMonitor):
    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        trigger_repository_factory: Callable[[], TriggerPlanRepository] = default_trigger_plan_repository,
        analyzer_factory: Callable[[], AIShadowV3Analyzer] = AIShadowV3Analyzer,
        execution_plane: ExecutionObservationPlane | None = None,
        reliability_ledger: LLMReliabilityLedger | None = None,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.trigger_repository_factory = trigger_repository_factory
        self.analyzer_factory = analyzer_factory
        self.execution_plane = execution_plane or default_execution_observation_plane()
        self.reliability = reliability_ledger or LLMReliabilityLedger()
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.decision_count = 0
        self.trigger_count = 0
        self.fill_count = 0
        self.rejection_count = 0
        self.geometry_challenger_count = 0
        self.agreement_count = 0
        self.data_gap_count = 0

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
        idem = _key(
            AI_SHADOW_V3_POLICY_VERSION,
            config.strategy_id,
            event_type,
            instrument_id,
            *identity,
        )
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=idem[:32],
                run_id="ai-shadow-v3",
                instrument_id=instrument_id,
                event_type=event_type,
                state=state,
                reason_code=reason_code,
                observed_at=observed_at,
                idempotency_key=idem,
                payload=payload,
            ),
        )

    def _recover_candidate(
        self,
        market_service: TradingMarketDataService,
        candidate,
        *,
        session_date,
        now: datetime,
    ) -> RecoveredBarSeries:
        recovered = market_service.recovered_bars(
            candidate.instrument_id,
            "1m",
            500,
            candidate.binding_id,
            session_date=session_date,
            as_of=now,
            knowledge_mode="live",
        )
        unresolved: list[datetime] = []
        for gap in recovered.report.unresolved_gaps:
            cursor = gap.start
            while cursor < gap.end:
                unresolved.append(cursor)
                cursor += timedelta(minutes=1)
        attempts = (
            RecoveryAttempt(
                stage="primary",
                source=recovered.report.primary_provider or "configured_history",
                succeeded=not bool(recovered.report.primary_error),
                bar_count=recovered.report.primary_bar_count,
                detail=recovered.report.primary_error,
            ),
            RecoveryAttempt(
                stage="fallback",
                source=recovered.report.fallback_provider or "none",
                succeeded=(
                    recovered.report.fallback_attempted
                    and not bool(recovered.report.fallback_error)
                ),
                bar_count=recovered.report.recovered_bar_count,
                detail=recovered.report.fallback_error,
            ),
        )
        status = (
            "UNAVAILABLE"
            if not recovered.bars
            else "PARTIAL"
            if unresolved
            else "COMPLETE"
        )
        return RecoveredBarSeries(
            instrument_id=candidate.instrument_id,
            interval="1m",
            session_date=session_date,
            observed_at=now,
            knowledge_mode=recovered.report.knowledge_mode,
            knowledge_cutoff=recovered.report.knowledge_cutoff,
            bars=tuple(recovered.bars),
            status=cast(Any, status),
            unresolved_starts=tuple(unresolved),
            confirmed_nontrading_starts=recovered.report.confirmed_nontrading_starts,
            attempts=attempts,
            source_providers=recovered.report.source_providers,
        )

    async def _service_trigger(
        self,
        *,
        plan: TriggerPlan,
        candidate,
        recovered: RecoveredBarSeries,
        feature: V3FeatureSnapshot,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        trigger_repository: TriggerPlanRepository,
    ) -> None:
        market = _trigger_snapshot(recovered.bars, observed_at=feature.observed_at)
        if market is None:
            return

        current = plan
        if current.status == "ARMED":
            quality_ok, quality_reasons = _trigger_quality_ready(current, feature)
            if not quality_ok:
                await self._append(
                    repository,
                    config,
                    instrument_id=current.instrument_id,
                    event_type="ai_v3_trigger",
                    state="data_gap",
                    reason_code="AI_V3_TRIGGER_FEATURE_DATA_INVALID",
                    observed_at=market.observed_at,
                    payload={
                        "trigger_plan_id": current.trigger_plan_id,
                        "reason_codes": list(quality_reasons),
                        "certificates": [
                            item.model_dump(mode="json")
                            for item in feature.certificates
                        ],
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(current.trigger_plan_id, market.observed_at, "data-gap"),
                )
                return
            updated = evaluate_armed_trigger(current, market)
            if updated.revision != current.revision:
                current = await asyncio.to_thread(
                    trigger_repository.save_transition,
                    current,
                    updated,
                )
                if current.status == "TRIGGERED":
                    self.trigger_count += 1
                await self._append(
                    repository,
                    config,
                    instrument_id=current.instrument_id,
                    event_type="ai_v3_trigger",
                    state=current.status.lower(),
                    reason_code="AI_V3_TRIGGER_TRANSITION",
                    observed_at=current.updated_at or market.observed_at,
                    payload={
                        "trigger_plan": current.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(current.trigger_plan_id, current.revision),
                )

        if current.status == "TRIGGERED":
            checking = transition_trigger_plan(
                current,
                status="EXECUTION_CHECK",
                reason="causal_quote_required",
                observed_at=current.updated_at or market.observed_at,
            )
            current = await asyncio.to_thread(
                trigger_repository.save_transition,
                current,
                checking,
            )

        if current.status != "EXECUTION_CHECK":
            return

        actionable_at = current.updated_at or market.observed_at
        selection = _causal_selection(
            self.execution_plane,
            current.instrument_id,
            decision_completed_at=current.created_at,
            actionable_at=actionable_at,
            market_snapshot_as_of=market.observed_at,
            binding_id=candidate.binding_id,
            ibkr_live=await asyncio.to_thread(_ibkr_live_quotes_authorized),
        )
        if selection is None:
            if self.now_factory().astimezone(timezone.utc) >= current.expires_at:
                rejected = transition_trigger_plan(
                    current,
                    status="REJECTED",
                    reason="causal_execution_capture_unavailable",
                    observed_at=self.now_factory(),
                )
                await asyncio.to_thread(
                    trigger_repository.save_transition,
                    current,
                    rejected,
                )
                self.rejection_count += 1
            return

        observation = selection.observation
        spread = observation.spread_bps
        iex_book_only = _iex_book_spread_only(observation, current.max_spread_bps)
        if not iex_book_only and (
            not observation.execution_eligible
            or observation.halted is True
            or spread is None
            or spread > current.max_spread_bps
        ):
            rejected = transition_trigger_plan(
                current,
                status="REJECTED",
                reason="execution_observation_ineligible",
                observed_at=selection.first_post_decision_quote_at,
            )
            rejected = await asyncio.to_thread(
                trigger_repository.save_transition,
                current,
                rejected,
            )
            self.rejection_count += 1
            await self._append(
                repository,
                config,
                instrument_id=current.instrument_id,
                event_type="ai_v3_fill",
                state="rejected",
                reason_code="AI_V3_EXECUTION_REJECTED",
                observed_at=selection.first_post_decision_quote_at,
                payload={
                    "trigger_plan_id": current.trigger_plan_id,
                    "causal_execution": selection.model_dump(mode="json"),
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(current.trigger_plan_id, rejected.revision),
            )
            return

        execution = observation.model_dump(mode="python")
        execution["spread_bps"] = observation.spread_bps
        if iex_book_only:
            # IEX's own book is not the NBBO; price the research fill from the
            # last trade with the simulator's assumed spread instead.
            execution.update(bid=None, ask=None, spread_bps=None)
        simulation = simulate_ai_shadow_fill(
            execution,
            side="buy",
            instrument_id=current.instrument_id,
            binding_id=observation.binding_id,
            decision_at=actionable_at,
            requested_units=Decimal("1"),
            reference_price=current.geometry.entry_reference,
            max_capture_lag_seconds=Decimal("10"),
            allow_degraded_price_only=iex_book_only,
        )
        final_status = "FILLED" if simulation.should_fill else "REJECTED"
        final = transition_trigger_plan(
            current,
            status=cast(Any, final_status),
            reason=simulation.fill_reason,
            observed_at=selection.first_post_decision_quote_at,
        )
        final = await asyncio.to_thread(
            trigger_repository.save_transition,
            current,
            final,
        )
        if simulation.should_fill:
            self.fill_count += 1
        else:
            self.rejection_count += 1
        await self._append(
            repository,
            config,
            instrument_id=current.instrument_id,
            event_type="ai_v3_fill",
            state=final.status.lower(),
            reason_code=(
                "AI_V3_FILL_NOT_EXECUTABLE"
                if not simulation.should_fill
                else "AI_V3_FILL_HYPOTHETICAL_IEX_BOOK"
                if simulation.hypothetical
                else "AI_V3_FILL_SIMULATED"
            ),
            observed_at=selection.first_post_decision_quote_at,
            payload={
                "trigger_plan_id": current.trigger_plan_id,
                "geometry": current.geometry.model_dump(mode="json"),
                "causal_execution": selection.model_dump(mode="json"),
                "simulation": simulation.model_dump(mode="json"),
                "research_only": True,
                "execution_authority": False,
            },
            identity=(current.trigger_plan_id, final.revision),
        )

    async def _record_geometry_challengers(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        rows_by_id: dict[str, dict[str, Any]],
        events: list[StrategyEvent],
    ) -> None:
        existing_sources = {
            str(event.payload.get("source_v2_event_id"))
            for event in events
            if event.event_type == "ai_v3_geometry_challenger"
            and event.payload.get("source_v2_event_id")
        }
        v2_events = [
            event
            for event in events
            if event.event_type == "ai_v2_decision"
            and event.event_id not in existing_sources
            and isinstance(event.payload, dict)
            and isinstance(event.payload.get("decision"), dict)
            and any(
                str(reason).startswith("AI_V2_GEOMETRY_VETO:")
                for reason in (event.payload.get("policy_reasons") or ())
            )
        ]
        for event in v2_events:
            row = rows_by_id.get(event.instrument_id)
            if row is None:
                continue
            recovered = row["recovered"]
            assert isinstance(recovered, RecoveredBarSeries)
            causal_bars = [
                bar
                for bar in recovered.bars
                if bar.end_time <= event.observed_at.astimezone(timezone.utc)
            ]
            if not causal_bars:
                continue
            feature = build_v3_feature_snapshot(
                causal_bars,
                instrument_id=event.instrument_id,
                session_date=recovered.session_date,
                observed_at=event.observed_at,
            )
            try:
                record = compare_runner_geometry_challenger(
                    instrument_id=event.instrument_id,
                    v2_decision=event.payload["decision"],
                    bars=causal_bars,
                    feature_snapshot=feature,
                    estimated_cost_bps=config.risk.max_spread_bps + Decimal("10"),
                    minimum_net_r=config.config.reward_multiple,
                )
            except Exception as exc:
                self.last_error = (
                    f"geometry_challenger/{event.instrument_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if await self._append(
                repository,
                config,
                instrument_id=event.instrument_id,
                event_type="ai_v3_geometry_challenger",
                state=record.challenger_action.lower(),
                reason_code="AI_V3_RUNNER_GEOMETRY_CHALLENGER",
                observed_at=event.observed_at,
                payload={
                    "source_v2_event_id": event.event_id,
                    "source_v2_decision": event.payload["decision"],
                    "comparison": record.model_dump(mode="json"),
                    "feature_certificates": [
                        item.model_dump(mode="json") for item in feature.certificates
                    ],
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(event.event_id, "runner-geometry"),
            ):
                self.geometry_challenger_count += 1

    async def _record_agreement_cohorts(
        self,
        *,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        events: list[StrategyEvent],
        decision_events: list[StrategyEvent],
    ) -> None:
        existing = {
            str(event.payload.get("source_ai_v3_event_id"))
            for event in events
            if event.event_type == "ai_v3_agreement_cohort"
            and event.payload.get("source_ai_v3_event_id")
        }
        legacy = [event for event in events if event.event_type == "ai_shadow_decision"]
        v2 = [event for event in events if event.event_type == "ai_v2_decision"]
        for decision_event in decision_events:
            if decision_event.event_id in existing:
                continue
            cutoff = decision_event.observed_at
            window_start = cutoff - timedelta(seconds=90)
            legacy_candidates = [
                event
                for event in legacy
                if event.instrument_id == decision_event.instrument_id
                and window_start <= event.observed_at <= cutoff
            ]
            v2_candidates = [
                event
                for event in v2
                if event.instrument_id == decision_event.instrument_id
                and window_start <= event.observed_at <= cutoff
            ]
            latest_legacy = max(
                legacy_candidates,
                key=lambda event: (event.observed_at, event.event_id),
                default=None,
            )
            latest_v2 = max(
                v2_candidates,
                key=lambda event: (event.observed_at, event.event_id),
                default=None,
            )
            v1_action = (
                latest_legacy.payload.get("action")
                if latest_legacy is not None
                else None
            )
            if latest_legacy is not None and isinstance(
                latest_legacy.payload.get("decision"), dict
            ):
                v1_action = latest_legacy.payload["decision"].get("action")
            v2_state = (
                latest_v2.payload.get("effective_state")
                if latest_v2 is not None
                else None
            )
            cohort = agreement_cohort(v1_action=v1_action, v2_state=v2_state)
            if await self._append(
                repository,
                config,
                instrument_id=decision_event.instrument_id,
                event_type="ai_v3_agreement_cohort",
                state=cohort,
                reason_code="AI_V3_V1_V2_AGREEMENT_COHORT",
                observed_at=cutoff,
                payload={
                    "source_ai_v3_event_id": decision_event.event_id,
                    "v1_event_id": latest_legacy.event_id if latest_legacy else None,
                    "v2_event_id": latest_v2.event_id if latest_v2 else None,
                    "v1_action": v1_action,
                    "v2_state": v2_state,
                    "reference_geometry": decision_event.payload.get(
                        "authoritative_geometry"
                    ),
                    "decision_completed_at": decision_event.payload.get(
                        "decision_completed_at"
                    ),
                    "statistical_independence_claimed": False,
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(decision_event.event_id, "agreement"),
            ):
                self.agreement_count += 1

    async def _run_config(
        self,
        config: TradingStrategyConfigDocument,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        trigger_repository: TriggerPlanRepository,
        *,
        now: datetime,
    ) -> int:
        if not _eligible(config):
            return 0
        universe = await asyncio.to_thread(
            resolve_v2_shadow_archive,
            config,
            repository,
            now=now,
        )
        if universe is None or universe.session_date != now.astimezone(_ET).date():
            return 0
        day_start, day_end = _session_start_end(now)
        events = await asyncio.to_thread(
            repository.events_by_types_between,
            config.strategy_id,
            event_types=(
                *_EVENT_TYPES,
                "ai_v2_decision",
                "ai_shadow_decision",
            ),
            start_time=day_start,
            end_time=day_end,
            limit=50_000,
        )

        rows: list[dict[str, Any]] = []
        rows_by_id: dict[str, dict[str, Any]] = {}
        for candidate in universe.candidates:
            recovered = await asyncio.to_thread(
                self._recover_candidate,
                market_service,
                candidate,
                session_date=universe.session_date,
                now=now,
            )
            feature = build_v3_feature_snapshot(
                recovered.bars,
                instrument_id=candidate.instrument_id,
                session_date=universe.session_date,
                observed_at=now,
            )
            row = {
                "candidate": candidate,
                "recovered": recovered,
                "feature": feature,
            }
            rows.append(row)
            rows_by_id[candidate.instrument_id] = row

        active_plans = await asyncio.to_thread(
            trigger_repository.active_for_strategy,
            config.strategy_id,
        )
        for plan in active_plans:
            if plan.arm_id != AI_SHADOW_V3_POLICY_VERSION:
                continue
            found_row = rows_by_id.get(plan.instrument_id)
            if found_row is None:
                continue
            row = found_row
            await self._service_trigger(
                plan=plan,
                candidate=row["candidate"],
                recovered=row["recovered"],
                feature=row["feature"],
                config=config,
                repository=repository,
                trigger_repository=trigger_repository,
            )

        await self._record_geometry_challengers(
            config=config,
            repository=repository,
            rows_by_id=rows_by_id,
            events=events,
        )

        if now.astimezone(_ET).time() > min(_LAST_ENTRY_ET, config.risk.force_flat_et):
            return 0

        latest_decision_at: dict[str, datetime] = {}
        for event in events:
            if event.event_type != "ai_v3_decision":
                continue
            prior = latest_decision_at.get(event.instrument_id)
            if prior is None or event.observed_at > prior:
                latest_decision_at[event.instrument_id] = event.observed_at

        prepared: list[dict[str, Any]] = []
        prepared_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            candidate = row["candidate"]
            feature = row["feature"]
            assert isinstance(feature, V3FeatureSnapshot)
            if not feature.alpha_input_ready:
                self.data_gap_count += 1
                continue
            last = latest_decision_at.get(candidate.instrument_id)
            if last is not None and last >= feature.observed_at:
                continue
            prepared.append(
                {
                    "instrument_id": candidate.instrument_id,
                    "morning_rank": candidate.discovery_rank,
                    "feature_snapshot": feature.model_dump(mode="json"),
                    "recovery": row["recovered"].model_dump(
                        mode="json",
                        exclude={"bars"},
                    ),
                    "geometry_authority": "deterministic_only",
                }
            )
            prepared_rows[candidate.instrument_id] = row

        if not prepared:
            return 0

        analyzer = self.analyzer_factory()
        provider = "unknown"
        model = None
        if hasattr(analyzer, "identity"):
            try:
                provider, model = analyzer.identity()
            except Exception:
                logger.debug("suppressed error in %s", "TradingAIShadowV3Monitor._run_config", exc_info=True)
        self.reliability.scheduled(provider, model, len(prepared))
        self.reliability.attempt(provider, model)
        started = monotonic_time.monotonic()
        try:
            result = await asyncio.to_thread(analyzer.assess, prepared)
        except Exception as exc:
            kind = classify_llm_failure(exc)
            self.reliability.failure(
                provider,
                model,
                kind=kind,
                missed=len(prepared),
            )
            self.last_error = f"{type(exc).__name__}: {exc}"
            for item in prepared:
                await self._append(
                    repository,
                    config,
                    instrument_id=str(item["instrument_id"]),
                    event_type="ai_v3_alpha_error",
                    state="error",
                    reason_code="AI_V3_ALPHA_PROVIDER_ERROR",
                    observed_at=now,
                    payload={
                        "provider": provider,
                        "model": model,
                        "failure_kind": kind,
                        "detail": str(exc)[:1000],
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(now.isoformat(), item["instrument_id"], kind),
                )
            return 0

        elapsed_ms = Decimal(str((monotonic_time.monotonic() - started) * 1000))
        self.reliability.success(
            result.provider,
            result.model,
            decisions=len(result.decisions),
            provider_latency_ms=result.provider_latency_ms,
            end_to_end_latency_ms=elapsed_ms,
        )
        emitted_events: list[StrategyEvent] = []
        decision_completed_at = self.now_factory()
        for decision in result.decisions:
            found_row = prepared_rows.get(decision.instrument_id)
            if found_row is None:
                continue
            row = found_row
            feature = row["feature"]
            recovered = row["recovered"]
            candidate = row["candidate"]
            assert isinstance(feature, V3FeatureSnapshot)
            assert isinstance(recovered, RecoveredBarSeries)
            regular = [
                bar
                for bar in recovered.bars
                if bar.is_final and bar.session == "regular"
            ]
            geometry = build_authoritative_runner_geometry(
                setup_family=decision.setup_family,
                bars=regular,
                feature_snapshot=feature,
                estimated_cost_bps=config.risk.max_spread_bps + Decimal("10"),
                minimum_net_r=config.config.reward_multiple,
            )
            effective_state = decision.state
            policy_reasons: list[str] = []
            if decision.state in {"armed", "enter"} and not geometry.valid:
                effective_state = "watch"
                policy_reasons.extend(
                    f"AI_V3_GEOMETRY_VETO:{reason}"
                    for reason in geometry.reason_codes
                )

            decision_id = _key(
                config.strategy_id,
                AI_SHADOW_V3_POLICY_VERSION,
                decision.instrument_id,
                feature.observed_at,
                decision_completed_at,
            )[:32]
            payload = {
                "policy_version": AI_SHADOW_V3_POLICY_VERSION,
                "decision_id": decision_id,
                "provider": result.provider,
                "model": result.model,
                "market_snapshot_as_of": feature.observed_at,
                "decision_completed_at": decision_completed_at,
                "provider_latency_ms": str(result.provider_latency_ms),
                "end_to_end_latency_ms": str(elapsed_ms),
                "raw_alpha_decision": decision.model_dump(mode="json"),
                "effective_state": effective_state,
                "policy_reasons": policy_reasons,
                "authoritative_geometry": geometry.model_dump(mode="json"),
                "model_geometry_is_advisory": True,
                "feature_snapshot": feature.model_dump(mode="json"),
                "recovery": recovered.model_dump(mode="json", exclude={"bars"}),
                "research_only": True,
                "execution_authority": False,
            }
            persisted = await self._append(
                repository,
                config,
                instrument_id=decision.instrument_id,
                event_type="ai_v3_decision",
                state=effective_state,
                reason_code="AI_V3_ALPHA_DECISION",
                observed_at=feature.observed_at,
                payload=payload,
                identity=(decision_id,),
            )
            if persisted:
                self.decision_count += 1
                emitted_events.append(
                    StrategyEvent(
                        strategy_id=config.strategy_id,
                        event_id=_key("local", decision_id)[:32],
                        run_id="ai-shadow-v3-local",
                        instrument_id=decision.instrument_id,
                        event_type="ai_v3_decision",
                        state=effective_state,
                        reason_code="AI_V3_ALPHA_DECISION",
                        observed_at=feature.observed_at,
                        idempotency_key=_key("local", decision_id),
                        payload=payload,
                    )
                )

            if effective_state not in {"armed", "enter"}:
                continue
            expiry_minutes = (
                decision.trigger.expiry_minutes
                if decision.trigger is not None
                else 2
            )
            plan = TriggerPlan(
                trigger_plan_id=create_trigger_plan_id(
                    strategy_id=config.strategy_id,
                    arm_id=AI_SHADOW_V3_POLICY_VERSION,
                    instrument_id=decision.instrument_id,
                    decision_id=decision_id,
                ),
                strategy_id=config.strategy_id,
                arm_id=AI_SHADOW_V3_POLICY_VERSION,
                instrument_id=decision.instrument_id,
                created_at=decision_completed_at,
                expires_at=decision_completed_at + timedelta(minutes=expiry_minutes),
                trigger=trigger_condition_from_decision(
                    decision,
                    geometry=geometry,
                ),
                geometry=geometry,
                required_certificate_ids=tuple(
                    item.certificate_id
                    for item in feature.certificates
                    if item.status != "INVALID"
                ),
                max_spread_bps=config.risk.max_spread_bps,
                origin=TriggerPlanOrigin(
                    decision_id=decision_id,
                    provider=result.provider,
                    model=result.model,
                    setup_family=decision.setup_family,
                    thesis=decision.thesis,
                    policy_version=AI_SHADOW_V3_POLICY_VERSION,
                ),
            )
            plan = await asyncio.to_thread(trigger_repository.create, plan)
            if effective_state == "enter" and plan.status == "ARMED":
                triggered = transition_trigger_plan(
                    plan,
                    status="TRIGGERED",
                    reason="alpha_enter_actionable",
                    observed_at=decision_completed_at,
                )
                plan = await asyncio.to_thread(
                    trigger_repository.save_transition,
                    plan,
                    triggered,
                )
                self.trigger_count += 1
                await self._service_trigger(
                    plan=plan,
                    candidate=candidate,
                    recovered=recovered,
                    feature=feature,
                    config=config,
                    repository=repository,
                    trigger_repository=trigger_repository,
                )

        await self._record_agreement_cohorts(
            config=config,
            repository=repository,
            events=events,
            decision_events=emitted_events,
        )
        return len(result.decisions)

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("ai shadow v3 clock must be timezone-aware")
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        trigger_repository = self.trigger_repository_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        total = 0
        for config in configs:
            try:
                total += await self._run_config(
                    config,
                    repository,
                    market_service,
                    trigger_repository,
                    now=now,
                )
            except Exception as exc:
                self.last_error = (
                    f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                )
                trade_log(
                    "auto_trading",
                    "ai_shadow_v3_runtime_error",
                    strategy_id=config.strategy_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
        self.last_run_at = now
        return total

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": ai_shadow_v3_monitor_enabled(),
            "running": self.scheduled,
            "policy_version": AI_SHADOW_V3_POLICY_VERSION,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "decision_count": self.decision_count,
            "trigger_count": self.trigger_count,
            "fill_count": self.fill_count,
            "rejection_count": self.rejection_count,
            "geometry_challenger_count": self.geometry_challenger_count,
            "agreement_count": self.agreement_count,
            "data_gap_count": self.data_gap_count,
            "llm_reliability": self.reliability.snapshot().model_dump(mode="json"),
            "v2_champion_mutated": False,
            "execution_authority": False,
        }


def create_trading_ai_shadow_v3_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingAIShadowV3Monitor):
        return None
    monitor = TradingAIShadowV3Monitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=ai_shadow_v3_monitor_enabled)


__all__ = [
    "TradingAIShadowV3Monitor",
    "ai_shadow_v3_monitor_enabled",
    "create_trading_ai_shadow_v3_monitor_task",
]
