"""Submitting a strategy configuration's entry proposals (strategy runner WP).

The strategy monitor and the strategy runner both send entries through this
one path: one trade attempt per proposal, the strategy's risk sizing, a
``risk_decision`` event, protection armed before the order can execute, and
the order placed through ``StrategyPaperAccess`` (the order gateway: entry
authorization, kill switches, daily loss and long-only checks). ``host`` is
the ``StrategyRunHost`` running the configuration.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.apps.trading.us_equity_calendar import EASTERN as _ET

from .paper import PaperOrderRequest
from .strategy_monitor import (
    StrategyRunHost,
    _execution_audit_payload,
    _key,
    _trade_attempt_id,
)
from .strategy_repository import StrategyProtection
from .strategy_risk import size_strategy_entry
from .strategy_v2_qualification import v2_profile_fingerprint
from .trade_logging import trade_log

if TYPE_CHECKING:
    from .order_gateway import StrategyPaperAccess
    from .service import TradingMarketDataService
    from .strategy_repository import (
        TradingStrategyConfigDocument,
        TradingStrategyRepository,
    )


async def submit_entry_proposals(
    host: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    paper_repository: StrategyPaperAccess,
    market_service: TradingMarketDataService,
    proposals: list[Any],
    *,
    universe: Any,
    now_utc: datetime,
    today_et: date,
    day_start_et: datetime,
    day_end_et: datetime,
) -> None:
    snapshot = await asyncio.to_thread(paper_repository.snapshot, config.account_id)
    if hasattr(strategy_repository, "entry_events_between"):
        entry_events = await asyncio.to_thread(
            strategy_repository.entry_events_between,
            config.strategy_id,
            start_time=day_start_et,
            end_time=day_end_et,
        )
    else:
        recent_events = await asyncio.to_thread(
            strategy_repository.recent_events,
            config.strategy_id,
            500,
        )
        entry_events = [
            event
            for event in recent_events
            if event.event_type == "entry_order_submitted"
            and event.observed_at.astimezone(_ET).date() == today_et
        ]
    trades_today = len(entry_events)
    traded_symbols = {event.instrument_id for event in entry_events}
    submitted_attempts = {
        str(event.payload.get("trade_attempt_id"))
        if event.payload.get("trade_attempt_id")
        else _trade_attempt_id(config.strategy_id, event.instrument_id, event.observed_at)
        for event in entry_events
    }

    daily_realized_pnl: Decimal | None = None
    if hasattr(strategy_repository, "daily_paper_pnl"):
        daily_realized_pnl = await asyncio.to_thread(
            strategy_repository.daily_paper_pnl,
            config.account_id,
            start_time=day_start_et,
            end_time=day_end_et,
        )

    protections = await asyncio.to_thread(
        strategy_repository.list_protections,
        config.strategy_id,
        active_only=True,
    )
    protected_symbols = {item.instrument_id for item in protections}
    positions_by_instrument = {position.instrument_id: position for position in snapshot.positions}
    open_risk = Decimal("0")
    for item in protections:
        if item.status not in {"pending_entry", "active"}:
            continue
        position = positions_by_instrument.get(item.instrument_id)
        if (
            config.config.strategy_version == "2.0.0"
            and item.status == "active"
            and position is not None
        ):
            # After V2 profit protection moves above entry, remaining downside
            # risk is zero rather than the old target/stop geometric estimate.
            per_share_risk = max(Decimal("0"), position.average_cost - item.stop_price)
        else:
            per_share_risk = max(
                Decimal("0"),
                (item.target_price - item.stop_price)
                / (config.config.reward_multiple + Decimal("1")),
            )
        open_risk += item.quantity * per_share_risk
    trade_log(
        "auto_trading",
        "portfolio_risk_context",
        run_id=host.current_run_id,
        strategy_id=config.strategy_id,
        account_id=config.account_id,
        trades_today=trades_today,
        traded_symbols=sorted(traded_symbols),
        submitted_attempts=sorted(submitted_attempts),
        protected_symbols=sorted(protected_symbols),
        daily_realized_pnl=daily_realized_pnl,
        open_strategy_risk=open_risk,
        balances=[balance.model_dump(mode="json") for balance in snapshot.balances],
        positions=[position.model_dump(mode="json") for position in snapshot.positions],
        open_orders=[order.model_dump(mode="json") for order in snapshot.open_orders],
    )

    for proposal in proposals:
        candidate = proposal.candidate
        result = proposal.result
        assert result.signal is not None
        observed_at = proposal.observed_at
        trade_attempt_id = _trade_attempt_id(
            config.strategy_id,
            candidate.instrument_id,
            observed_at,
        )
        if trade_attempt_id in submitted_attempts:
            trade_log(
                "auto_trading",
                "candidate_skipped",
                run_id=host.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                trade_attempt_id=trade_attempt_id,
                reason="trade_attempt_already_submitted",
            )
            continue
        if candidate.instrument_id in protected_symbols:
            trade_log(
                "auto_trading",
                "candidate_skipped",
                run_id=host.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                reason="existing_strategy_protection",
            )
            continue
        try:
            execution = await asyncio.to_thread(
                market_service.execution_observation,
                candidate.instrument_id,
                candidate.binding_id,
            )
        except Exception as exc:
            host.rejection_count += 1
            await host._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="rejection",
                state=result.state,
                reason_code="DATA_UNAVAILABLE",
                observed_at=observed_at,
                payload={"detail": str(exc), "trade_attempt_id": trade_attempt_id},
            )
            trade_log(
                "auto_trading",
                "execution_observation_error",
                run_id=host.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                trade_attempt_id=trade_attempt_id,
                error_type=type(exc).__name__,
                detail=str(exc),
            )
            continue
        execution_payload = _execution_audit_payload(execution)
        trade_log(
            "auto_trading",
            "execution_observation",
            run_id=host.current_run_id,
            strategy_id=config.strategy_id,
            instrument_id=candidate.instrument_id,
            trade_attempt_id=trade_attempt_id,
            signal=result.signal,
            execution=execution_payload,
        )
        if not execution.execution_eligible:
            host.rejection_count += 1
            await host._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="rejection",
                state=result.state,
                reason_code="DATA_STALE_OR_INELIGIBLE",
                observed_at=observed_at,
                payload={
                    "trade_attempt_id": trade_attempt_id,
                    "reasons": list(execution.rejection_reasons),
                    "execution": execution_payload,
                },
            )
            continue
        decision = size_strategy_entry(
            snapshot,
            result.signal,
            config.risk,
            spread_bps=execution.spread_bps,
            trades_today=trades_today,
            traded_symbols_today=traded_symbols,
            reserved_instruments=protected_symbols,
            daily_realized_pnl=daily_realized_pnl,
            open_strategy_risk=open_risk,
            observed_at=now_utc,
        )
        decision_payload = decision.model_dump(mode="json")
        profile_fingerprint = (
            v2_profile_fingerprint(config.config)
            if config.config.strategy_version == "2.0.0"
            else config.config.strategy_version
        )
        risk_event_payload = {
            "trade_attempt_id": trade_attempt_id,
            "strategy_version": config.config.strategy_version,
            "profile_fingerprint": profile_fingerprint,
            "universe_id": universe.universe_id,
            "risk_decision": decision_payload,
            "execution": execution_payload,
            "signal": result.signal.model_dump(mode="json"),
            "features": result.features.model_dump(mode="json"),
            "trades_today_before_entry": trades_today,
            "daily_realized_pnl": (
                str(daily_realized_pnl) if daily_realized_pnl is not None else None
            ),
            "open_strategy_risk_before_entry": str(open_risk),
        }
        await host._event(
            strategy_repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="risk_decision",
            state="approved" if decision.allowed else "rejected",
            reason_code=decision.reason_code,
            observed_at=observed_at,
            payload=risk_event_payload,
        )
        trade_log(
            "auto_trading",
            "risk_decision",
            run_id=host.current_run_id,
            strategy_id=config.strategy_id,
            account_id=config.account_id,
            instrument_id=candidate.instrument_id,
            trade_attempt_id=trade_attempt_id,
            observed_at=observed_at,
            trades_today=trades_today,
            daily_realized_pnl=daily_realized_pnl,
            open_strategy_risk=open_risk,
            spread_bps=execution.spread_bps,
            signal=result.signal,
            decision=decision_payload,
        )
        if not decision.allowed:
            host.rejection_count += 1
            await host._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="rejection",
                state=result.state,
                reason_code=decision.reason_code,
                observed_at=observed_at,
                payload={
                    **decision_payload,
                    "trade_attempt_id": trade_attempt_id,
                    "execution": execution_payload,
                    "signal": result.signal.model_dump(mode="json"),
                },
            )
            continue

        order_key = _key(config.strategy_id, trade_attempt_id, "entry")
        order_id = f"strat-{order_key[:32]}"
        protection = StrategyProtection(
            strategy_id=config.strategy_id,
            protection_id=f"prot-{order_key[:32]}",
            account_id=config.account_id,
            instrument_id=candidate.instrument_id,
            entry_order_id=order_id,
            stop_price=result.signal.stop_price,
            target_price=result.signal.target_price,
            initial_stop_price=result.signal.stop_price,
            initial_target_price=(
                None if config.config.strategy_version == "2.0.0"
                else result.signal.target_price
            ),
            quantity=decision.quantity,
            status="pending_entry",
        )
        # Arm protection before the order can become executable. The strategy
        # protection table intentionally does not require the paper order FK,
        # so a crash between these writes is fail-closed rather than exposed.
        await asyncio.to_thread(strategy_repository.save_protection, protection)
        try:
            await asyncio.to_thread(
                paper_repository.place_entry,
                config.account_id,
                PaperOrderRequest(
                    order_id=order_id,
                    instrument_id=candidate.instrument_id,
                    binding_id=candidate.binding_id,
                    side="buy",
                    order_type="market",
                    quantity=decision.quantity,
                    reference_price=execution.ask or execution.last,
                    idempotency_key=order_key,
                ),
                trade_attempt_id=trade_attempt_id,
            )
        except ValueError as exc:
            protection.status = "cancelled"
            protection.trigger_reason = "entry_submit_failed"
            try:
                await asyncio.to_thread(strategy_repository.save_protection, protection)
            except ValueError as cleanup_exc:
                host.last_error = f"entry_protection_cleanup: {cleanup_exc}"
            host.rejection_count += 1
            await host._event(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="rejection",
                state=result.state,
                reason_code="PAPER_ORDER_REJECTED",
                observed_at=observed_at,
                payload={
                    "detail": str(exc),
                    "trade_attempt_id": trade_attempt_id,
                    "order_id": order_id,
                    "execution": execution_payload,
                    "risk_decision": decision_payload,
                    "signal": result.signal.model_dump(mode="json"),
                },
            )
            trade_log(
                "auto_trading",
                "entry_order_rejected",
                run_id=host.current_run_id,
                strategy_id=config.strategy_id,
                account_id=config.account_id,
                instrument_id=candidate.instrument_id,
                trade_attempt_id=trade_attempt_id,
                order_id=order_id,
                detail=str(exc),
                execution=execution_payload,
                risk_decision=decision_payload,
            )
            continue

        await host._event(
            strategy_repository,
            config,
            instrument_id=candidate.instrument_id,
            event_type="entry_order_submitted",
            state="entry_ready",
            reason_code="AUTO_PAPER_ENTRY_SUBMITTED",
            observed_at=observed_at,
            payload={
                "trade_attempt_id": trade_attempt_id,
                "strategy_version": config.config.strategy_version,
                "profile_fingerprint": profile_fingerprint,
                "order_id": order_id,
                "quantity": str(decision.quantity),
                "reference_price": str(execution.ask or execution.last),
                "stop_price": str(result.signal.stop_price),
                "target_price": str(result.signal.target_price),
                "quality_score": result.signal.quality_score,
                "structure_interval": config.config.structure_interval,
                "execution_interval": config.config.execution_interval,
                "priority": [
                    observed_at.astimezone(timezone.utc).isoformat(),
                    -result.signal.quality_score,
                    candidate.discovery_rank if candidate.discovery_rank is not None else 10**9,
                    candidate.instrument_id,
                ],
                "signal": result.signal.model_dump(mode="json"),
                "features": result.features.model_dump(mode="json"),
                "execution": execution_payload,
                "risk_decision": decision_payload,
                "trades_today_before_entry": trades_today,
                "daily_realized_pnl": str(daily_realized_pnl) if daily_realized_pnl is not None else None,
                "open_strategy_risk_before_entry": str(open_risk),
                "universe_id": universe.universe_id,
            },
        )
        trade_log(
            "auto_trading",
            "entry_order_submitted",
            run_id=host.current_run_id,
            strategy_id=config.strategy_id,
            account_id=config.account_id,
            instrument_id=candidate.instrument_id,
            trade_attempt_id=trade_attempt_id,
            order_id=order_id,
            quantity=decision.quantity,
            reference_price=execution.ask or execution.last,
            signal=result.signal,
            features=result.features,
            execution=execution_payload,
            risk_decision=decision_payload,
        )
        host.paper_order_count += 1
        trades_today += 1
        traded_symbols.add(candidate.instrument_id)
        submitted_attempts.add(trade_attempt_id)
        protected_symbols.add(candidate.instrument_id)
        open_risk += decision.estimated_risk
