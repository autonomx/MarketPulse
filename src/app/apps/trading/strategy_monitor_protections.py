"""Protective-order reconciliation for the strategy monitor (moved out of the class)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from app.apps.trading.us_equity_calendar import EASTERN as _ET

from .binding_authority import require_execution_binding
from .paper import PaperOrderRequest, paper_protection_trigger
from .service import TradingMarketDataService

# The monitor imports this module lazily, so importing it here makes no cycle.
from .strategy_monitor import (
    StrategyRunHost,
    _execution_audit_payload,
    _key,
    _paper_observation,
    _rsi_crossed_after_activation,
)
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_v2_management import (
    v2_active_stop_for_prior_high,
    v2_hold_expired,
    v2_initial_stop_from_target,
    v2_management_levels,
)
from .trade_logging import trade_log

if TYPE_CHECKING:
    from .order_gateway import StrategyPaperAccess


async def reconcile_protections(
    monitor: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    paper_repository: StrategyPaperAccess,
    market_service: TradingMarketDataService,
) -> None:
    protections = await asyncio.to_thread(
        strategy_repository.list_protections,
        config.strategy_id,
        active_only=True,
    )
    if not protections:
        return
    snapshot = await asyncio.to_thread(paper_repository.snapshot, config.account_id)
    history = {order.order_id: order for order in snapshot.order_history}
    positions = {position.instrument_id: position for position in snapshot.positions}
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    force_flat = now_et.time() >= config.risk.force_flat_et
    trade_log(
        "auto_trading",
        "protection_reconcile_start",
        run_id=monitor.current_run_id,
        strategy_id=config.strategy_id,
        account_id=config.account_id,
        protection_count=len(protections),
        force_flat=force_flat,
        force_flat_et=config.risk.force_flat_et,
    )
    for protection in protections:
        entry_order = history.get(protection.entry_order_id)
        if protection.status == "pending_entry":
            if entry_order is not None and entry_order.status == "filled":
                position = positions.get(protection.instrument_id)
                if position is not None and position.quantity > 0:
                    activated_at: datetime | None = entry_order.updated_at or entry_order.created_at or datetime.now(timezone.utc)
                    fill_price = entry_order.average_fill_price
                    if protection.initial_stop_price is None:
                        protection.initial_stop_price = protection.stop_price
                    if fill_price is not None:
                        protection.mae_price = fill_price if protection.mae_price is None else min(protection.mae_price, fill_price)
                        protection.mfe_price = fill_price if protection.mfe_price is None else max(protection.mfe_price, fill_price)
                    if config.config.strategy_version == "2.0.0":
                        if fill_price is None:
                            monitor.last_error = "v2_protection: filled entry missing average_fill_price"
                            trade_log(
                                "auto_trading",
                                "v2_protection_activation_deferred",
                                run_id=monitor.current_run_id,
                                strategy_id=config.strategy_id,
                                instrument_id=protection.instrument_id,
                                entry_order_id=entry_order.order_id,
                                reason="missing_average_fill_price",
                            )
                            continue
                        try:
                            levels = v2_management_levels(
                                config.config,
                                entry_price=fill_price,
                                initial_stop=protection.stop_price,
                            )
                        except ValueError as exc:
                            monitor.last_error = f"v2_protection: {exc}"
                            trade_log(
                                "auto_trading",
                                "v2_protection_activation_deferred",
                                run_id=monitor.current_run_id,
                                strategy_id=config.strategy_id,
                                instrument_id=protection.instrument_id,
                                entry_order_id=entry_order.order_id,
                                reason="invalid_fill_anchored_risk",
                                detail=str(exc),
                            )
                            continue
                        # Match the V11 backtester: keep the structural L2 stop,
                        # but anchor R/target to the actual pessimistic fill.
                        protection.target_price = levels.target_price
                        protection.initial_target_price = levels.target_price
                    elif protection.initial_target_price is None:
                        protection.initial_target_price = protection.target_price
                    protection.status = "active"
                    protection.quantity = min(protection.quantity, position.quantity)
                    saved = await asyncio.to_thread(strategy_repository.save_protection, protection)
                    if config.config.strategy_version == "2.0.0":
                        await monitor._event(
                            strategy_repository,
                            config,
                            instrument_id=protection.instrument_id,
                            event_type="protection",
                            state="active",
                            reason_code="V2_PROTECTION_ANCHORED_TO_FILL",
                            observed_at=cast(datetime, activated_at),
                            payload={
                                "entry_fill_price": str(entry_order.average_fill_price),
                                "initial_stop": str(saved.stop_price),
                                "target_price": str(saved.target_price),
                                "reward_multiple": str(config.config.reward_multiple),
                                "profit_protection_trigger_r": str(config.config.v2_profit_protection_trigger_r),
                                "protected_stop_r": str(config.config.v2_protected_stop_r),
                                "max_hold_minutes": config.config.v2_max_hold_minutes,
                            },
                        )
            elif entry_order is not None and entry_order.status in {"rejected", "cancelled"}:
                protection.status = "cancelled"
                protection.trigger_reason = f"entry_{entry_order.status}"
                await asyncio.to_thread(strategy_repository.save_protection, protection)
            continue

        if protection.status == "exit_submitted":
            exit_order = history.get(protection.exit_order_id or "")
            if exit_order is not None and exit_order.status == "filled":
                protection.status = "closed"
                await asyncio.to_thread(strategy_repository.save_protection, protection)
            elif exit_order is not None and exit_order.status in {"rejected", "cancelled"}:
                protection.status = "active"
                protection.exit_order_id = None
                protection.trigger_reason = f"exit_{exit_order.status}_retry"
                await asyncio.to_thread(strategy_repository.save_protection, protection)
            continue

        if protection.status != "active":
            continue
        position = positions.get(protection.instrument_id)
        if position is None or position.quantity <= 0:
            protection.status = "closed"
            protection.trigger_reason = "position_closed"
            await asyncio.to_thread(strategy_repository.save_protection, protection)
            continue

        conflicting_exit = any(
            order.status == "open"
            and order.instrument_id == protection.instrument_id
            and order.side == "sell"
            for order in snapshot.open_orders
        )
        if conflicting_exit:
            trade_log(
                "auto_trading",
                "protection_exit_skipped",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=protection.instrument_id,
                protection_id=protection.protection_id,
                reason="conflicting_open_sell_order",
            )
            continue

        historical_binding_id = (
            entry_order.binding_id if entry_order is not None else None
        )
        binding_id = historical_binding_id
        binding_rebound = False
        try:
            binding_id = require_execution_binding(binding_id)
        except ValueError:
            # Historical/replay research provenance is not execution
            # authority. Resolve the current execution binding independently.
            binding_id = None
            binding_rebound = True
        try:
            execution = await asyncio.to_thread(
                market_service.execution_observation,
                protection.instrument_id,
                binding_id,
            )
        except Exception as exc:
            if binding_rebound:
                protection.status = "quarantined"
                protection.trigger_reason = "execution_binding_resolution_failed"
                protection = await asyncio.to_thread(
                    strategy_repository.save_protection,
                    protection,
                )
                await monitor._event(
                    strategy_repository,
                    config,
                    instrument_id=protection.instrument_id,
                    event_type="protection",
                    state="quarantined",
                    reason_code="PROTECTION_EXECUTION_BINDING_UNRESOLVED",
                    observed_at=datetime.now(timezone.utc),
                    payload={
                        "protection_id": protection.protection_id,
                        "entry_order_id": protection.entry_order_id,
                        "historical_binding_id": historical_binding_id,
                        "detail": f"{type(exc).__name__}: {exc}",
                        "execution_authority": False,
                    },
                )
                continue
            monitor.last_error = f"protection_data: {type(exc).__name__}: {exc}"
            trade_log(
                "auto_trading",
                "protection_execution_error",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=protection.instrument_id,
                protection_id=protection.protection_id,
                error_type=type(exc).__name__,
                detail=str(exc),
            )
            continue
        if binding_rebound:
            await monitor._event(
                strategy_repository,
                config,
                instrument_id=protection.instrument_id,
                event_type="protection",
                state="active",
                reason_code="PROTECTION_EXECUTION_BINDING_REBOUND",
                observed_at=datetime.now(timezone.utc),
                payload={
                    "protection_id": protection.protection_id,
                    "entry_order_id": protection.entry_order_id,
                    "historical_binding_id": historical_binding_id,
                    "resolved_binding_id": execution.binding_id,
                    "execution_authority": True,
                },
            )
        if not execution.execution_eligible:
            trade_log(
                "auto_trading",
                "protection_execution_ineligible",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                instrument_id=protection.instrument_id,
                protection_id=protection.protection_id,
                execution=_execution_audit_payload(execution),
            )
            continue
        trigger = None
        activated_at = None
        if entry_order is not None:
            activated_at = entry_order.updated_at or entry_order.created_at

        if activated_at is not None and entry_order is not None and entry_order.average_fill_price is not None:
            mark = execution.last or execution.bid or execution.ask
            if mark is not None:
                range_is_post_entry = (
                    execution.bar_start_time is not None
                    and execution.bar_start_time >= activated_at
                )
                observed_low = execution.low if range_is_post_entry and execution.low is not None else mark
                observed_high = execution.high if range_is_post_entry and execution.high is not None else mark
                next_mae = observed_low if protection.mae_price is None else min(protection.mae_price, observed_low)
                next_mfe = observed_high if protection.mfe_price is None else max(protection.mfe_price, observed_high)
                if next_mae != protection.mae_price or next_mfe != protection.mfe_price:
                    protection.mae_price = next_mae
                    protection.mfe_price = next_mfe
                    protection = await asyncio.to_thread(strategy_repository.save_protection, protection)

        if (
            config.config.strategy_version == "2.0.0"
            and entry_order is not None
            and entry_order.average_fill_price is not None
            and activated_at is not None
        ):
            entry_price = entry_order.average_fill_price
            try:
                initial_stop = protection.initial_stop_price or v2_initial_stop_from_target(
                    config.config,
                    entry_price=entry_price,
                    target_price=protection.target_price,
                )
                recovery_method = getattr(market_service, "recovered_bars", None)
                if callable(recovery_method):
                    response = await asyncio.to_thread(
                        recovery_method,
                        protection.instrument_id,
                        "1m",
                        240,
                        binding_id,
                        session_date=execution.source_time.astimezone(_ET).date(),
                        as_of=execution.source_time,
                        knowledge_mode="live",
                    )
                    protection_bars = list(response.bars)
                else:
                    legacy_response = await asyncio.to_thread(
                        market_service.bars,
                        protection.instrument_id,
                        "1m",
                        240,
                        binding_id,
                    )
                    protection_bars = list(legacy_response.bars)
                finalized = [
                    bar
                    for bar in protection_bars
                    if bar.is_final
                    and bar.end_time > activated_at
                    and bar.end_time <= execution.source_time
                ]
                prior_high = max(
                    [entry_price, *(bar.high for bar in finalized)],
                )
                desired_stop = v2_active_stop_for_prior_high(
                    config.config,
                    entry_price=entry_price,
                    initial_stop=initial_stop,
                    prior_finalized_high=prior_high,
                )
                if desired_stop > protection.stop_price:
                    old_stop = protection.stop_price
                    protection.stop_price = desired_stop
                    protection.trigger_reason = "profit_protection_armed"
                    protection = await asyncio.to_thread(
                        strategy_repository.save_protection, protection
                    )
                    await monitor._event(
                        strategy_repository,
                        config,
                        instrument_id=protection.instrument_id,
                        event_type="protection",
                        state="active",
                        reason_code="V2_PROFIT_PROTECTION_ARMED",
                        observed_at=execution.source_time,
                        payload={
                            "old_stop": str(old_stop),
                            "new_stop": str(protection.stop_price),
                            "entry_fill_price": str(entry_price),
                            "prior_finalized_high": str(prior_high),
                            "trigger_r": str(config.config.v2_profit_protection_trigger_r),
                            "protected_stop_r": str(config.config.v2_protected_stop_r),
                            "finalized_bar_count_since_entry": len(finalized),
                        },
                    )
            except Exception as exc:
                # Static structural protection remains active if the optional
                # profit-protection evidence cannot be refreshed this cycle.
                monitor.last_error = f"v2_protection_management: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "v2_protection_management_error",
                    run_id=monitor.current_run_id,
                    strategy_id=config.strategy_id,
                    instrument_id=protection.instrument_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                )

        if config.config.strategy_version != "2.0.0" and force_flat:
            # Preserve the original 1.x contract exactly: once force-flat
            # time is reached, EOD liquidation wins over stop/target checks.
            trigger = "force_flat"
        else:
            trigger_kind = paper_protection_trigger(
                is_long=True,
                stop_price=protection.stop_price,
                target_price=protection.target_price,
                observation=_paper_observation(execution),
                activated_at=activated_at,
            )
            if trigger_kind == "stop":
                trigger = "protective_stop"
            elif trigger_kind == "target":
                trigger = "profit_target"

            if trigger is None and activated_at is not None:
                try:
                    recovery_method = getattr(market_service, "recovered_bars", None)
                    if callable(recovery_method):
                        indicator_response = await asyncio.to_thread(
                            recovery_method,
                            protection.instrument_id,
                            config.config.execution_interval,
                            240,
                            binding_id,
                            session_date=execution.source_time.astimezone(_ET).date(),
                            as_of=execution.source_time,
                            knowledge_mode="live",
                        )
                        indicator_bars = list(indicator_response.bars)
                    else:
                        legacy_indicator_response = await asyncio.to_thread(
                            market_service.bars,
                            protection.instrument_id,
                            config.config.execution_interval,
                            240,
                            binding_id,
                        )
                        indicator_bars = list(legacy_indicator_response.bars)
                    if _rsi_crossed_after_activation(
                        indicator_bars,
                        period=config.config.exit_rsi_period,
                        threshold=config.config.exit_rsi_threshold,
                        activated_at=activated_at,
                        observed_at=execution.source_time,
                    ):
                        trigger = "rsi"
                except Exception as exc:
                    # Indicator refresh is diagnostic/fail-safe only: static
                    # stop/target and force-flat protection remain authoritative.
                    monitor.last_error = f"protection_rsi: {type(exc).__name__}: {exc}"
                    trade_log(
                        "auto_trading",
                        "protection_rsi_error",
                        run_id=monitor.current_run_id,
                        strategy_id=config.strategy_id,
                        instrument_id=protection.instrument_id,
                        error_type=type(exc).__name__,
                        detail=str(exc),
                    )

            if (
                trigger is None
                and config.config.strategy_version == "2.0.0"
                and activated_at is not None
                and v2_hold_expired(
                    config.config,
                    activated_at=activated_at,
                    observed_at=execution.source_time,
                )
            ):
                trigger = "max_hold"
            elif trigger is None and force_flat:
                trigger = "force_flat"
        if trigger is None:
            continue
        quantity = min(protection.quantity, position.quantity)
        order_id = f"exit-{protection.protection_id}"[:200]
        idem = _key(config.strategy_id, protection.protection_id, trigger)
        trade_log(
            "auto_trading",
            "protection_triggered",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            account_id=config.account_id,
            instrument_id=protection.instrument_id,
            protection_id=protection.protection_id,
            trigger=trigger,
            quantity=quantity,
            stop_price=protection.stop_price,
            target_price=protection.target_price,
            position_quantity=position.quantity,
            execution=_execution_audit_payload(execution),
        )
        try:
            await asyncio.to_thread(
                paper_repository.place_exit,
                config.account_id,
                PaperOrderRequest(
                    order_id=order_id,
                    instrument_id=protection.instrument_id,
                    binding_id=binding_id,
                    side="sell",
                    order_type="market",
                    quantity=quantity,
                    reference_price=execution.bid or execution.last,
                    idempotency_key=idem,
                ),
            )
        except ValueError as exc:
            monitor.last_error = f"protection_order: {exc}"
            trade_log(
                "auto_trading",
                "protection_exit_order_rejected",
                run_id=monitor.current_run_id,
                strategy_id=config.strategy_id,
                account_id=config.account_id,
                instrument_id=protection.instrument_id,
                protection_id=protection.protection_id,
                order_id=order_id,
                trigger=trigger,
                detail=str(exc),
                execution=_execution_audit_payload(execution),
            )
            continue
        protection.exit_order_id = order_id
        protection.status = "exit_submitted"
        protection.trigger_reason = trigger
        await asyncio.to_thread(strategy_repository.save_protection, protection)
        trade_log(
            "auto_trading",
            "protection_exit_order_submitted",
            run_id=monitor.current_run_id,
            strategy_id=config.strategy_id,
            account_id=config.account_id,
            instrument_id=protection.instrument_id,
            protection_id=protection.protection_id,
            order_id=order_id,
            trigger=trigger,
            quantity=quantity,
            reference_price=execution.bid or execution.last,
        )
