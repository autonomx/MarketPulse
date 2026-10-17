from __future__ import annotations

from app.config.env import environment

import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .execution import ExecutionObservation
from .order_gateway import OrderGateway
from .providers.request_budget import in_provider_lane
from .paper import (
    PaperAccountSnapshot,
    PaperMarginPosition,
    PaperMarketObservation,
    PaperOrderRequest,
    paper_margin_call_closes,
    paper_margin_status,
    paper_observation_moment,
    paper_order_is_expired,
    paper_price_tick,
    paper_protection_trigger,
    paper_trailing_protection_update,
)
from .paper_protection import PaperPositionProtection
from .paper_protection_repository import (
    TradingPaperProtectionRepository,
    default_paper_protection_repository,
)
from .paper import MARGIN_CALL_ORDER_PREFIX
from .paper_margin_notifications import PaperMarginCallNotifier, default_margin_call_notifier, margin_call_orders
from .paper_repository import TradingPaperRepository
from .paper_runtime_repository import default_runtime_paper_repository
from .service import TradingMarketDataService, default_market_data_service


_MONITOR_STATE_KEY = "_omnix_trading_paper_monitor"
# Orders a margin call places (TVP-7.2b) carry this prefix, so the account shows them as margin calls.


def _uses_margin(snapshot: PaperAccountSnapshot) -> bool:
    """Whether equity can fall below the margin the positions need: leverage, or any short."""
    leveraged = any(margin.long_pct < 100 or margin.short_pct < 100 for margin in snapshot.account.margin.values())
    return leveraged or any(position.quantity < 0 for position in snapshot.positions)


def _env_flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def trading_paper_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _env_flag("OMNIX_TRADING_PAPER_MONITOR_IN_TESTS", "0")
    return _env_flag("OMNIX_TRADING_PAPER_MONITOR", "1")


def _interval_seconds() -> float:
    """Idle account scan cadence; active execution uses a separate fast cadence."""
    try:
        value = float(environment().get("OMNIX_TRADING_PAPER_INTERVAL_SECONDS", "15"))
    except ValueError:
        value = 15.0
    return max(5.0, value)


def _active_interval_seconds() -> float:
    """Fallback polling cadence while any order/protection needs execution evidence."""
    try:
        value = float(environment().get("OMNIX_TRADING_PAPER_ACTIVE_INTERVAL_SECONDS", "1"))
    except ValueError:
        value = 1.0
    return max(0.25, min(5.0, value))


def _protection_key(protection: PaperPositionProtection, trigger: str) -> str:
    raw = (
        f"{protection.account_id}|{protection.instrument_id}|"
        f"{protection.revision}|{trigger}|paper-protection-v1"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _paper_observation(execution: ExecutionObservation) -> PaperMarketObservation:
    return PaperMarketObservation(
        instrument_id=execution.instrument_id,
        binding_id=execution.binding_id,
        provider=execution.provider,
        price=execution.last,
        bid=execution.bid,
        ask=execution.ask,
        bid_size=execution.bid_size,
        ask_size=execution.ask_size,
        high=execution.high,
        low=execution.low,
        volume=execution.bar_volume,
        bar_start_time=execution.bar_start_time,
        source_time=execution.source_time,
        evaluated_at=datetime.now(timezone.utc),
        execution_eligible=execution.execution_eligible,
        freshness_mode=execution.freshness_mode,
        provider_sequence=execution.provider_sequence,
        rejection_reasons=execution.rejection_reasons,
        halted=execution.halted is True,
    )


class TradingPaperMonitor(ScheduledTradingMonitor):
    """Server-authoritative paper execution and OCO protection monitor.

    Idle accounts are scanned conservatively, but once an order or protection is
    active the execution dispatcher switches to a tight polling fallback. This
    intentionally does *not* accelerate strategy signal evaluation: only already
    authorized paper orders/protections consume the fast cadence.
    """

    def __init__(
        self,
        *,
        repository_factory: Callable[[], TradingPaperRepository] = default_runtime_paper_repository,
        protection_repository_factory: Callable[[], TradingPaperProtectionRepository] = default_paper_protection_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        interval_seconds: float | None = None,
        active_interval_seconds: float | None = None,
        margin_call_notifier_factory: Callable[[], PaperMarginCallNotifier] = default_margin_call_notifier,
    ) -> None:
        self.repository_factory = repository_factory
        self.protection_repository_factory = protection_repository_factory
        self.market_service_factory = market_service_factory
        self.margin_call_notifier_factory = margin_call_notifier_factory
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.active_interval_seconds = active_interval_seconds or _active_interval_seconds()
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None
        self.last_execution_observation_at: datetime | None = None
        self.last_observation_age_ms: float | None = None
        self.max_observation_age_ms = 0.0
        self.quote_count = 0
        self.rejected_quote_count = 0
        self.fill_count = 0
        self.protection_trigger_count = 0
        self.active_target_count = 0
        self.active_order_count = 0
        self.active_protection_count = 0
        self.margin_call_count = 0

    def tick_seconds(self) -> float:
        return min(self.interval_seconds, self.active_interval_seconds)

    def current_interval_seconds(self) -> float:
        return self.active_interval_seconds if self.active_target_count else self.interval_seconds

    async def _trail_protection(
        self,
        protection: PaperPositionProtection,
        *,
        is_long: bool,
        observation: PaperMarketObservation,
        activated_at: datetime | None,
        protections: TradingPaperProtectionRepository,
    ) -> None:
        """Move a trailing stop-loss leg behind the best price since activation."""
        if not protection.trailing or protection.stop_loss is None:
            return
        update = paper_trailing_protection_update(
            is_long=is_long,
            stop_loss=protection.stop_loss,
            trail_amount=protection.trail_amount,
            trail_percent=protection.trail_percent,
            water_mark=protection.trail_water_mark,
            observation=observation,
            activated_at=activated_at,
            tick_size=paper_price_tick(protection.instrument_id),
        )
        if update is None:
            return
        water_mark, stop_loss = update
        await asyncio.to_thread(
            protections.trail_stop,
            protection.account_id,
            protection.instrument_id,
            water_mark=water_mark,
            stop_loss=stop_loss,
            expected_revision=protection.revision,
            moved_at=paper_observation_moment(observation) if stop_loss != protection.stop_loss else None,
        )

    @in_provider_lane("protective")
    async def _reconcile_protection(
        self,
        *,
        account_id: str,
        instrument_id: str,
        execution: ExecutionObservation,
        repository: TradingPaperRepository,
        protections: TradingPaperProtectionRepository,
    ) -> None:
        try:
            protection = await asyncio.to_thread(
                protections.get,
                account_id,
                instrument_id,
                include_inactive=False,
            )
        except ValueError:
            return

        snapshot = await asyncio.to_thread(repository.snapshot, account_id)
        history = {order.order_id: order for order in snapshot.order_history}
        position = next(
            (
                item
                for item in snapshot.positions
                if item.instrument_id == instrument_id and item.quantity != 0
            ),
            None,
        )

        if protection.status == "pending_entry":
            if position is not None:
                await asyncio.to_thread(
                    protections.transition,
                    account_id,
                    instrument_id,
                    status="active",
                    exit_order_id=None,
                    trigger_reason="entry_filled",
                    expected_revision=protection.revision,
                )
                return
            entry = history.get(protection.entry_order_id or "")
            if entry is not None and entry.status in {"rejected", "cancelled", "expired"}:
                await asyncio.to_thread(
                    protections.transition,
                    account_id,
                    instrument_id,
                    status="cancelled",
                    exit_order_id=None,
                    trigger_reason=f"entry_{entry.status}",
                    expected_revision=protection.revision,
                )
            return

        if protection.status == "exit_submitted":
            exit_order = history.get(protection.exit_order_id or "")
            if exit_order is not None and exit_order.status == "filled":
                await asyncio.to_thread(
                    protections.transition,
                    account_id,
                    instrument_id,
                    status="closed",
                    exit_order_id=exit_order.order_id,
                    trigger_reason=protection.trigger_reason or "exit_filled",
                    expected_revision=protection.revision,
                )
            elif exit_order is not None and exit_order.status in {"rejected", "cancelled", "expired"}:
                await asyncio.to_thread(
                    protections.transition,
                    account_id,
                    instrument_id,
                    status="active",
                    exit_order_id=None,
                    trigger_reason=f"exit_{exit_order.status}_retry",
                    expected_revision=protection.revision,
                )
            return

        if protection.status != "active":
            return
        if position is None:
            await asyncio.to_thread(
                protections.transition,
                account_id,
                instrument_id,
                status="closed",
                exit_order_id=None,
                trigger_reason="position_closed",
                expected_revision=protection.revision,
            )
            return

        is_long = position.quantity > 0
        close_side = "sell" if is_long else "buy"
        conflicting = any(
            order.instrument_id == instrument_id
            and order.status == "open"
            and order.side == close_side
            for order in snapshot.open_orders
        )

        entry = history.get(protection.entry_order_id or "")
        activated_at = (
            entry.updated_at
            if entry is not None and entry.updated_at is not None
            else protection.updated_at or protection.created_at
        )
        observation = _paper_observation(execution)
        # The trigger is checked against the stop from before this observation;
        # a trailing leg moves only afterwards (the pessimistic order).
        trigger_kind = paper_protection_trigger(
            is_long=is_long,
            stop_price=protection.stop_loss,
            target_price=protection.take_profit,
            observation=observation,
            activated_at=activated_at,
            # Any stop that moved (trailed or edited) is checked from its move.
            stop_moved_at=protection.trail_moved_at,
        )
        if conflicting or trigger_kind is None:
            await self._trail_protection(
                protection,
                is_long=is_long,
                observation=observation,
                activated_at=activated_at,
                protections=protections,
            )
            return
        trigger = "stop_loss" if trigger_kind == "stop" else "take_profit"

        key = _protection_key(protection, trigger)
        order_id = f"paper-protection-{key[:32]}"
        reference = (
            execution.bid if close_side == "sell" else execution.ask
        ) or execution.last
        try:
            await asyncio.to_thread(
                OrderGateway(repository).place_reducing,
                account_id,
                PaperOrderRequest(
                    order_id=order_id,
                    instrument_id=instrument_id,
                    binding_id=protection.binding_id or execution.binding_id,
                    side=cast(Any, close_side),
                    order_type="market",
                    quantity=abs(position.quantity),
                    reference_price=reference,
                    idempotency_key=key,
                ),
            )
        except ValueError as exc:
            self.last_error = f"paper_protection_order: {exc}"
            return

        await asyncio.to_thread(
            protections.transition,
            account_id,
            instrument_id,
            status="exit_submitted",
            exit_order_id=order_id,
            trigger_reason=trigger,
            expected_revision=protection.revision,
        )
        self.protection_trigger_count += 1
        self.wake()

    async def _margin_call(self, account_id: str, prices: dict[str, Any], repository: TradingPaperRepository) -> None:
        """Close positions while equity is below the margin they need (TVP-7.2b): most margin first, only as far as needed.

        Each close is a reducing market order whose id and idempotency key follow from the position it closes, so a
        restart or a second tick places it once; while one is working for an instrument, no other is placed for it.
        """
        snapshot = await asyncio.to_thread(repository.snapshot, account_id)
        base = next((balance for balance in snapshot.balances if balance.currency == snapshot.account.base_currency), None)
        if base is None:
            return
        open_positions = {position.instrument_id: position for position in snapshot.positions if position.quantity != 0}
        margin_positions = [
            PaperMarginPosition(item.instrument_id, item.quantity, item.average_cost, prices.get(item.instrument_id, item.last_price))
            for item in open_positions.values()
        ]
        status = paper_margin_status(snapshot.account, base.available, base.reserved, margin_positions)
        for instrument_id, quantity in paper_margin_call_closes(snapshot.account, status, margin_positions):
            position = open_positions[instrument_id]
            if any(order.instrument_id == instrument_id and order.order_id.startswith(MARGIN_CALL_ORDER_PREFIX) for order in snapshot.open_orders):
                continue
            close_side = "sell" if position.quantity > 0 else "buy"
            # Working orders on the closing side hold the position (sells reserve a long; buys count against a short):
            # cancel the newest of them until the close fits, the margin call taking their place.
            same_side = sorted(
                (order for order in snapshot.open_orders if order.instrument_id == instrument_id and order.side == close_side),
                key=lambda order: (order.created_at or datetime.min.replace(tzinfo=timezone.utc), order.order_id),
                reverse=True,
            )
            working = sum((order.quantity - order.filled_quantity for order in same_side), Decimal("0"))
            free = abs(position.quantity) - (position.reserved_quantity if close_side == "sell" else working)
            for order in same_side:
                if free >= quantity:
                    break
                try:
                    await asyncio.to_thread(OrderGateway(repository).cancel, account_id, order.order_id)
                except ValueError:
                    continue  # filled or cancelled meanwhile
                free += order.quantity - order.filled_quantity
            # Earlier margin calls on the instrument (cancelled, rejected or filled) are part of the key, so a new
            # shortfall after one of them places a new order rather than finding the old one.
            earlier = sum(1 for order in snapshot.order_history if order.instrument_id == instrument_id and order.order_id.startswith(MARGIN_CALL_ORDER_PREFIX))
            key = hashlib.sha256(
                f"margin-call|{account_id}|{instrument_id}|{position.quantity}|{position.average_cost}|{quantity}|{earlier}".encode()
            ).hexdigest()
            price = prices.get(instrument_id) or position.last_price or position.average_cost
            try:
                await asyncio.to_thread(
                    OrderGateway(repository).place_reducing,
                    account_id,
                    PaperOrderRequest(
                        order_id=f"{MARGIN_CALL_ORDER_PREFIX}{key[:32]}",
                        instrument_id=instrument_id,
                        side=cast(Any, close_side),
                        order_type="market",
                        quantity=quantity,
                        reference_price=price,
                        idempotency_key=key,
                    ),
                )
            except ValueError as exc:
                self.last_error = f"paper_margin_call_order: {exc}"
                continue
            self.margin_call_count += 1
            self.wake()
        await self._notify_margin_calls(snapshot.account, account_id, repository)

    async def _notify_margin_calls(self, account: Any, account_id: str, repository: TradingPaperRepository) -> None:
        """Queue email and push notifications of the account's recent margin calls (TVP-7.2b), once each.

        Outside the order's transaction (the order gateway is not changed): a pass that misses one queues it on the next.
        """
        if not account.notify_margin_calls:
            return
        try:
            snapshot = await asyncio.to_thread(repository.snapshot, account_id)
            orders = margin_call_orders([*snapshot.open_orders, *snapshot.order_history], datetime.now(timezone.utc))
            if orders:
                await asyncio.to_thread(self.margin_call_notifier_factory().notify, snapshot.account, orders)
        except Exception as exc:  # notifications never stop the margin call itself
            self.last_error = f"paper_margin_call_notify: {type(exc).__name__}: {exc}"

    async def run_once(self) -> int:
        repository = self.repository_factory()
        protections = self.protection_repository_factory()
        accounts = await asyncio.to_thread(repository.list_accounts, 100)
        targets: dict[tuple[str, str | None], set[str]] = defaultdict(set)
        # Accounts whose equity can fall below their margin: their positions are priced every run (TVP-7.2b).
        margin_accounts: list[str] = []
        active_orders = 0
        active_protections = 0
        for account in accounts:
            if not account.enabled:
                continue
            snapshot = await asyncio.to_thread(repository.snapshot, account.account_id)
            now = datetime.now(timezone.utc)
            if any(paper_order_is_expired(order, now) for order in snapshot.open_orders):
                # DAY/GTD orders expire on the server clock, with or without
                # market data for their instrument.
                await asyncio.to_thread(repository.expire_orders, account.account_id, now=now)
                snapshot = await asyncio.to_thread(repository.snapshot, account.account_id)
            active_orders += len(snapshot.open_orders)
            for order in snapshot.open_orders:
                targets[(order.instrument_id, order.binding_id)].add(account.account_id)
            if _uses_margin(snapshot):
                margin_accounts.append(account.account_id)
                for position in snapshot.positions:
                    if position.quantity != 0:
                        targets[(position.instrument_id, None)].add(account.account_id)
            try:
                account_protections = await asyncio.to_thread(
                    protections.list,
                    account.account_id,
                    active_only=True,
                )
            except ValueError:
                account_protections = []
            active_protections += len(account_protections)
            for protection in account_protections:
                targets[(protection.instrument_id, protection.binding_id)].add(account.account_id)

        self.active_order_count = active_orders
        self.active_protection_count = active_protections
        self.active_target_count = len(targets)

        service = self.market_service_factory()
        filled = 0
        prices: dict[str, Any] = {}
        for (instrument_id, requested_binding), account_ids in sorted(
            targets.items(), key=lambda item: (item[0][0], item[0][1] or "")
        ):
            try:
                execution = await asyncio.to_thread(
                    service.execution_observation,
                    instrument_id,
                    requested_binding,
                )
                self.quote_count += 1
                now = datetime.now(timezone.utc)
                age_ms = max(
                    0.0,
                    (now - execution.source_time.astimezone(timezone.utc)).total_seconds() * 1000.0,
                )
                self.last_execution_observation_at = execution.source_time.astimezone(timezone.utc)
                self.last_observation_age_ms = age_ms
                self.max_observation_age_ms = max(self.max_observation_age_ms, age_ms)
                if not execution.execution_eligible:
                    self.rejected_quote_count += 1
                    self.last_error = (
                        "execution_data_rejected: " + ",".join(execution.rejection_reasons)
                    )
                    continue
                observation = _paper_observation(execution)
                prices[instrument_id] = observation.price
                for account_id in sorted(account_ids):
                    fills = await asyncio.to_thread(
                        repository.process_observation,
                        account_id,
                        observation,
                    )
                    filled += len(fills)
                    await self._reconcile_protection(
                        account_id=account_id,
                        instrument_id=instrument_id,
                        execution=execution,
                        repository=repository,
                        protections=protections,
                    )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                continue
        for account_id in margin_accounts:
            try:
                await self._margin_call(account_id, prices, repository)
            except Exception as exc:
                self.last_error = f"paper_margin_call: {type(exc).__name__}: {exc}"
        self.fill_count += filled
        self.last_run_at = datetime.now(timezone.utc)
        return filled

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": trading_paper_monitor_enabled(),
            "running": self.scheduled,
            "idle_interval_seconds": self.interval_seconds,
            "active_interval_seconds": self.active_interval_seconds,
            "current_interval_seconds": (
                self.active_interval_seconds if self.active_target_count else self.interval_seconds
            ),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "last_execution_observation_at": (
                self.last_execution_observation_at.isoformat()
                if self.last_execution_observation_at
                else None
            ),
            "last_observation_age_ms": self.last_observation_age_ms,
            "max_observation_age_ms": self.max_observation_age_ms,
            "active_target_count": self.active_target_count,
            "active_order_count": self.active_order_count,
            "active_protection_count": self.active_protection_count,
            "quote_count": self.quote_count,
            "rejected_quote_count": self.rejected_quote_count,
            "fill_count": self.fill_count,
            "protection_trigger_count": self.protection_trigger_count,
            "margin_call_count": self.margin_call_count,
            "fail_closed": True,
            "reference_price_fallback": False,
            "server_authoritative_protection": True,
            "adaptive_execution_cadence": True,
        }


def create_trading_paper_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _MONITOR_STATE_KEY, None)
    if isinstance(existing, TradingPaperMonitor):
        return None
    monitor = TradingPaperMonitor()
    setattr(state, _MONITOR_STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=trading_paper_monitor_enabled)
