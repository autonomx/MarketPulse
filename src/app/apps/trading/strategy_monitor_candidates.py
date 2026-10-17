"""Candidate evaluation and entry selection for the strategy monitor (moved out of the class)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from app.apps.trading.us_equity_calendar import EASTERN as _ET

from . import strategy_session_evidence as _session_evidence
from .feature_qualification import FeatureRequirement, qualify_bar_feature
from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from .market_evidence import MARKET_EVIDENCE_POLICY_VERSION
from .research.policy import ResearchPolicyDecision
from .service import TradingMarketDataService
from .strategies import evaluate_gap_pullback
from .strategies.models import GapPullbackResult
from .strategy_evaluability import (
    assess_session_evaluability,
)
from .strategy_intraday_learning import (
    IntradayLearningSnapshot,
    build_intraday_learning_snapshot,
)

# The monitor imports this module lazily, so importing it here makes no cycle.
from .strategy_monitor import (
    _REGULAR_OPEN,
    StrategyRunHost,
    _bar_audit_payload,
    _candidate_lifecycle_stage,
    _current_session_1m_integrity,
    _EntryProposal,
    _finalized_bars_for_session,
)
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_research_policy import (
    apply_research_policy_to_quality,
    resolve_strategy_research_policy,
)
from .strategy_shadow_execution import observe_shadow_execution
from .strategy_stoch_execution_cost import (
    StochExecutionAction,
    simulate_stoch_execution,
)
from .strategy_stoch_execution_cost import (
    action_for_snapshot as stoch_execution_action_for_snapshot,
)
from .strategy_stoch_execution_cost import (
    build_execution_summary as build_stoch_execution_summary,
)
from .strategy_stoch_execution_cost import (
    requested_fraction_for_action as stoch_requested_fraction_for_action,
)
from .strategy_stoch_trend_capture import (
    evaluate_stoch_trend_capture,
    stoch_trend_capture_risk_decision,
)
from .strategy_timeframes import resample_final_bars
from .strategy_v2_qualification import (
    v2_profile_fingerprint,
)
from .trade_logging import trade_log


async def evaluate_candidates(
    monitor: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    market_service: TradingMarketDataService,
    universe,
) -> list[_EntryProposal]:
    now = datetime.now(timezone.utc)
    universe_session_date = getattr(universe, "session_date", None)
    if config.mode == "shadow" and (
        universe_session_date is None
        or universe_session_date == now.astimezone(_ET).date()
    ):
        market_service = cast(TradingMarketDataService, _session_evidence._CurrentSessionMarketDataProxy(
            market_service,
            session_date=universe_session_date or now.astimezone(_ET).date(),
            observed_at=now,
        ))
    market_service = cast(TradingMarketDataService, _session_evidence._FullSessionMarketServiceProxy(
        market_service,
        session_date=getattr(
            universe,
            "session_date",
            now.astimezone(_ET).date(),
        ),
        observed_at=now,
        allow_shadow_fallback=config.mode == "shadow",
    ))
    if isinstance(universe, GapperUniverseSnapshot):
        session_assessment = assess_session_evaluability(universe, config.config)
        await monitor._event(
            strategy_repository,
            config,
            instrument_id="__universe__",
            event_type="session_evaluability",
            state=session_assessment.status,
            reason_code="SESSION_EVALUABILITY_ASSESSED",
            observed_at=universe.evaluation_time,
            payload={
                **session_assessment.model_dump(mode="json"),
                "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                "research_only": True,
                "execution_authority": False,
            },
        )
    proposals: list[_EntryProposal] = []
    learning_rows: list[tuple[GapperCandidate, GapPullbackResult, datetime, IntradayLearningSnapshot]] = []
    evaluated_any = False

    captured_stoch_entry_signals: set[tuple[str, str]] = set()
    captured_stoch_execution_actions: set[tuple[str, str, str]] = set()
    stoch_entry_payload_by_instrument: dict[str, dict[str, Any]] = {}
    stoch_action_payloads_by_instrument: dict[
        str,
        dict[StochExecutionAction, dict[str, Any]],
    ] = {}
    stoch_execution_history_available = True
    if config.config.stoch_trend_capture_enabled:
        session_start_et = datetime(
            universe.session_date.year,
            universe.session_date.month,
            universe.session_date.day,
            tzinfo=_ET,
        )
        session_end_et = session_start_et + timedelta(days=1)
        try:
            if hasattr(strategy_repository, "events_by_types_between"):
                prior_stoch_events = await asyncio.to_thread(
                    strategy_repository.events_by_types_between,
                    config.strategy_id,
                    event_types=(
                        "stoch_trend_capture_entry",
                        "stoch_trend_execution",
                    ),
                    start_time=session_start_et.astimezone(timezone.utc),
                    end_time=session_end_et.astimezone(timezone.utc),
                    limit=2_000,
                )
            else:
                prior_stoch_events = [
                    event
                    for event in await asyncio.to_thread(
                        strategy_repository.recent_events,
                        config.strategy_id,
                        4_000,
                    )
                    if event.event_type
                    in {
                        "stoch_trend_capture_entry",
                        "stoch_trend_execution",
                    }
                    and session_start_et.astimezone(timezone.utc)
                    <= event.observed_at.astimezone(timezone.utc)
                    < session_end_et.astimezone(timezone.utc)
                ]

            for event in prior_stoch_events:
                payload = event.payload if isinstance(event.payload, dict) else {}
                if event.event_type == "stoch_trend_capture_entry":
                    captured_stoch_entry_signals.add(
                        (
                            event.instrument_id,
                            event.observed_at.astimezone(timezone.utc).isoformat(),
                        )
                    )
                    stoch_entry_payload_by_instrument[event.instrument_id] = payload
                    continue

                action = payload.get("action")
                if action not in {
                    "range_exit",
                    "partial_exit",
                    "runner_exit",
                    "force_flat",
                }:
                    continue
                action_name = str(action)
                captured_stoch_execution_actions.add(
                    (
                        event.instrument_id,
                        action_name,
                        event.observed_at.astimezone(timezone.utc).isoformat(),
                    )
                )
                stoch_action_payloads_by_instrument.setdefault(
                    event.instrument_id,
                    {},
                )[action_name] = payload  # type: ignore[index]
        except Exception as exc:
            stoch_execution_history_available = False
            # Evidence lookup is fail-closed for the overlay only. The
            # canonical deterministic strategy continues unaffected.
            trade_log(
                "auto_trading",
                "stoch_trend_execution_history_error",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                universe_id=universe.universe_id,
                error_type=type(exc).__name__,
                detail=str(exc),
                research_only=True,
                execution_authority=False,
            )

    for candidate in universe.candidates:
        now_utc = datetime.now(timezone.utc)
        integrity_observed_at = getattr(universe, "evaluation_time", now_utc)
        legacy_candidate_contract = not hasattr(
            universe, "evaluation_time"
        ) and not hasattr(candidate, "market_data_complete")
        membership_only = (
            config.config.strategy_version == "2.0.0"
            and getattr(universe, "discovery_source", None) == "finviz"
        )
        if getattr(candidate, "market_data_complete", True) is False and not membership_only:
            # Candidate enrichment is no longer a trade-wide authority gate.
            # Preserve the diagnostic, then let the strategy's actual
            # feature requirements decide whether the candidate is usable.
            await monitor._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="data_integrity",
                state="degraded",
                reason_code="CANDIDATE_ENRICHMENT_INCOMPLETE",
                observed_at=integrity_observed_at,
                payload={
                    "universe_id": universe.universe_id,
                    "market_data_complete": False,
                    "data_quality_flags": list(
                        getattr(candidate, "data_quality_flags", ())
                    ),
                    "premarket_bar_count": getattr(
                        candidate,
                        "premarket_bar_count",
                        None,
                    ),
                    "feature_local_authority": True,
                    "research_only": True,
                    "execution_authority": False,
                },
            )

        primary_error: Exception | None = None
        shared_recovery = None
        stoch_capture = None
        try:
            recovery_method = getattr(market_service, "recovered_bars", None)
            if callable(recovery_method):
                shared_recovery = await asyncio.to_thread(
                    recovery_method,
                    candidate.instrument_id,
                    "1m",
                    500,
                    candidate.binding_id,
                    session_date=universe.session_date,
                    as_of=now_utc,
                )
                if shared_recovery.report.primary_error:
                    primary_error = RuntimeError(shared_recovery.report.primary_error)
                raw_bars = list(shared_recovery.bars)
            else:
                response = await asyncio.to_thread(
                    market_service.bars,
                    candidate.instrument_id,
                    "1m",
                    500,
                    candidate.binding_id,
                )
                raw_bars = list(response.bars)
            if legacy_candidate_contract:
                base_bars = [bar for bar in raw_bars if bar.is_final]
            else:
                base_bars = _finalized_bars_for_session(
                    raw_bars,
                    universe.session_date,
                )
        except Exception as exc:
            primary_error = exc
            base_bars = []

        gap_pullback_coverage = None
        if legacy_candidate_contract:
            current_ready = bool(base_bars)
            integrity_reason = (
                "CURRENT_SESSION_1M_READY"
                if current_ready
                else "CURRENT_SESSION_1M_UNAVAILABLE"
            )
        elif now_utc.astimezone(_ET).date() < universe.session_date or (
            now_utc.astimezone(_ET).date() == universe.session_date
            and now_utc.astimezone(_ET).time() < _REGULAR_OPEN
        ):
            current_ready = False
            integrity_reason = "CURRENT_SESSION_NOT_STARTED"
        elif not base_bars:
            current_ready = False
            integrity_reason = "CURRENT_SESSION_1M_UNAVAILABLE"
        else:
            report = shared_recovery.report if shared_recovery is not None else None
            gap_pullback_coverage = qualify_bar_feature(
                base_bars,
                FeatureRequirement(
                    requirement_id="gap-pullback-session-evidence-v1",
                    feature_name="gap_pullback_session_ohlcv",
                    interval="1m",
                    dependency_class="SESSION_CUMULATIVE",
                ),
                instrument_id=candidate.instrument_id,
                session_date=universe.session_date,
                observed_at=now_utc,
                confirmed_nontrading_starts=(
                    report.confirmed_nontrading_starts
                    if report is not None
                    else ()
                ),
                knowledge_mode=(
                    report.knowledge_mode
                    if report is not None
                    else "live"
                ),
                knowledge_cutoff=(
                    report.knowledge_cutoff
                    if report is not None
                    else now_utc
                ),
            )
            current_ready = gap_pullback_coverage.status != "INVALID"
            integrity_reason = (
                "GAP_PULLBACK_REQUIRED_FEATURES_READY"
                if current_ready
                else "GAP_PULLBACK_REQUIRED_FEATURES_INVALID"
            )
        bar_source = (
            "shared_recovery:"
            + ",".join(shared_recovery.report.source_providers)
            if shared_recovery is not None
            else "configured_history"
        )

        # Finviz learning is a non-canonical SHADOW experiment. It may use
        # current Alpaca IEX indicator history to rescue a missing Yahoo
        # opening sequence, but canonical AUTO PAPER never changes evidence
        # source through this path.
        if (
            not current_ready
            and integrity_reason != "CURRENT_SESSION_NOT_STARTED"
            and config.mode == "shadow"
            and universe.discovery_source == "finviz"
            and shared_recovery is None
        ):
            try:
                fallback_bars = await asyncio.to_thread(
                    market_service.execution_indicator_bars,
                    candidate.instrument_id,
                    candidate.binding_id,
                    as_of=now_utc,
                )
                fallback_session = _finalized_bars_for_session(
                    fallback_bars,
                    universe.session_date,
                )
                fallback_ready, fallback_reason = _current_session_1m_integrity(
                    fallback_session,
                    session_date=universe.session_date,
                    observed_at=now_utc,
                )
            except Exception as fallback_exc:
                fallback_ready = False
                fallback_reason = integrity_reason
                if monitor._should_log_diagnostic(
                    ("bars_fallback", config.strategy_id, candidate.instrument_id),
                    now_utc,
                ):
                    trade_log(
                        "auto_trading",
                        "candidate_bars_fallback_error",
                        run_id=monitor.current_run_id,
                        strategy_id=config.strategy_id,
                        universe_id=universe.universe_id,
                        instrument_id=candidate.instrument_id,
                        error_type=type(fallback_exc).__name__,
                        detail=str(fallback_exc),
                        execution_authority=False,
                    )
            if fallback_ready:
                base_bars = fallback_session
                current_ready = True
                integrity_reason = "CURRENT_SESSION_1M_FALLBACK"
                bar_source = "alpaca_iex_indicator_fallback"
            else:
                integrity_reason = fallback_reason

        if not current_ready:
            yahoo_recovery_report = (
                shared_recovery.report
                if shared_recovery is not None
                and shared_recovery.report.primary_provider == "yahoo"
                else None
            )
            if (
                yahoo_recovery_report is not None
                and integrity_reason != "CURRENT_SESSION_NOT_STARTED"
                and yahoo_recovery_report.unresolved_gaps
            ):
                monitor.yahoo_unresolved_candidate_evaluation_count += 1
                evidence_store = getattr(
                    market_service,
                    "yahoo_evidence_store",
                    None,
                )
                recorder = getattr(evidence_store, "record_evaluation_outcome", None)
                if callable(recorder):
                    await asyncio.to_thread(
                        recorder,
                        repaired=yahoo_recovery_report.recovered_bar_count > 0,
                        unresolved=True,
                    )
            state = "waiting" if integrity_reason == "CURRENT_SESSION_NOT_STARTED" else "invalid"
            await monitor._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="data_integrity",
                state=state,
                reason_code=integrity_reason,
                observed_at=integrity_observed_at,
                payload={
                    "universe_id": universe.universe_id,
                    "market_data_complete": True,
                    "current_session_1m_complete": False,
                    "causal_1m_available": False,
                    "detected_at": now_utc,
                    "primary_error": (
                        f"{type(primary_error).__name__}: {primary_error}"
                        if primary_error is not None
                        else None
                    ),
                    "recovery_report": (
                        shared_recovery.report.model_dump(mode="json")
                        if shared_recovery is not None
                        else None
                    ),
                    "feature_certificate": (
                        gap_pullback_coverage.model_dump(mode="json")
                        if gap_pullback_coverage is not None
                        else None
                    ),
                    "yahoo_recovery_applied": bool(
                        yahoo_recovery_report is not None
                        and yahoo_recovery_report.recovered_bar_count > 0
                    ),
                    "yahoo_recovery_unresolved": bool(
                        yahoo_recovery_report is not None
                        and yahoo_recovery_report.unresolved_gaps
                    ),
                    "research_only": True,
                    "execution_authority": False,
                },
            )
            if primary_error is not None and monitor._should_log_diagnostic(
                ("bars_primary", config.strategy_id, candidate.instrument_id),
                now_utc,
            ):
                monitor.last_error = (
                    f"strategy_bars: {type(primary_error).__name__}: {primary_error}"
                )
                trade_log(
                    "auto_trading",
                    "candidate_bars_error",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    universe_id=universe.universe_id,
                    instrument_id=candidate.instrument_id,
                    binding_id=candidate.binding_id,
                    error_type=type(primary_error).__name__,
                    detail=str(primary_error),
                )
            continue

        execution_bars = resample_final_bars(
            base_bars,
            config.config.execution_interval,
        )
        structure_bars = resample_final_bars(
            base_bars,
            config.config.structure_interval,
        )
        if not execution_bars or not structure_bars:
            if monitor._should_log_diagnostic(
                ("bars_unusable", config.strategy_id, candidate.instrument_id),
                now_utc,
            ):
                trade_log(
                    "auto_trading",
                    "candidate_bars_unusable",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    universe_id=universe.universe_id,
                    instrument_id=candidate.instrument_id,
                    binding_id=candidate.binding_id,
                    finalized_bar_count=len(base_bars),
                    structure_bar_count=len(structure_bars),
                    execution_bar_count=len(execution_bars),
                )
            continue

        evaluation_key = (
            config.strategy_id,
            universe.universe_id,
            candidate.instrument_id,
        )
        latest_bar_end = base_bars[-1].end_time
        if (
            not legacy_candidate_contract
            and monitor._last_evaluated_bar_end.get(evaluation_key) == latest_bar_end
        ):
            continue
        monitor._last_evaluated_bar_end[evaluation_key] = latest_bar_end

        result = evaluate_gap_pullback(candidate, structure_bars, config.config)
        observed_at = structure_bars[-1].end_time
        evaluated_any = True
        monitor.evaluation_count += 1
        yahoo_recovered_evaluation = bool(
            shared_recovery is not None
            and shared_recovery.report.primary_provider == "yahoo"
            and shared_recovery.report.recovered_bar_count > 0
        )
        if yahoo_recovered_evaluation:
            monitor.yahoo_recovered_candidate_evaluation_count += 1
        if (
            shared_recovery is not None
            and shared_recovery.report.primary_provider == "yahoo"
        ):
            evidence_store = getattr(
                market_service,
                "yahoo_evidence_store",
                None,
            )
            recorder = getattr(evidence_store, "record_evaluation_outcome", None)
            if callable(recorder):
                await asyncio.to_thread(
                    recorder,
                    repaired=yahoo_recovered_evaluation,
                    unresolved=False,
                )
        await monitor._event(
            strategy_repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="data_integrity",
            state="valid",
            reason_code=integrity_reason,
            observed_at=integrity_observed_at,
            payload={
                "universe_id": universe.universe_id,
                "market_data_complete": getattr(candidate, "market_data_complete", True),
                "premarket_research_only": membership_only,
                "current_session_1m_complete": True,
                "causal_1m_available": True,
                "bar_source": bar_source,
                "detected_at": now_utc,
                "recovery_report": (
                    shared_recovery.report.model_dump(mode="json")
                    if shared_recovery is not None
                    else None
                ),
                "feature_certificate": (
                    gap_pullback_coverage.model_dump(mode="json")
                    if gap_pullback_coverage is not None
                    else None
                ),
                "yahoo_recovery_applied": yahoo_recovered_evaluation,
                "research_only": True,
                "execution_authority": False,
            },
        )
        await monitor._event(
            strategy_repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="state",
            state=result.state,
            reason_code=result.reason_code,
            observed_at=observed_at,
            payload={
                "features": result.features.model_dump(mode="json"),
                "transitions": list(result.transitions),
                "lifecycle_stage": _candidate_lifecycle_stage(result),
                "strategy_version": config.config.strategy_version,
                "profile_fingerprint": (
                    v2_profile_fingerprint(config.config)
                    if config.config.strategy_version == "2.0.0"
                    else config.config.strategy_version
                ),
                "mode": config.mode,
                "universe_id": universe.universe_id,
                "structure_interval": config.config.structure_interval,
                "execution_interval": config.config.execution_interval,
                "structure_bar_count": len(structure_bars),
                "execution_bar_count": len(execution_bars),
                "current_session_1m_complete": True,
                "causal_1m_available": True,
                "bar_source": bar_source,
                "latest_structure_bar": _bar_audit_payload(structure_bars[-1]),
                "latest_execution_bar": _bar_audit_payload(execution_bars[-1]),
                # Preserve a short overlapping causal 1-minute window on
                # every idempotent finalized-bar state event. Post-close
                # replay can de-duplicate by bar start_time and detect
                # missing minutes instead of reconstructing chart shape
                # from daily OHLC or hindsight.
                "causal_1m_bar_window": [
                    _bar_audit_payload(bar)
                    for bar in base_bars[-10:]
                ],
            },
        )
        if config.config.stoch_trend_capture_enabled:
            try:
                stoch_capture = evaluate_stoch_trend_capture(
                    base_bars,
                    entry_start_et=config.risk.entry_start_et,
                    last_entry_et=config.risk.last_entry_et,
                    force_flat_et=config.risk.force_flat_et,
                )
            except Exception as exc:
                trade_log(
                    "auto_trading",
                    "stoch_trend_capture_error",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    universe_id=universe.universe_id,
                    instrument_id=candidate.instrument_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    research_only=True,
                    execution_authority=False,
                )
            else:
                stoch_observed_at = stoch_capture.as_of or base_bars[-1].end_time
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="stoch_trend_capture",
                    state=stoch_capture.state,
                    reason_code=stoch_capture.reason_code,
                    observed_at=stoch_observed_at,
                    payload={
                        "universe_id": universe.universe_id,
                        "universe_discovery_source": universe.discovery_source,
                        "morning_discovery_rank": candidate.discovery_rank,
                        "policy": stoch_capture.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                )

                # Capture authoritative execution conditions exactly when the
                # first 3m oversold signal becomes actionable. Later cycles
                # must not backfill entry eligibility from changed quotes.
                stoch_signal_key = (
                    candidate.instrument_id,
                    stoch_capture.entry_signal_time.astimezone(timezone.utc).isoformat(),
                ) if stoch_capture.entry_signal_time is not None else None
                if (
                    stoch_capture.state == "entry_armed"
                    and stoch_capture.entry_signal_time is not None
                    and stoch_execution_history_available
                    and stoch_signal_key not in captured_stoch_entry_signals
                ):
                    try:
                        entry_evidence = await asyncio.to_thread(
                            observe_shadow_execution,
                            market_service,
                            instrument_id=candidate.instrument_id,
                            binding_id=candidate.binding_id,
                        )
                        risk_decision = stoch_trend_capture_risk_decision(
                            entry_evidence.execution,
                            max_spread_bps=config.risk.max_spread_bps,
                        )
                        entry_simulation = (
                            simulate_stoch_execution(
                                entry_evidence.execution,
                                action="entry",
                                instrument_id=candidate.instrument_id,
                                binding_id=candidate.binding_id,
                                decision_at=stoch_capture.entry_signal_time,
                                requested_fraction=Decimal("1"),
                            )
                            if risk_decision.allowed
                            else None
                        )
                        source_time = entry_evidence.execution.get("source_time")
                        capture_lag_seconds = None
                        if isinstance(source_time, datetime):
                            capture_lag_seconds = (
                                source_time.astimezone(timezone.utc)
                                - stoch_capture.entry_signal_time.astimezone(timezone.utc)
                            ).total_seconds()
                        entry_payload = {
                            "universe_id": universe.universe_id,
                            "policy_version": stoch_capture.policy_version,
                            "entry_signal_time": stoch_capture.entry_signal_time,
                            "execution_capture_observed_at": stoch_observed_at,
                            "execution_capture_lag_seconds": capture_lag_seconds,
                            "risk_decision": risk_decision.model_dump(mode="json"),
                            "execution": entry_evidence.execution,
                            "execution_simulation": (
                                entry_simulation.model_dump(mode="json")
                                if entry_simulation is not None
                                else None
                            ),
                            "research_only": True,
                            "execution_authority": False,
                        }
                    except Exception as exc:
                        entry_payload = {
                            "universe_id": universe.universe_id,
                            "policy_version": stoch_capture.policy_version,
                            "entry_signal_time": stoch_capture.entry_signal_time,
                            "execution_capture_observed_at": stoch_observed_at,
                            "risk_decision": {
                                "allowed": False,
                                "reason_codes": ["STOCH_TREND_EXECUTION_EVIDENCE_ERROR"],
                            },
                            "execution_simulation": None,
                            "detail": f"{type(exc).__name__}: {exc}",
                            "research_only": True,
                            "execution_authority": False,
                        }
                    # Bind idempotency to the signal timestamp. If polling
                    # sees the same armed state more than once, the first
                    # causal capture wins rather than creating duplicates.
                    await monitor._event(
                        strategy_repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="stoch_trend_capture_entry",
                        state="entry_evidence",
                        reason_code="STOCH_TREND_ENTRY_EVIDENCE_CAPTURED",
                        observed_at=stoch_capture.entry_signal_time,
                        payload=entry_payload,
                    )
                    assert stoch_signal_key is not None
                    captured_stoch_entry_signals.add(stoch_signal_key)
                    stoch_entry_payload_by_instrument[
                        candidate.instrument_id
                    ] = entry_payload
                elif (
                    stoch_signal_key is not None
                    and stoch_capture.entry_time is not None
                    and stoch_execution_history_available
                    and stoch_signal_key not in captured_stoch_entry_signals
                ):
                    # The monitor first observed this signal only after the
                    # next 3m bar was already finalized. Never backfill the
                    # entry with a later quote: persist an explicit
                    # fail-closed evidence record so reconstructed returns
                    # cannot masquerade as prospectively executable trades.
                    await monitor._event(
                        strategy_repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="stoch_trend_capture_entry",
                        state="entry_evidence",
                        reason_code="STOCH_TREND_ENTRY_EVIDENCE_CAPTURED",
                        observed_at=cast(datetime, stoch_capture.entry_signal_time),
                        payload={
                            "universe_id": universe.universe_id,
                            "policy_version": stoch_capture.policy_version,
                            "entry_signal_time": stoch_capture.entry_signal_time,
                            "entry_time": stoch_capture.entry_time,
                            "execution_capture_observed_at": stoch_observed_at,
                            "execution_capture_lag_seconds": None,
                            "risk_decision": {
                                "allowed": False,
                                "reason_codes": ["STOCH_TREND_ENTRY_EVIDENCE_MISSED"],
                            },
                            "execution": None,
                            "execution_simulation": None,
                            "detail": (
                                "No point-in-time execution observation was captured "
                                "before the next finalized 3m entry bar."
                            ),
                            "research_only": True,
                            "execution_authority": False,
                        },
                    )
                    captured_stoch_entry_signals.add(stoch_signal_key)
                    stoch_entry_payload_by_instrument[candidate.instrument_id] = {
                        "universe_id": universe.universe_id,
                        "policy_version": stoch_capture.policy_version,
                        "entry_signal_time": stoch_capture.entry_signal_time,
                        "entry_time": stoch_capture.entry_time,
                        "execution_capture_observed_at": stoch_observed_at,
                        "execution_capture_lag_seconds": None,
                        "risk_decision": {
                            "allowed": False,
                            "reason_codes": ["STOCH_TREND_ENTRY_EVIDENCE_MISSED"],
                        },
                        "execution": None,
                        "execution_simulation": None,
                        "detail": (
                            "No point-in-time execution observation was captured "
                            "before the next finalized 3m entry bar."
                        ),
                        "research_only": True,
                        "execution_authority": False,
                    }

        if config.config.stoch_trend_capture_enabled and stoch_capture is not None:
            execution_action = stoch_execution_action_for_snapshot(stoch_capture)
            if execution_action is not None and execution_action[0] != "entry":
                action, action_time = execution_action
                action_key = (
                    candidate.instrument_id,
                    action,
                    action_time.astimezone(timezone.utc).isoformat(),
                )
                if (
                    stoch_execution_history_available
                    and action_key not in captured_stoch_execution_actions
                ):
                    try:
                        action_evidence = await asyncio.to_thread(
                            observe_shadow_execution,
                            market_service,
                            instrument_id=candidate.instrument_id,
                            binding_id=candidate.binding_id,
                        )
                        action_simulation = simulate_stoch_execution(
                            action_evidence.execution,
                            action=action,
                            instrument_id=candidate.instrument_id,
                            binding_id=candidate.binding_id,
                            decision_at=action_time,
                            requested_fraction=stoch_requested_fraction_for_action(
                                action,
                                stoch_capture,
                            ),
                            reference_price=(
                                stoch_capture.runner_exit_price
                                if action == "force_flat"
                                else None
                            ),
                        )
                        action_payload = {
                            "universe_id": universe.universe_id,
                            "policy_version": stoch_capture.policy_version,
                            "action": action,
                            "decision_at": action_time,
                            "execution": action_evidence.execution,
                            "execution_simulation": action_simulation.model_dump(
                                mode="json"
                            ),
                            "research_only": True,
                            "execution_authority": False,
                        }
                    except Exception as exc:
                        action_payload = {
                            "universe_id": universe.universe_id,
                            "policy_version": stoch_capture.policy_version,
                            "action": action,
                            "decision_at": action_time,
                            "execution": None,
                            "execution_simulation": None,
                            "detail": f"{type(exc).__name__}: {exc}",
                            "research_only": True,
                            "execution_authority": False,
                        }
                    await monitor._event(
                        strategy_repository,
                        config,
                        instrument_id=candidate.instrument_id,
                        event_type="stoch_trend_execution",
                        state=action,
                        reason_code="STOCH_EXECUTION_ACTION_CAPTURED",
                        observed_at=action_time,
                        payload=action_payload,
                    )
                    captured_stoch_execution_actions.add(action_key)
                    stoch_action_payloads_by_instrument.setdefault(
                        candidate.instrument_id,
                        {},
                    )[action] = action_payload

            missed_actions: list[tuple[StochExecutionAction, datetime]] = []
            if (
                stoch_capture.state == "range_exited"
                and stoch_capture.first_overbought_time is not None
            ):
                missed_actions.append(
                    ("range_exit", stoch_capture.first_overbought_time)
                )
            if (
                stoch_capture.partial_exit_time is not None
                and stoch_capture.first_overbought_time is not None
            ):
                missed_actions.append(
                    ("partial_exit", stoch_capture.first_overbought_time)
                )
            if (
                stoch_capture.state == "trend_exited"
                and stoch_capture.trend_break_time is not None
            ):
                missed_actions.append(
                    ("runner_exit", stoch_capture.trend_break_time)
                )

            for missed_action, missed_time in missed_actions:
                missed_key = (
                    candidate.instrument_id,
                    missed_action,
                    missed_time.astimezone(timezone.utc).isoformat(),
                )
                if (
                    not stoch_execution_history_available
                    or missed_key in captured_stoch_execution_actions
                ):
                    continue
                missed_payload = {
                    "universe_id": universe.universe_id,
                    "policy_version": stoch_capture.policy_version,
                    "action": missed_action,
                    "decision_at": missed_time,
                    "execution": None,
                    "execution_simulation": None,
                    "detail": (
                        "No point-in-time bid/ask observation was captured "
                        "while this Stoch action was actionable."
                    ),
                    "research_only": True,
                    "execution_authority": False,
                }
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="stoch_trend_execution",
                    state=missed_action,
                    reason_code="STOCH_EXECUTION_EVIDENCE_MISSED",
                    observed_at=missed_time,
                    payload=missed_payload,
                )
                captured_stoch_execution_actions.add(missed_key)
                stoch_action_payloads_by_instrument.setdefault(
                    candidate.instrument_id,
                    {},
                )[missed_action] = missed_payload

            if stoch_capture.state in {"range_exited", "trend_exited", "force_flat"}:
                summary = build_stoch_execution_summary(
                    stoch_capture,
                    entry_payload=stoch_entry_payload_by_instrument.get(
                        candidate.instrument_id
                    ),
                    action_payloads=stoch_action_payloads_by_instrument.get(
                        candidate.instrument_id,
                        {},
                    ),
                )
                summary_at = (
                    stoch_capture.runner_exit_time
                    or stoch_capture.as_of
                    or stoch_observed_at
                )
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="stoch_trend_execution_summary",
                    state="complete" if summary.complete else "incomplete",
                    reason_code=summary.reason_code,
                    observed_at=summary_at,
                    payload={
                        "universe_id": universe.universe_id,
                        "policy": summary.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                )

        if config.config.intraday_learning_enabled:
            try:
                learning = build_intraday_learning_snapshot(candidate, result, base_bars)
            except Exception as exc:
                trade_log(
                    "auto_trading",
                    "intraday_learning_snapshot_error",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    universe_id=universe.universe_id,
                    instrument_id=candidate.instrument_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
            else:
                learning_rows.append((candidate, result, base_bars[-1].end_time, learning))
        if result.state == "entry_ready" and result.signal is not None:
            monitor.signal_count += 1
            if config.config.strategy_version == "1.2.0":
                try:
                    research_decision = await asyncio.to_thread(
                        resolve_strategy_research_policy,
                        strategy_version=config.config.strategy_version,
                        instrument_id=candidate.instrument_id,
                        decision_at=observed_at,
                    )
                except Exception as exc:
                    research_decision = None
                    reason_code = "RESEARCH_POLICY_RESOLUTION_ERROR"
                    detail: str | None = f"{type(exc).__name__}: {exc}"
                else:
                    quality_gate = apply_research_policy_to_quality(
                        cast(ResearchPolicyDecision, research_decision),
                        base_quality_score=result.features.quality_score,
                        minimum_quality_score=config.config.minimum_quality_score,
                        score_adjustment_enabled=config.config.research_score_adjustment_enabled,
                    )
                    reason_code = quality_gate.reason_code
                    detail = None
                allowed = quality_gate.allowed if research_decision is not None else False
                if allowed and research_decision is not None and result.signal is not None:
                    adjusted_quality = quality_gate.adjusted_quality_score
                    result = result.model_copy(update={
                        "features": result.features.model_copy(update={"quality_score": adjusted_quality}),
                        "signal": result.signal.model_copy(update={"quality_score": adjusted_quality}),
                    })
                payload = {
                    "strategy_version": config.config.strategy_version,
                    "policy_version": (
                        research_decision.policy_version if research_decision is not None else "trading-research-1"
                    ),
                    "authoritative": True,
                    "allowed": allowed,
                    "score_adjustment": (
                        quality_gate.score_adjustment if research_decision is not None else 0
                    ),
                    "base_quality_score": (
                        quality_gate.base_quality_score if research_decision is not None else result.features.quality_score
                    ),
                    "adjusted_quality_score": (
                        quality_gate.adjusted_quality_score if research_decision is not None else result.features.quality_score
                    ),
                    "minimum_quality_score": config.config.minimum_quality_score,
                    "proposed_score_adjustment": (
                        quality_gate.proposed_score_adjustment if research_decision is not None else 0
                    ),
                    "score_adjustment_enabled": config.config.research_score_adjustment_enabled,
                    "detail": detail,
                    "decision_at": observed_at,
                }
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="research_policy",
                    state="entry_ready" if allowed else "rejected",
                    reason_code=reason_code,
                    observed_at=observed_at,
                    payload=payload,
                )
                trade_log(
                    "auto_trading",
                    "research_policy_decision",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    instrument_id=candidate.instrument_id,
                    **payload,
                    reason_code=reason_code,
                )
                if not allowed:
                    monitor.rejection_count += 1
                    continue
            proposals.append(
                _EntryProposal(
                    candidate=candidate,
                    result=result,
                    observed_at=observed_at,
                )
            )
    if learning_rows:
        ranked_learning = sorted(
            learning_rows,
            key=lambda row: (
                -row[3].execution_adjusted_opportunity_score,
                -row[3].raw_movement_score,
                -row[3].execution_quality_score,
                row[0].discovery_rank or 10**9,
                row[0].instrument_id,
            ),
        )
        for rank, (candidate, result, observed_at, learning) in enumerate(ranked_learning, start=1):
            persisted = await monitor._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="intraday_learning",
                state=learning.pattern,
                reason_code="INTRADAY_LEARNING_SNAPSHOT",
                observed_at=observed_at,
                payload={
                    "rank": rank,
                    "universe_id": universe.universe_id,
                    "universe_discovery_source": universe.discovery_source,
                    "morning_discovery_rank": candidate.discovery_rank,
                    "strategy_version": config.config.strategy_version,
                    "deterministic_state": result.state,
                    "deterministic_reason_code": result.reason_code,
                    "learning": learning.model_dump(mode="json"),
                    "research_only": True,
                    "execution_authority": False,
                },
            )
            monitor.intraday_learning_snapshot_count += int(persisted)
        trade_log(
            "auto_trading",
            "intraday_learning_ranked",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            universe_id=universe.universe_id,
            candidate_count=len(ranked_learning),
            ranks=[
                {
                    "instrument_id": row[0].instrument_id,
                    "rank": index,
                    "pattern": row[3].pattern,
                    "opportunity_score": row[3].opportunity_score,
                    "raw_movement_score": row[3].raw_movement_score,
                    "execution_adjusted_opportunity_score": row[3].execution_adjusted_opportunity_score,
                    "squeeze_probability_score": row[3].squeeze_probability_score,
                    "failed_selloff_probability_score": row[3].failed_selloff_probability_score,
                    "trend_continuation_score": row[3].trend_continuation_score,
                    "gap_retention_score": row[3].gap_retention_score,
                }
                for index, row in enumerate(ranked_learning, start=1)
            ],
            execution_authority=False,
        )

        # Research annotation: proposals never wait on the LLM (WP-8.3).
        monitor.intraday_llm_annotations.start(config.strategy_id, lambda: monitor._run_intraday_llm(
            config, strategy_repository, universe, list(ranked_learning)))

    proposals.sort(key=lambda proposal: proposal.priority)
    if evaluated_any or proposals:
        trade_log(
            "auto_trading",
            "candidate_arbitration",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            universe_id=universe.universe_id,
            proposal_count=len(proposals),
            proposals=[
                {
                    "instrument_id": proposal.candidate.instrument_id,
                    "observed_at": proposal.observed_at,
                    "quality_score": (
                        proposal.result.signal.quality_score
                        if proposal.result.signal is not None
                        else proposal.result.features.quality_score
                    ),
                    "discovery_rank": proposal.candidate.discovery_rank,
                    "priority": proposal.priority,
                }
                for proposal in proposals
            ],
        )
    if isinstance(universe, GapperUniverseSnapshot):
        await monitor._record_diagnostic_v2_candidates(
            config,
            strategy_repository,
            market_service,
            universe,
        )
    if hasattr(universe, "session_date") and hasattr(universe, "candidates"):
        await _session_evidence._collect_trend_signal(
            monitor,
            config,
            strategy_repository,
            market_service,
            universe,
            now=now,
        )
    return proposals
