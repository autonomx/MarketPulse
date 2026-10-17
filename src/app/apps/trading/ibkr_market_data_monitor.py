"""Shared IBKR quote-demand reconciler and causal observation bridge.

This monitor owns no trading authority. It unions the instruments demanded by
all active strategy configurations, opens one upstream IBKR quote line per
unique instrument, and continuously records quote snapshots into the existing
causal observation plane. The rollout remains zero-authority unless the separate
IBKR LIVE_DATA gate is explicitly enabled.
"""

from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .execution import assess_execution_observation, execution_observation_from_quote
from .execution_observation_plane import (
    ExecutionObservationPlane,
    default_execution_observation_plane,
)
from .ibkr_evidence import IbkrEvidenceStore, default_ibkr_evidence_store
from .ibkr_settings import load_ibkr_settings
from .providers.ibkr import IbkrEquityProvider
from .service import TradingMarketDataService, default_market_data_service
from .strategy_repository import TradingStrategyRepository, default_strategy_repository
from .strategy_shadow_universe import (
    resolve_stoch_rsi_5m_runtime_archive,
    resolve_v2_runtime_archive,
)
from .streaming.manager import StreamingQuoteUpdate
from .us_equity_calendar import us_equity_session
from app.apps.trading.us_equity_calendar import EASTERN as _ET

logger = logging.getLogger(__name__)


_STATE_KEY = "_omnix_trading_ibkr_market_data_monitor"


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ibkr_market_data_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_IBKR_MONITOR_IN_TESTS", "0")
    return load_ibkr_settings()[0].monitor_enabled


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_IBKR_DEMAND_RECONCILE_SECONDS", "5"))
    except ValueError:
        value = 5.0
    return max(1.0, value)


def _market_data_line_budget() -> int:
    """Conservative default leaves headroom under a typical 100-line allowance."""

    try:
        value = int(_env_str("OMNIX_IBKR_MARKET_DATA_LINE_BUDGET", "80"))
    except ValueError:
        value = 80
    return max(1, value)


class TradingIbkrMarketDataMonitor(ScheduledTradingMonitor):
    error_event = "ibkr_market_data_monitor_error"

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        plane: ExecutionObservationPlane | None = None,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
        evidence_store: IbkrEvidenceStore | None = None,
        market_data_line_budget: int | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.plane = plane or default_execution_observation_plane()
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.evidence_store = evidence_store or default_ibkr_evidence_store()
        self.market_data_line_budget = max(
            1,
            int(market_data_line_budget or _market_data_line_budget()),
        )
        self._keys: dict[str, str] = {}
        self._callbacks: dict[str, Callable] = {}
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.demanded_instrument_count = 0
        self.budget_denied_instrument_count = 0
        self.live_event_count = 0
        self.nonlive_event_count = 0
        self.recorded_observation_count = 0
        self.subscription_create_count = 0
        self.subscription_remove_count = 0
        self.subscription_error_count = 0

    def _active_demand(
        self,
        repository: TradingStrategyRepository,
        *,
        now: datetime,
    ) -> set[str]:
        demanded: set[str] = set()
        configs = repository.list_configs(active_only=True)
        for config in configs:
            universe = None
            if config.active_universe_id:
                try:
                    universe = repository.get_universe(config.active_universe_id)
                except Exception:
                    universe = None
            if universe is None:
                try:
                    if config.strategy_kind == "stoch_rsi_5m_v1":
                        universe = resolve_stoch_rsi_5m_runtime_archive(
                            config,
                            repository,
                            now=now,
                        )
                    else:
                        universe = resolve_v2_runtime_archive(
                            config,
                            repository,
                            now=now,
                        )
                except Exception:
                    universe = None
            if universe is None:
                continue
            for candidate in universe.candidates:
                instrument_id = str(candidate.instrument_id)
                if instrument_id.startswith("equity:"):
                    demanded.add(instrument_id)
        return demanded

    def _admitted_demand(self, demanded: set[str]) -> set[str]:
        """Preserve existing lines first, then admit deterministic new demand."""

        existing = sorted(set(self._keys) & demanded)
        selected = existing[: self.market_data_line_budget]
        remaining = self.market_data_line_budget - len(selected)
        if remaining > 0:
            new_demand = sorted(demanded - set(selected))
            selected.extend(new_demand[:remaining])
        admitted = set(selected)
        self.budget_denied_instrument_count = max(0, len(demanded - admitted))
        return admitted

    def _record_quote(self, update: StreamingQuoteUpdate) -> None:
        session_date = update.source_time.astimezone(_ET).date()
        if update.last is None:
            self.evidence_store.record_missing_quote(
                session_date,
                "IBKR_LAST_MISSING",
                defer=True,
            )
            return
        freshness = "live" if update.market_data_type == "LIVE" else "delayed"
        quote = {
            "instrument_id": update.instrument_id,
            "binding_id": update.binding_id,
            "provider": update.provider,
            "bid": update.bid,
            "ask": update.ask,
            "bid_size": update.bid_size,
            "ask_size": update.ask_size,
            "last": update.last,
            "high": update.high,
            "low": update.low,
            "cumulative_volume": update.cumulative_volume,
            "source_time": update.source_time,
            "received_at": update.received_at,
            "session": us_equity_session(update.source_time),
            "freshness_mode": freshness,
            "provider_sequence": update.provider_sequence,
            "market_data_type": update.market_data_type,
            "live_entitled": update.live_entitled,
            "contract_id": update.contract_id,
            "primary_exchange": update.primary_exchange,
            "local_symbol": update.local_symbol,
        }
        observation = execution_observation_from_quote(
            quote,
            binding_id=update.binding_id,
            provider=update.provider,
            received_at=update.received_at,
        )
        observation = assess_execution_observation(
            observation,
            binding_purpose="LIVE_DATA",
        )
        if self.plane.record(observation, recorded_at=update.received_at):
            self.recorded_observation_count += 1

        reference = max(
            (
                row
                for row in self.plane.observations(update.instrument_id)
                if row.observation.binding_purpose == "EXECUTION"
                and row.observation.provider == "alpaca_iex"
                and abs(
                    (
                        update.received_at.astimezone(timezone.utc)
                        - row.recorded_at.astimezone(timezone.utc)
                    ).total_seconds()
                )
                <= 5
            ),
            key=lambda row: row.recorded_at,
            default=None,
        )
        last_diff_bps = None
        spread_diff_bps = None
        if reference is not None and reference.observation.last > 0:
            last_diff_bps = (
                (update.last - reference.observation.last)
                / reference.observation.last
                * Decimal("10000")
            )
            if observation.spread_bps is not None and reference.observation.spread_bps is not None:
                spread_diff_bps = observation.spread_bps - reference.observation.spread_bps

        self.evidence_store.record_quote(
            session_date,
            market_data_type=update.market_data_type,
            live_entitled=update.live_entitled,
            quote_age_seconds=observation.age_seconds,
            spread_bps=observation.spread_bps,
            last_price_diff_bps=last_diff_bps,
            spread_diff_bps=spread_diff_bps,
            # On the IBKR network thread: queue only; the scheduled cycle writes.
            defer=True,
        )
        if update.market_data_type == "LIVE" and update.live_entitled is True:
            self.live_event_count += 1
        else:
            self.nonlive_event_count += 1

    def _provider_callback(
        self,
        *,
        market_service: TradingMarketDataService,
        binding_id: str,
        instrument_id: str,
    ):
        def callback(snapshot) -> None:
            update = StreamingQuoteUpdate(
                binding_id=binding_id,
                instrument_id=instrument_id,
                provider="ibkr",
                source_time=snapshot.source_time,
                received_at=snapshot.received_at,
                bid=snapshot.bid,
                ask=snapshot.ask,
                bid_size=snapshot.bid_size,
                ask_size=snapshot.ask_size,
                last=snapshot.last,
                last_size=snapshot.last_size,
                high=snapshot.high,
                low=snapshot.low,
                cumulative_volume=snapshot.cumulative_volume,
                market_data_type=snapshot.market_data_type,
                live_entitled=snapshot.live_entitled,
                contract_id=str(snapshot.contract.con_id),
                primary_exchange=snapshot.contract.primary_exchange,
                local_symbol=snapshot.contract.local_symbol,
                provider_sequence=snapshot.provider_sequence,
            )
            market_service.subscriptions.publish_quote(update)
            key = market_service.subscriptions.key(
                binding_id,
                instrument_id,
                None,
                stream_kind="QUOTE",
            )
            market_service.subscriptions.mark_connected(key)

        return callback

    def _ensure_subscription(
        self,
        market_service: TradingMarketDataService,
        provider: IbkrEquityProvider,
        instrument_id: str,
    ) -> None:
        binding = provider.get_binding(instrument_id)
        listener_id = f"ibkr-plane:{instrument_id}"
        if instrument_id not in self._callbacks:
            self._callbacks[instrument_id] = self._provider_callback(
                market_service=market_service,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
            )
        key, created = market_service.subscriptions.subscribe_quote(
            listener_id=listener_id,
            binding_id=binding.binding_id,
            instrument_id=instrument_id,
            listener=self._record_quote,
        )
        self._keys[instrument_id] = key
        callback = self._callbacks[instrument_id]
        try:
            # Calling this on every reconciliation is intentional: IbkrRuntime
            # deduplicates healthy subscriptions but recreates them after a
            # Gateway reconnect invalidates prior request IDs.
            provider.subscribe_quote(instrument_id, callback)
            if created:
                self.subscription_create_count += 1
        except Exception as exc:
            self.subscription_error_count += 1
            self.last_error = f"{instrument_id}: {type(exc).__name__}: {exc}"
            market_service.subscriptions.mark_disconnected(key)
            self.evidence_store.record_subscription_error(
                self.now_factory().astimezone(_ET).date(),
                f"{type(exc).__name__}:{exc}",
            )

    def _remove_subscription(
        self,
        market_service: TradingMarketDataService,
        provider: IbkrEquityProvider,
        instrument_id: str,
    ) -> None:
        key = self._keys.pop(instrument_id, None)
        callback = self._callbacks.pop(instrument_id, None)
        if key is None:
            return
        listener_id = f"ibkr-plane:{instrument_id}"
        close_upstream = market_service.subscriptions.unsubscribe(key, listener_id)
        if close_upstream:
            try:
                provider.unsubscribe_quote(instrument_id, listener=callback)
            except Exception:
                logger.debug("suppressed error in %s", "TradingIbkrMarketDataMonitor._remove_subscription", exc_info=True)
        self.subscription_remove_count += 1

    def _run_once_blocking(self) -> int:
        """Reconcile IBKR demand without blocking the gateway event loop."""

        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("IBKR market-data monitor clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        provider = market_service.registry.provider("ibkr")
        if not isinstance(provider, IbkrEquityProvider):
            raise TypeError("IBKR registry provider has unexpected type")

        demanded = self._active_demand(repository, now=now)
        self.demanded_instrument_count = len(demanded)
        admitted = self._admitted_demand(demanded)

        for instrument_id in sorted(set(self._keys) - admitted):
            self._remove_subscription(market_service, provider, instrument_id)

        if not provider.runtime.enabled:
            self.last_run_at = now
            return 0

        runtime_diagnostics = provider.runtime.diagnostics()
        self.evidence_store.record_runtime(
            now.astimezone(_ET).date(),
            connected=bool(runtime_diagnostics.get("connected")),
            reconnect_count=int(runtime_diagnostics.get("reconnect_count", 0) or 0),
            active_subscriptions=int(runtime_diagnostics.get("active_quote_subscriptions", 0) or 0),
            historical_request_count=int(runtime_diagnostics.get("historical_request_count", 0) or 0),
            historical_cache_hit_count=int(runtime_diagnostics.get("historical_cache_hit_count", 0) or 0),
        )

        before = self.recorded_observation_count
        for instrument_id in sorted(admitted):
            self._ensure_subscription(
                market_service,
                provider,
                instrument_id,
            )

        self.last_run_at = now
        # Write the quote evidence queued by the network thread since the last cycle.
        self.evidence_store.flush()
        return self.recorded_observation_count - before

    async def run_once(self) -> int:
        # IBKR connect, contract qualification, and subscribe/unsubscribe calls
        # can synchronously wait for the desktop gateway. Keep the complete pass
        # on one worker thread so those waits cannot stall FastAPI health or chat.
        return await asyncio.to_thread(self._run_once_blocking)

    def diagnostics(self) -> dict[str, Any]:
        settings = load_ibkr_settings()[0]
        return {
            "enabled": settings.monitor_enabled,
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "demanded_instrument_count": self.demanded_instrument_count,
            "market_data_line_budget": self.market_data_line_budget,
            "budget_denied_instrument_count": self.budget_denied_instrument_count,
            "active_subscription_count": len(self._keys),
            "subscription_create_count": self.subscription_create_count,
            "subscription_remove_count": self.subscription_remove_count,
            "subscription_error_count": self.subscription_error_count,
            "live_event_count": self.live_event_count,
            "nonlive_event_count": self.nonlive_event_count,
            "recorded_observation_count": self.recorded_observation_count,
            "authority_mode": "LIVE_DATA" if settings.live_authority_enabled else "ZERO_AUTHORITY_OBSERVATION",
            "order_execution_authority": False,
        }

    async def close(self) -> None:
        await asyncio.to_thread(self._remove_all_subscriptions)
        await asyncio.to_thread(self.evidence_store.flush)

    def _remove_all_subscriptions(self) -> None:
        try:
            market_service = self.market_service_factory()
            provider = market_service.registry.provider("ibkr")
            for instrument_id in list(self._keys):
                self._remove_subscription(market_service, provider, instrument_id)
        except Exception:
            logger.debug("suppressed error in %s", "TradingIbkrMarketDataMonitor._remove_all_subscriptions", exc_info=True)


def create_trading_ibkr_market_data_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingIbkrMarketDataMonitor):
        return None
    monitor = TradingIbkrMarketDataMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(
        name=__name__, monitor=monitor, enabled=ibkr_market_data_monitor_enabled, shutdown=(monitor.close,),
    )


__all__ = [
    "TradingIbkrMarketDataMonitor",
    "ibkr_market_data_monitor_enabled",
    "create_trading_ibkr_market_data_monitor_task",
]
