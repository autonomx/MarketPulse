from __future__ import annotations

from app.config.env import environment

import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .alerts_watchlist import Target, plan_watchlist_pass, watchlist_members
from .repositories import TradingDocumentRepository, default_trading_repository
from .providers.request_budget import upstream_of
from .alerts import AlertEvaluationContext, AlertOutcomeRecord, TradingAlert
from .alerts_repository import TradingAlertRepository, default_alert_repository
from .alerts_evaluation import evaluate_conditions, history_limit, required_bars
from .alert_conditions import CompareBars
from .service import TradingMarketDataService, default_market_data_service

if TYPE_CHECKING:
    from .indicators.intrabar import IntrabarLoader
    from .indicators.registry import TradingSession
    from .alerts_scripts import ScriptAlertContext
    from .external_series import ExternalSeries


_MONITOR_STATE_KEY = "_omnix_trading_alert_monitor"


def _env_flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def trading_alert_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _env_flag("OMNIX_TRADING_ALERT_MONITOR_IN_TESTS", "0")
    return _env_flag("OMNIX_TRADING_ALERT_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(environment().get("OMNIX_TRADING_ALERT_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(5.0, value)


# The pass interval, public for the watchlist capacity the API reports (TVP-1.7).
alert_monitor_interval_seconds = _interval_seconds


def _history_limit(alerts: Sequence[TradingAlert]) -> int:
    """Bars to fetch for a group of alerts; see ``alerts_evaluation.history_limit``."""
    return history_limit(max((required_bars(alert.conditions, alert.evaluation_policy.interval) for alert in alerts), default=1))


def _final_only(alert: TradingAlert) -> bool:
    return alert.frequency == "once_per_bar_close" or not alert.evaluation_policy.allow_partial_bars


def _outcomes(
    alerts: Sequence[TradingAlert],
    bars: Sequence[Any],
    external: ExternalSeries | None = None,
    session: TradingSession | None = None,
    compare: CompareBars | None = None,
    script_context: ScriptAlertContext | None = None,
    intrabar: IntrabarLoader | None = None,
) -> list[AlertOutcomeRecord]:
    records: list[AlertOutcomeRecord] = []
    for alert in alerts:
        outcome = evaluate_conditions(
            alert.conditions, bars, final_only=_final_only(alert), external=external, session=session, compare=compare,
            script_context=script_context, intrabar=intrabar,
        )
        if outcome is not None:
            records.append(AlertOutcomeRecord(alert.alert_id, alert.revision, outcome))
    return records


def _external_series(instrument_id: str, interval: str) -> ExternalSeries:
    from .external_series import ExternalSeries

    return ExternalSeries(instrument_id, interval)


class TradingAlertMonitor(ScheduledTradingMonitor):
    def __init__(
        self,
        *,
        repository_factory: Callable[[], TradingAlertRepository] = default_alert_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        document_repository_factory: Callable[[], TradingDocumentRepository] = default_trading_repository,
        interval_seconds: float | None = None,
        external_series_factory: Callable[[str, str], ExternalSeries] = _external_series,
    ) -> None:
        self.repository_factory = repository_factory
        self.external_series_factory = external_series_factory
        self.market_service_factory = market_service_factory
        self.document_repository_factory = document_repository_factory
        # Watchlist alerts (TVP-1.7), by alert: its list's members, those evaluated and skipped in the last pass.
        self.watchlist_status: dict[str, dict[str, Any]] = {}
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None
        self.evaluation_count = 0
        self.trigger_count = 0
        self.unreadable_count = 0
        self.unreadable_alert_ids: list[str] = []

    async def run_once(self) -> int:
        from .alerts_scripts import ScriptAlertContext
        from .indicator_context import compare_bars_loader, instrument_session, intrabar_loader

        repository = self.repository_factory()
        listing = await asyncio.to_thread(repository.list_alerts_report, 500)
        alerts = listing.alerts
        # Unreadable alerts are a state of the data, not an error of this pass.
        self.unreadable_count = len(listing.unreadable)
        self.unreadable_alert_ids = [item.alert_id for item in listing.unreadable[:20]]
        error: str | None = None
        targets: dict[tuple[str, str | None, str], list[TradingAlert]] = defaultdict(list)
        now = datetime.now(timezone.utc)
        service = self.market_service_factory()
        watchlist_alerts: list[TradingAlert] = []
        for alert in alerts:
            if alert.enabled and not alert.is_expired(now):
                if alert.watchlist_id is not None:
                    watchlist_alerts.append(alert)
                    continue
                targets[
                    (
                        alert.instrument_id,
                        alert.binding_id,
                        alert.evaluation_policy.interval,
                    )
                ].append(alert)
        # Watchlist alerts, by each member symbol they evaluate (TVP-1.7).
        watch_targets = await self._expand_watchlists(watchlist_alerts, set(targets), service)
        triggered = 0
        for target in sorted(set(targets) | set(watch_targets), key=lambda item: (item[0], item[1] or "", item[2])):
            instrument_id, requested_binding_id, interval = target
            target_alerts = targets.get(target, [])
            symbol_alerts = watch_targets.get(target, [])
            try:
                response = await asyncio.to_thread(
                    service.bars,
                    instrument_id,
                    interval,
                    _history_limit([*target_alerts, *symbol_alerts]),
                    requested_binding_id,
                )
                if not response.bars:
                    continue
                bars = list(response.bars)
                # External-data indicators (TVP-0.2) fetch each metric once for the target's alerts; indicators get the
                # instrument's session hours and their compare symbols' bars on this interval, as on the chart.
                external = self.external_series_factory(instrument_id, interval)
                session = instrument_session(instrument_id)
                def load_compare_bars(symbol: str, limit: int, interval: str = interval) -> Any:
                    return service.bars(symbol, interval, limit, None).bars

                compare = compare_bars_loader(load_compare_bars)
                scripts = ScriptAlertContext(instrument_id, interval, service)
                # Volume Delta and CVD read lower-timeframe bars on the target's feed, each range once a pass (TVP-6.4).
                intrabar = intrabar_loader(service, instrument_id, interval, requested_binding_id)
                # Indicator maths (and metric and compare fetches) are blocking work; keep them off the event loop.
                outcomes = await asyncio.to_thread(_outcomes, target_alerts, bars, external, session, compare, scripts, intrabar)
                symbol_outcomes = await asyncio.to_thread(_outcomes, symbol_alerts, bars, external, session, compare, scripts, intrabar)
                if not outcomes and not symbol_outcomes:
                    continue
                context = AlertEvaluationContext(
                    instrument_id=instrument_id,
                    interval=interval,
                    evaluated_at=datetime.now(timezone.utc),
                    binding_id=requested_binding_id,
                    resolved_binding_id=response.binding.binding_id,
                    provider=response.binding.provider,
                )
                if outcomes:
                    triggered += len(await asyncio.to_thread(repository.record_outcomes, context, outcomes))
                if symbol_outcomes:
                    triggered += len(await asyncio.to_thread(repository.record_watchlist_outcomes, context, symbol_outcomes))
                self.evaluation_count += 1
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        self.trigger_count += triggered
        # A clean pass clears the previous pass's error.
        self.last_error = error
        self.last_run_at = datetime.now(timezone.utc)
        return triggered

    async def _expand_watchlists(
        self,
        alerts: list[TradingAlert],
        fetched: set[Target],
        service: TradingMarketDataService,
    ) -> dict[Target, list[TradingAlert]]:
        """Each watchlist alert's member symbols, read now, within its limit and the pass's request budget."""
        if not alerts:
            self.watchlist_status = {}
            return {}
        documents = self.document_repository_factory()
        members: dict[str, list[str] | None] = {}
        for watchlist_id in sorted({alert.watchlist_id or "" for alert in alerts}):
            try:
                document = await asyncio.to_thread(documents.get, "watchlist", watchlist_id)
            except Exception:
                document = None
            members[watchlist_id] = watchlist_members(document)
        upstreams: dict[str, str | None] = {}

        def upstream_for(symbol: str) -> str | None:
            # Once per symbol and pass: resolving a binding walks the catalog.
            if symbol not in upstreams:
                try:
                    upstreams[symbol] = upstream_of(service.registry.resolve_binding(symbol).provider)
                except Exception:  # an unknown symbol: no bars to fetch
                    upstreams[symbol] = None
            return upstreams[symbol]

        watch_targets, self.watchlist_status = await asyncio.to_thread(
            plan_watchlist_pass, alerts, members, fetched, upstream_for, self.interval_seconds,
        )
        return watch_targets

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": trading_alert_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "evaluation_count": self.evaluation_count,
            "trigger_count": self.trigger_count,
            "unreadable_alert_count": self.unreadable_count,
            "unreadable_alert_ids": list(self.unreadable_alert_ids),
            "watchlist_alerts": {alert_id: dict(status) for alert_id, status in self.watchlist_status.items()},
        }


def create_trading_alert_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _MONITOR_STATE_KEY, None)
    if isinstance(existing, TradingAlertMonitor):
        return None
    monitor = TradingAlertMonitor()
    setattr(state, _MONITOR_STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=trading_alert_monitor_enabled)
