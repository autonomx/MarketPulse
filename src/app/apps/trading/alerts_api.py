from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.errors import LegacyPersistenceRetired
from app.persistence.errors import RevisionConflict

from .alerts_monitor import alert_monitor_interval_seconds
from .alerts_notify import NotificationSettingsRepository, default_notification_settings_repository
from .alerts_watchlist import watchlist_members, watchlist_symbol_cap
from .repositories import TradingDocumentRepository, default_trading_repository
from .service import TradingMarketDataService, default_market_data_service
from .alerts import WATCHLIST_SYMBOL_DEFAULT, watchlist_id_of, TradingAlert, TradingAlertCreate, TradingAlertEvaluation, TradingAlertTrigger, TradingAlertUpdate
from .alerts_repository import TradingAlertRepository, TradingAlertUnreadable, default_alert_repository
from .alerts_delivery import NotificationDelivery, NotificationDeliveryRepository, default_delivery_repository
from .alerts_channels import (
    available_alert_channels,
    AlertWebhookStore,
    ProtectedAlertWebhookStore,
    alert_webhook_prefix,
    mask_webhook_url,
    unavailable_channels,
)

logger = logging.getLogger(__name__)


class TradingAlertListResponse(BaseModel):
    alerts: list[TradingAlert]
    # Stored alerts that no longer read; archive them with their revision.
    unreadable: list[TradingAlertUnreadable] = []


class TradingAlertTriggerListResponse(BaseModel):
    triggers: list[TradingAlertTrigger]


class TradingAlertIndicatorListResponse(BaseModel):
    indicator_ids: list[str]


class TradingAlertDeliveryListResponse(BaseModel):
    # Status of each notification delivery; destinations are never returned.
    deliveries: list[NotificationDelivery]


class WatchlistAlertCapacity(BaseModel):
    """What an alert on a watchlist evaluates (TVP-1.7): the dialog shows it next to the symbol limit."""

    watchlist_id: str
    symbol_count: int
    # The most symbols a pass fetches for this list within its providers' request budgets.
    provider_cap: int
    default_limit: int


AlertRepositoryFactory = Callable[[], TradingAlertRepository]


@dataclass(frozen=True)
class _WebhookPlan:
    """Which protected-store reference an alert write will point at.

    ``ref`` is what the row will reference. ``written`` is a reference this
    request stored before the row write (inside the alert's advisory-locked
    transaction): it is removed again if the transaction fails. ``previous``
    is what the row pointed at, read under the row lock; it is removed after
    a commit that replaced it. The row never references anything that was not
    stored first, and nothing is removed that another row may point at.
    """

    ref: str | None
    written: str | None
    previous: str | None


def _conflict(exc: RevisionConflict) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)})


def _store_failure(alert_id: str, exc: Exception) -> HTTPException:
    logger.error("trading_alert_webhook_store_failed alert_id=%s error=%s", alert_id, type(exc).__name__)
    return HTTPException(
        status_code=503,
        detail="the protected webhook store is unavailable; the alert was not changed",
    )


def _plan_webhook(
    request: TradingAlertCreate | TradingAlertUpdate,
    previous_ref: str | None,
    workspace_id: str,
    alert_id: str,
    *,
    channels: frozenset[str],
    store: AlertWebhookStore,
) -> _WebhookPlan:
    missing = unavailable_channels(request.parameters.notification_channels, channels)
    if missing:
        raise HTTPException(status_code=422, detail=f"alert channel {missing[0]} is not available yet")
    webhook = request.parameters.delivery.webhook
    secret = request.webhook_secret
    if secret is not None and webhook is None:
        raise HTTPException(status_code=422, detail="webhook_secret needs parameters.delivery.webhook")
    if webhook is None and "webhook" in request.parameters.notification_channels:
        raise HTTPException(status_code=422, detail="the webhook channel needs parameters.delivery.webhook")
    if webhook is None:
        return _WebhookPlan(ref=None, written=None, previous=previous_ref)
    stored: dict[str, str] | None = None
    if previous_ref:
        try:
            stored = store.load(previous_ref)
        except Exception as exc:
            # Never mistake an unreadable store for a missing webhook.
            raise _store_failure(alert_id, exc) from exc
        if stored is None and (webhook.url is None or secret is None):
            # The row points at a webhook the store does not have; refuse to guess.
            raise HTTPException(
                status_code=409,
                detail="the stored webhook is missing; send its url and webhook_secret again",
            )
    url = webhook.url or (stored or {}).get("url")
    if not url:
        raise HTTPException(status_code=422, detail="parameters.delivery.webhook.url is required")
    value = (stored or {}).get("secret", "") if secret is None else secret.get_secret_value().strip()
    webhook.url = None  # write-only: the protected store keeps it
    webhook.display_url = mask_webhook_url(url)
    webhook.has_secret = bool(value)
    if stored is not None and stored.get("url") == url and stored.get("secret", "") == value:
        return _WebhookPlan(ref=previous_ref, written=None, previous=previous_ref)
    if not store.available():
        raise HTTPException(status_code=422, detail="alert webhooks require an operating-system credential store")
    ref = f"{alert_webhook_prefix(workspace_id, alert_id)}{uuid.uuid4().hex}"
    try:
        store.save(ref, url, value)
    except LegacyPersistenceRetired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise _store_failure(alert_id, exc) from exc
    return _WebhookPlan(ref=ref, written=ref, previous=previous_ref)


def _best_effort(action: str, alert_id: str, operation: Callable[[], None]) -> None:
    """A reference no row points at is unused, so a failure here only leaves litter."""
    try:
        operation()
    except Exception:
        logger.warning("trading_alert_webhook_%s_failed alert_id=%s", action, alert_id, exc_info=True)


@dataclass(frozen=True)
class _AlertRoutes:
    """What the alert routes share: repositories, the webhook store, the channels and the request checks."""

    repository_factory: AlertRepositoryFactory
    store: AlertWebhookStore
    channels: frozenset[str]
    delivery_repository_factory: Callable[[], NotificationDeliveryRepository]
    document_repository_factory: Callable[[], TradingDocumentRepository]
    market_service_factory: Callable[[], TradingMarketDataService]
    notification_settings_factory: Callable[[], NotificationSettingsRepository]

    def watchlist_or_422(self, watchlist_id: str | None) -> list[str]:
        """A watchlist alert's watchlist must exist; its members, read now (TVP-1.7)."""
        if watchlist_id is None:
            return []
        document = self.document_repository_factory().get("watchlist", watchlist_id)
        if not document or document.get("status", "active") != "active":
            raise HTTPException(status_code=422, detail=f"watchlist {watchlist_id} was not found")
        return watchlist_members(document) or []

    def delivery_or_422(self, channels) -> None:
        """Email and push need their setup first (TVP-0.5b/c): settings, and a browser that allows notifications."""
        if "email" in channels and self.notification_settings_factory().email() is None:
            raise HTTPException(status_code=422, detail="set up email delivery before choosing the Email channel")
        if "push" in channels and not self.notification_settings_factory().subscriptions():
            raise HTTPException(status_code=422, detail="turn on notifications in a browser before choosing the Push channel")

    def scripts_or_422(self, conditions) -> None:
        """A script alert's script version must exist and compile (TVP-11.4)."""
        from .alerts_scripts import script_sources, validate_script_sources

        if not script_sources(conditions):
            return
        try:
            validate_script_sources(conditions, self.document_repository_factory())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def plan_webhook(
        self,
        request: TradingAlertCreate | TradingAlertUpdate,
        previous_ref: str | None,
        workspace_id: str,
        alert_id: str,
    ) -> _WebhookPlan:
        return _plan_webhook(request, previous_ref, workspace_id, alert_id, channels=self.channels, store=self.store)


def create_trading_alert_router(
    repository_factory: AlertRepositoryFactory = default_alert_repository,
    *,
    webhook_store: AlertWebhookStore | None = None,
    available_channels: Iterable[str] | None = None,
    delivery_repository_factory: Callable[[], NotificationDeliveryRepository] = default_delivery_repository,
    document_repository_factory: Callable[[], TradingDocumentRepository] = default_trading_repository,
    market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
    notification_settings_factory: Callable[[], NotificationSettingsRepository] = default_notification_settings_repository,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/alerts", tags=["trading-alerts"])
    routes = _AlertRoutes(
        repository_factory=repository_factory,
        store=webhook_store or ProtectedAlertWebhookStore(),
        channels=frozenset(available_alert_channels() if available_channels is None else available_channels),
        delivery_repository_factory=delivery_repository_factory,
        document_repository_factory=document_repository_factory,
        market_service_factory=market_service_factory,
        notification_settings_factory=notification_settings_factory,
    )
    # Registered in this order, as before, so the route table is unchanged.
    _add_alert_routes(router, routes)
    _add_alert_change_routes(router, routes)
    return router


def _add_alert_routes(router: APIRouter, routes: _AlertRoutes) -> None:
    """Listing, creating and evaluating alerts, and what the alert dialog reads."""
    repository_factory = routes.repository_factory
    store = routes.store
    delivery_repository_factory = routes.delivery_repository_factory
    market_service_factory = routes.market_service_factory
    watchlist_or_422 = routes.watchlist_or_422
    delivery_or_422 = routes.delivery_or_422
    scripts_or_422 = routes.scripts_or_422
    plan_webhook = routes.plan_webhook
    best_effort = _best_effort

    @router.get("", response_model=TradingAlertListResponse)
    def list_alerts(
        limit: int = Query(default=200, ge=1, le=500),
    ) -> TradingAlertListResponse:
        listing = repository_factory().list_alerts_report(limit=limit)
        return TradingAlertListResponse(alerts=listing.alerts, unreadable=listing.unreadable)

    @router.get("/watchlist-capacity", response_model=WatchlistAlertCapacity)
    def watchlist_capacity(watchlist_id: str = Query(min_length=1, max_length=200)) -> WatchlistAlertCapacity:
        """How many of a watchlist's symbols an alert on it evaluates: its symbols, and the cap their providers allow."""
        symbols = watchlist_or_422(watchlist_id)
        registry = market_service_factory().registry

        def provider_of(symbol: str) -> str | None:
            try:
                return registry.resolve_binding(symbol).provider
            except Exception:
                logger.debug("suppressed error in %s", "provider_of", exc_info=True)
                return None

        return WatchlistAlertCapacity(
            watchlist_id=watchlist_id,
            symbol_count=len(symbols),
            provider_cap=watchlist_symbol_cap(symbols, provider_of, alert_monitor_interval_seconds()),
            default_limit=WATCHLIST_SYMBOL_DEFAULT,
        )

    @router.post("", response_model=TradingAlert, status_code=201)
    def create_alert(request: TradingAlertCreate) -> TradingAlert:
        watchlist_or_422(watchlist_id_of(request.instrument_id))
        scripts_or_422(request.conditions)
        delivery_or_422(request.parameters.notification_channels)
        repository = repository_factory()
        workspace_id = repository.context.workspace_id
        alert_id = request.alert_id
        plan: _WebhookPlan | None = None
        try:
            with repository.alert_transaction(alert_id) as state:
                if state.exists:
                    raise HTTPException(status_code=409, detail=f"Trading alert already exists: {alert_id}")
                plan = plan_webhook(request, None, workspace_id, alert_id)
                if plan.written:
                    # Nothing references webhooks left by an earlier alert with this id.
                    best_effort("cleanup", alert_id, lambda: store.delete_alert(workspace_id, alert_id, keep=plan.written))
                created = repository.create(request, webhook_ref=plan.ref)
        except Exception as exc:
            if plan is not None and plan.written:
                best_effort("discard", alert_id, lambda: store.delete(plan.written or ""))
            if isinstance(exc, RevisionConflict):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise
        return created

    @router.get("/indicators", response_model=TradingAlertIndicatorListResponse)
    def list_alert_indicators() -> TradingAlertIndicatorListResponse:
        """The indicators the server evaluates for alerts (TVP-1.3): the dialog offers these, greys out the rest.

        Includes the external-data indicators (TVP-0.2), read from their metric series."""
        from .indicators.external import external_indicator_ids
        from .indicators.intrabar import INTRABAR_OUTPUTS
        from .indicators.registry import server_indicator_ids

        return TradingAlertIndicatorListResponse(
            indicator_ids=sorted([*server_indicator_ids(), *external_indicator_ids(), *INTRABAR_OUTPUTS])
        )

    @router.get("/triggers", response_model=TradingAlertTriggerListResponse)
    def list_triggers(
        limit: int = Query(default=200, ge=1, le=500),
    ) -> TradingAlertTriggerListResponse:
        return TradingAlertTriggerListResponse(
            triggers=repository_factory().list_triggers(limit=limit)
        )

    @router.get("/deliveries", response_model=TradingAlertDeliveryListResponse)
    def list_deliveries(
        alert_id: str | None = Query(default=None, min_length=1, max_length=200),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> TradingAlertDeliveryListResponse:
        """Webhook (and later email and push) deliveries, newest first: status, attempts and the last error code."""
        return TradingAlertDeliveryListResponse(
            deliveries=delivery_repository_factory().list_deliveries(alert_id=alert_id, limit=limit)
        )

    @router.post("/evaluate", response_model=TradingAlertTriggerListResponse)
    def evaluate_alerts(
        request: TradingAlertEvaluation,
    ) -> TradingAlertTriggerListResponse:
        """Evaluate a pushed price against the instrument's alerts.

        Only alerts whose conditions all read one price field (close from
        ``observed_price``, or volume from ``observed_volume``) against value
        targets are evaluated: the alert's ``last_observed_value`` is the
        previous value and the pushed value the current one. Every other alert
        (indicators, percent change, trendlines, moving operators) is skipped;
        the server monitor evaluates those on bars. Alerts with a per-bar
        frequency (``once_per_bar``, ``once_per_bar_close``) are skipped too: a
        pushed price carries no bar. Other frequencies, cooldown and
        idempotency apply as for monitored alerts, with ``observed_at`` as the
        observation's time.
        """
        return TradingAlertTriggerListResponse(
            triggers=repository_factory().evaluate(request)
        )


def _add_alert_change_routes(router: APIRouter, routes: _AlertRoutes) -> None:
    """Updating and archiving an alert."""
    repository_factory = routes.repository_factory
    store = routes.store
    watchlist_or_422 = routes.watchlist_or_422
    delivery_or_422 = routes.delivery_or_422
    scripts_or_422 = routes.scripts_or_422
    plan_webhook = routes.plan_webhook
    best_effort = _best_effort

    @router.put("/{alert_id}", response_model=TradingAlert)
    def update_alert(
        alert_id: str,
        request: TradingAlertUpdate,
        if_match: int = Header(alias="If-Match", ge=1),
    ) -> TradingAlert:
        repository = repository_factory()
        workspace_id = repository.context.workspace_id
        plan: _WebhookPlan | None = None
        try:
            with repository.alert_transaction(alert_id) as state:
                previous = repository.get(alert_id) if state.exists else None
                if previous is None or previous.revision != if_match:
                    raise RevisionConflict(f"Trading alert expected revision {if_match}: {alert_id}")
                # An alert whose watchlist was deleted can still be disabled or edited; it can't be (re)enabled on it.
                if request.enabled or request.instrument_id != previous.instrument_id:
                    watchlist_or_422(watchlist_id_of(request.instrument_id))
                if request.enabled or request.conditions != previous.conditions:
                    scripts_or_422(request.conditions)
                added = set(request.parameters.notification_channels) - set(previous.parameters.notification_channels)
                delivery_or_422(added if not request.enabled else request.parameters.notification_channels)
                plan = plan_webhook(request, state.webhook_ref, workspace_id, alert_id)
                updated = repository.update(alert_id, request, expected_revision=if_match, webhook_ref=plan.ref)
        except Exception as exc:
            if plan is not None and plan.written:
                best_effort("discard", alert_id, lambda: store.delete(plan.written or ""))
            if isinstance(exc, RevisionConflict):
                raise _conflict(exc) from exc
            raise
        if plan.previous and plan.previous != plan.ref:
            # Rotated or removed: the reference the row pointed at stops existing.
            best_effort("prune", alert_id, lambda: store.delete(plan.previous or ""))
        return updated

    @router.delete("/{alert_id}", response_model=TradingAlert)
    def archive_alert(
        alert_id: str,
        if_match: int = Header(alias="If-Match", ge=1),
    ) -> TradingAlert:
        repository = repository_factory()
        try:
            with repository.alert_transaction(alert_id) as state:
                archived = repository.archive(alert_id, expected_revision=if_match)
        except RevisionConflict as exc:
            raise _conflict(exc) from exc
        if state.webhook_ref:
            best_effort("prune", alert_id, lambda: store.delete(state.webhook_ref or ""))
        return archived
