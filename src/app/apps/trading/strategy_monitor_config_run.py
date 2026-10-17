"""One strategy configuration's monitor pass (moved out of the class)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_session_settled

from .order_gateway import StrategyPaperAccess, strategy_paper_access
from .paper_repository import TradingPaperRepository
from .service import TradingMarketDataService
from .strategy_data_integrity import assess_universe_integrity
from .strategy_entry_path import submit_entry_proposals

# The monitor imports this module lazily, so importing it here makes no cycle.
from .strategy_monitor import (
    StrategyRunHost,
    _v2_qualification_events,
)
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_shadow_execution import observe_shadow_execution
from .strategy_tape_prefetch import prefetch_candidate_tapes
from .strategy_shadow_universe import (
    resolve_v2_runtime_archive,
)
from .strategy_v2_qualification import (
    evaluate_v2_prospective_qualification,
    v2_profile_fingerprint,
)
from .trade_logging import trade_log


async def run_config(
    monitor: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    raw_paper_repository: TradingPaperRepository,
    market_service: TradingMarketDataService,
) -> None:
    paper_repository: StrategyPaperAccess = strategy_paper_access(
        raw_paper_repository,
        monitor=monitor,
        config=config,
        strategy_repository=strategy_repository,
        market_service=market_service,
    )
    cycle_started_at = datetime.now(timezone.utc)
    log_cycle_heartbeat = monitor._should_log_diagnostic(
        ("strategy_cycle_heartbeat", config.strategy_id),
        cycle_started_at,
    )
    if log_cycle_heartbeat:
        trade_log(
            "auto_trading",
            "strategy_cycle_start",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            account_id=config.account_id,
            strategy_kind=config.strategy_kind,
            strategy_version=config.strategy_version,
            mode=config.mode,
            enabled=config.enabled,
            active_universe_id=config.active_universe_id,
            config=config.config,
            risk_profile=config.risk,
        )
    await monitor._reconcile_protections(
        config,
        strategy_repository,
        paper_repository,
        market_service,
    )
    if config.mode == "off" or not config.enabled:
        if log_cycle_heartbeat:
            trade_log(
                "auto_trading",
                "strategy_cycle_skipped",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                reason="mode_off" if config.mode == "off" else "disabled",
            )
        return

    now_utc = datetime.now(timezone.utc)
    if regular_session_settled(now_utc):
        # The session's bars are final: every later pass would repeat the last one,
        # and its provider requests, until midnight. Protections were reconciled above.
        if config.mode == "auto_paper":
            monitor._set_auto_paper_readiness(
                config,
                state="blocked",
                reason="session_settled",
                observed_at=now_utc,
            )
        if log_cycle_heartbeat:
            trade_log(
                "auto_trading",
                "strategy_cycle_skipped",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                reason="session_settled",
                execution_authority=False,
            )
        return

    if config.strategy_kind == "stoch_rsi_5m_v1":
        await monitor._run_stoch_rsi_5m_config(
            config,
            strategy_repository,
            market_service,
            now_utc=now_utc,
        )
        return

    if config.mode == "auto_paper" and config.config.strategy_version == "2.0.0":
        qualification_events = await asyncio.to_thread(
            _v2_qualification_events,
            strategy_repository,
            config.strategy_id,
            now=now_utc,
        )
        qualification = await asyncio.to_thread(
            evaluate_v2_prospective_qualification,
            config,
            qualification_events,
        )
        if not qualification.auto_paper_authorized:
            monitor._set_auto_paper_readiness(
                config,
                state="blocked",
                reason="qualification_not_authorized",
                observed_at=now_utc,
            )
            if log_cycle_heartbeat:
                trade_log(
                    "auto_trading",
                    "v2_auto_paper_qualification_blocked",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    profile_fingerprint=qualification.current_profile_fingerprint,
                    evidence_fingerprint=qualification.evidence_fingerprint,
                    reason_codes=qualification.reason_codes,
                    matched_eligible_trade_count=qualification.matched_eligible_trade_count,
                    execution_match_rate=qualification.execution_match_rate,
                    expectancy_r=qualification.expectancy_r,
                    one_sided_90_lcb_r=qualification.one_sided_90_lcb_r,
                    max_drawdown_r=qualification.max_drawdown_r,
                    execution_authority=False,
                )
            return
    now_et = now_utc.astimezone(_ET)
    today_et = now_et.date()
    day_start_et = datetime(today_et.year, today_et.month, today_et.day, tzinfo=_ET)
    day_end_et = day_start_et + timedelta(days=1)

    universe_source = "active_universe"
    if config.active_universe_id is not None:
        universe = await asyncio.to_thread(
            strategy_repository.get_universe,
            config.active_universe_id,
        )
    else:
        universe = await asyncio.to_thread(
            resolve_v2_runtime_archive,
            config,
            strategy_repository,
            now=now_utc,
        )
        universe_source = (
            "auto_archive_auto_paper"
            if config.mode == "auto_paper"
            else "auto_archive_shadow"
        )
        if universe is None:
            if config.mode == "auto_paper":
                monitor._set_auto_paper_readiness(
                    config,
                    state="blocked",
                    reason="daily_universe_not_ready",
                    observed_at=now_utc,
                )
            if log_cycle_heartbeat:
                reason = (
                    "v2_auto_paper_archive_not_ready"
                    if config.mode == "auto_paper"
                    and config.config.strategy_version == "2.0.0"
                    else (
                        "v2_shadow_archive_not_ready"
                        if config.mode == "shadow"
                        and config.config.strategy_version == "2.0.0"
                        else "no_active_universe"
                    )
                )
                trade_log(
                    "auto_trading",
                    "strategy_cycle_skipped",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    reason=reason,
                    execution_authority=False,
                )
            return

    integrity = assess_universe_integrity(universe)
    if log_cycle_heartbeat:
        trade_log(
            "auto_trading",
            "universe_loaded",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            universe_id=universe.universe_id,
            runtime_universe_source=universe_source,
            session_date=universe.session_date,
            evaluation_time=universe.evaluation_time,
            discovery_source=universe.discovery_source,
            source_fingerprint=universe.source_fingerprint,
            candidate_count=len(universe.candidates),
            capture_on_time=integrity.capture_on_time,
            cohort_complete=integrity.cohort_complete,
            cohort_integrity=integrity.cohort_integrity,
            market_data_complete=integrity.market_data_complete,
            prospective_eligible=integrity.prospective_eligible,
            integrity_reason_codes=integrity.reason_codes,
        )
    if universe.session_date != today_et:
        monitor._set_auto_paper_readiness(
            config,
            state="blocked",
            reason="universe_session_mismatch",
            observed_at=now_utc,
            universe_id=universe.universe_id,
        )
        rejection_time = day_start_et.astimezone(timezone.utc)
        for candidate in universe.candidates:
            monitor.rejection_count += 1
            await monitor._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="rejection",
                state="rejected",
                reason_code="UNIVERSE_SESSION_MISMATCH",
                observed_at=rejection_time,
                payload={
                    "universe_id": universe.universe_id,
                    "universe_session_date": universe.session_date.isoformat(),
                    "runtime_session_date": today_et.isoformat(),
                },
            )
        return

    if universe.discovery_source == "finviz" and not integrity.prospective_eligible:
        monitor._set_auto_paper_readiness(
            config,
            state="blocked",
            reason=(
                integrity.reason_codes[0]
                if integrity.reason_codes
                else "universe_data_integrity_invalid"
            ),
            observed_at=now_utc,
            universe_id=universe.universe_id,
        )
        await monitor._event(
            strategy_repository,
            config,
            instrument_id="__universe__",
            event_type="universe_integrity",
            state="invalid",
            reason_code=(
                integrity.reason_codes[0]
                if integrity.reason_codes
                else "UNIVERSE_DATA_INTEGRITY_INVALID"
            ),
            observed_at=universe.evaluation_time,
            payload={
                "universe_id": universe.universe_id,
                **integrity.model_dump(mode="json"),
                "research_only": True,
                "execution_authority": False,
            },
        )
        return

    monitor._set_auto_paper_readiness(
        config,
        state="ready",
        reason="qualified_daily_universe_ready",
        observed_at=now_utc,
        universe_id=universe.universe_id,
    )

    await prefetch_candidate_tapes(market_service, universe, interval="1m", as_of=now_utc)
    proposals = await monitor._evaluate_candidates(
        config,
        strategy_repository,
        market_service,
        universe,
    )
    if not await monitor._proposals_evaluated(config, strategy_repository, proposals):
        return
    if config.mode == "shadow" and proposals:
        for proposal in proposals:
            candidate = proposal.candidate
            result = proposal.result
            assert result.signal is not None
            try:
                evidence = await asyncio.to_thread(
                    observe_shadow_execution,
                    market_service,
                    instrument_id=candidate.instrument_id,
                    binding_id=candidate.binding_id,
                )
            except Exception as exc:
                payload = {
                    "strategy_version": config.config.strategy_version,
                    "mode": "shadow",
                    "universe_id": universe.universe_id,
                    "universe_source": universe_source,
                    "profile_fingerprint": (
                        v2_profile_fingerprint(config.config)
                        if config.config.strategy_version == "2.0.0"
                        else None
                    ),
                    "signal": result.signal.model_dump(mode="json"),
                    "features": result.features.model_dump(mode="json"),
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                    "execution_authority": False,
                }
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="shadow_execution",
                    state=result.state,
                    reason_code="SHADOW_EXECUTION_UNAVAILABLE",
                    observed_at=proposal.observed_at,
                    payload=payload,
                )
                trade_log(
                    "auto_trading",
                    "shadow_execution_unavailable",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    instrument_id=candidate.instrument_id,
                    **payload,
                )
                continue

            payload = {
                "strategy_version": config.config.strategy_version,
                "mode": "shadow",
                "universe_id": universe.universe_id,
                "universe_source": universe_source,
                "profile_fingerprint": (
                    v2_profile_fingerprint(config.config)
                    if config.config.strategy_version == "2.0.0"
                    else None
                ),
                "signal": result.signal.model_dump(mode="json"),
                "features": result.features.model_dump(mode="json"),
                "execution": evidence.execution,
                "execution_authority": False,
            }
            await monitor._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="shadow_execution",
                state=result.state,
                reason_code=evidence.reason_code,
                observed_at=proposal.observed_at,
                payload=payload,
            )
            trade_log(
                "auto_trading",
                "shadow_execution_observation",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                reason_code=evidence.reason_code,
                **payload,
            )

        trade_log(
            "auto_trading",
            "strategy_cycle_no_entry_work",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            mode=config.mode,
            proposal_count=len(proposals),
            shadow_execution_observed=True,
        )
        return

    if config.mode != "auto_paper" or not proposals:
        trade_log(
            "auto_trading",
            "strategy_cycle_no_entry_work",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            mode=config.mode,
            proposal_count=len(proposals),
        )
        return

    await submit_entry_proposals(
        monitor,
        config,
        strategy_repository,
        paper_repository,
        market_service,
        proposals,
        universe=universe,
        now_utc=now_utc,
        today_et=today_et,
        day_start_et=day_start_et,
        day_end_et=day_end_et,
    )
