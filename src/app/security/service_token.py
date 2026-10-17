"""Authentication dependency for operator-issued internal service credentials."""
from __future__ import annotations

import hmac
import re

from fastapi import HTTPException, Request

from app.config.env import env_str
from app.observability.logging import current_log_context


def valid_service_token(supplied: list[str]) -> bool:
    """Accept exactly one credential against the currently issued environment token."""
    expected = env_str("OMNIX_SERVICE_TOKEN", "") or ""
    return bool(
        re.fullmatch(r"[A-Za-z0-9_-]{43,}", expected)
        and len(supplied) == 1
        and hmac.compare_digest(expected.encode("utf-8"), supplied[0].encode("utf-8"))
    )


def service_headers() -> dict[str, str]:
    """Headers for an Omnix sidecar only; callers must disable redirects."""
    token = env_str("OMNIX_SERVICE_TOKEN", "") or ""
    if not valid_service_token([token]):
        raise RuntimeError("service_credential_unavailable")
    headers = {"X-Omnix-Client": "gateway", "X-Omnix-Service-Token": token}
    # The service logs under the gateway's request id (WP-10.2).
    request_id = current_log_context().get("request_id")
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def require_service_token(request: Request) -> None:
    supplied = request.headers.getlist("x-omnix-service-token")
    # token_urlsafe(32) supplies at least 256 bits and produces 43 characters.
    if not valid_service_token(supplied):
        raise HTTPException(status_code=401, detail="invalid_service_token")
