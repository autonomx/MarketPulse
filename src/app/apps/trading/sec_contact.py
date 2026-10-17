"""The contact every SEC EDGAR request carries.

SEC's fair-access policy refuses requests (403) whose User-Agent has no contact email. The contact is, in order:

1. ``OMNIX_SEC_USER_AGENT``, when the operator sets one;
2. the signed-in user's account email (the local user's when sign-in is off);
3. the install owner's email, for guests, accounts without an email, and scheduled work outside a request.

Without any of them, a request is not sent: :class:`SecContactRequired` asks the user to add an email to their account.
The email goes only to SEC, in this header.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.config.env import env_str
from app.config.product import product_name
from app.runtime.tenant_context import LOCAL_USER_ID

CACHE_SECONDS = 60.0
CONTACT_REQUIRED_MESSAGE = (
    "SEC asks for a contact email with each request. Add an email to your account (Settings, Account), then try again."
)


class SecContactRequired(RuntimeError):
    """No contact email is known, so SEC would refuse the request."""

    code = "sec_contact_required"

    def __init__(self) -> None:
        super().__init__(CONTACT_REQUIRED_MESSAGE)


def _requesting_user() -> str:
    from app.runtime.tenant_context import current_tenant
    from app.security.request_tenant import current_principal

    principal = current_principal()
    if principal is not None:
        return principal.user_id
    try:
        return current_tenant().user_id
    except RuntimeError:
        return LOCAL_USER_ID


def _account_email(user_id: str) -> str | None:
    from app.persistence.unit_of_work import unit_of_work

    with unit_of_work() as work:
        try:
            account = work.identities.user_account(user_id)
        finally:
            work.rollback()
    if account is None or account["status"] != "active" or account["account_kind"] == "guest":
        return None
    return account["email"]


class SecContact:
    """Resolves the contact email; each user's lookup is cached for :data:`CACHE_SECONDS`."""

    def __init__(
        self,
        *,
        user: Callable[[], str] = _requesting_user,
        email_of: Callable[[str], str | None] = _account_email,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user = user
        self._email_of = email_of
        self._clock = clock
        self._cache: dict[str, tuple[float, str | None]] = {}
        self._lock = threading.Lock()

    def _cached_email(self, user_id: str) -> str | None:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(user_id)
            if hit is not None and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        email = self._email_of(user_id)
        with self._lock:
            self._cache[user_id] = (now, email)
        return email

    def email(self) -> str | None:
        user_id = self._user()
        email = self._cached_email(user_id)
        if not email and user_id != LOCAL_USER_ID:
            email = self._cached_email(LOCAL_USER_ID)
        return email

    def user_agent(self) -> str:
        configured = (env_str("OMNIX_SEC_USER_AGENT", "") or "").strip()
        if configured:
            return configured
        email = self.email()
        if not email:
            raise SecContactRequired()
        return f"{product_name()} {email}"


_DEFAULT = SecContact()


def sec_headers(**extra: str) -> dict[str, str]:
    """The headers of one SEC request; raises :class:`SecContactRequired` when there is no contact."""
    return {"User-Agent": _DEFAULT.user_agent(), "Accept-Encoding": "gzip, deflate", **extra}


__all__ = ["CONTACT_REQUIRED_MESSAGE", "SecContact", "SecContactRequired", "sec_headers"]
