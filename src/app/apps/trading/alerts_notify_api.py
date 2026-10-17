"""Alert email and web-push settings over HTTP (TVP-0.5b/c): ``/api/trading/notifications``.

Email settings are the workspace's; the SMTP password goes to the protected secret store and is never returned.
Push subscriptions are per signed-in user and device; their endpoints and keys are never returned either.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.config.product import product_name
from app.security.tenant_context import current_tenant

from .alerts_notify import (
    EmailSettings,
    EmailSettingsWrite,
    NotificationSecretStore,
    NotificationSettingsRepository,
    ProtectedNotificationSecretStore,
    PushSubscription,
    PushSubscriptionWrite,
    default_notification_settings_repository,
    smtp_password_secret,
)

if TYPE_CHECKING:
    from .alerts_notify_senders import EmailSender, PushSender

logger = logging.getLogger(__name__)


class EmailSettingsResponse(BaseModel):
    configured: bool
    settings: EmailSettings | None = None
    # Whether credentials can be stored here (the OS-protected store).
    store_available: bool


class PushSettingsResponse(BaseModel):
    available: bool
    public_key: str | None = None
    subscriptions: list[PushSubscription]


class DeliveryTestResponse(BaseModel):
    outcome: Literal["delivered", "retry", "failed"]
    error: str | None = None


def create_trading_notification_router(
    repository_factory: Callable[[], NotificationSettingsRepository] = default_notification_settings_repository,
    store: NotificationSecretStore | None = None,
    *,
    email_sender: EmailSender | None = None,
    push_sender: PushSender | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/notifications", tags=["trading-notifications"])
    secrets = store or ProtectedNotificationSecretStore()

    # The senders load smtplib and the Web Push cryptography: built on first use, not with the router.
    def email() -> EmailSender:
        from .alerts_notify_senders import EmailSender

        return email_sender or EmailSender(store=secrets)

    def push() -> PushSender:
        from .alerts_notify_senders import PushSender

        return push_sender or PushSender(store=secrets)

    def workspace() -> str:
        return str(current_tenant().workspace_id)

    @router.get("/email", response_model=EmailSettingsResponse)
    def get_email() -> EmailSettingsResponse:
        settings = repository_factory().email()
        return EmailSettingsResponse(configured=settings is not None, settings=settings, store_available=secrets.available())

    @router.put("/email", response_model=EmailSettingsResponse)
    def put_email(request: EmailSettingsWrite) -> EmailSettingsResponse:
        repository = repository_factory()
        previous = repository.email()
        name = smtp_password_secret(workspace())
        has_password = previous.has_password if previous else False
        if request.password is not None:
            if request.password and not secrets.available():
                raise HTTPException(status_code=422, detail="this server has no protected credential store for an SMTP password")
            try:
                if request.password:
                    secrets.save(name, request.password)
                else:
                    secrets.delete(name)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="the credential store is unavailable") from exc
            has_password = bool(request.password)
        if request.username and not has_password:
            raise HTTPException(status_code=422, detail="a login needs its password")
        settings = EmailSettings.model_validate({**request.model_dump(exclude={"password", "has_password"}), "has_password": has_password})
        repository.save_email(settings)
        return EmailSettingsResponse(configured=True, settings=settings, store_available=secrets.available())

    @router.delete("/email", status_code=204)
    def delete_email() -> Response:
        repository_factory().save_email(None)
        try:
            secrets.delete(smtp_password_secret(workspace()))
        except Exception:
            logger.debug("suppressed error in %s", "delete_email", exc_info=True)
        return Response(status_code=204)

    @router.post("/email/test", response_model=DeliveryTestResponse)
    async def test_email() -> DeliveryTestResponse:
        settings = repository_factory().email()
        if settings is None:
            raise HTTPException(status_code=422, detail="set up email delivery first")
        from .alerts_notify_senders import alert_email

        message = alert_email(settings, f"This is a test of {product_name()} alert emails. Alerts with the Email channel arrive like this.", test=True)
        result = await asyncio.to_thread(email().deliver, settings, workspace(), message)
        return DeliveryTestResponse(outcome=result.outcome, error=result.error)

    @router.get("/push", response_model=PushSettingsResponse)
    def get_push() -> PushSettingsResponse:
        from .alerts_notify_senders import vapid_key
        from .webpush import vapid_public_key

        try:
            key = vapid_key(secrets, create=True)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="the credential store is unavailable") from exc
        return PushSettingsResponse(
            available=key is not None, public_key=vapid_public_key(key) if key is not None else None,
            subscriptions=repository_factory().listed_subscriptions(),
        )

    @router.post("/push/subscriptions", status_code=201)
    def subscribe(request: PushSubscriptionWrite) -> dict[str, str]:
        if not request.endpoint.startswith("https://"):
            raise HTTPException(status_code=422, detail="a push endpoint is an https URL")
        try:
            subscription_id = repository_factory().save_subscription(str(current_tenant().user_id), request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"subscription_id": subscription_id}

    @router.delete("/push/subscriptions/{subscription_id}", status_code=204)
    def unsubscribe(subscription_id: str) -> Response:
        if not repository_factory().delete_subscription(subscription_id):
            raise HTTPException(status_code=404, detail="no such subscription")
        return Response(status_code=204)

    @router.post("/push/test", response_model=DeliveryTestResponse)
    async def test_push() -> DeliveryTestResponse:
        repository = repository_factory()
        mine = repository.subscriptions(user_id=str(current_tenant().user_id))
        if not mine:
            raise HTTPException(status_code=422, detail="turn on notifications in this browser first")
        from .alerts_notify_senders import push_payload

        payload = push_payload(f"This is a test of {product_name()} alert notifications.", title=f"{product_name()} test notification")
        result = await asyncio.to_thread(push().deliver, repository, workspace(), mine, payload)
        return DeliveryTestResponse(outcome=result.outcome, error=result.error)

    return router


__all__ = ["create_trading_notification_router"]
