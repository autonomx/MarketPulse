"""Listener policy shared by Omnix entrypoints.

Configuration moves to app.config in WP-2.3. Do not resolve hostnames here:
only numeric IP addresses and the literal localhost are accepted.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import ipaddress
import logging
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)

# Every interface of a container's own network namespace: only for servers
# inside a sandbox container, which the host reaches through a relay published
# on loopback (WP-4.7). Host listeners use bind_host().
CONTAINER_ALL_INTERFACES = "0.0.0.0"
_DEFAULT_ORIGINS = (
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
)


def bind_host(requested: str | None = None) -> str:
    """Return a validated listener address; public binding requires two opt-ins."""
    configured = (_env_str("OMNIX_BIND_HOST", "") or "").strip() or "127.0.0.1"
    host = configured if requested is None else str(requested).strip()
    if host == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("OMNIX_BIND_HOST must be an IP address or localhost") from exc
    if address.is_loopback:
        return host
    if configured != host or (_env_str("OMNIX_ALLOW_LAN", "") or "").strip().lower() != "true":
        raise ValueError("Non-loopback binding requires OMNIX_BIND_HOST and OMNIX_ALLOW_LAN=true")
    _LOGGER.warning("Omnix listener explicitly exposed beyond loopback: %s", host)
    return host


def allowed_origins() -> list[str]:
    """Return exact CORS origins, rejecting wildcard/credential combinations."""
    raw = _env_str("OMNIX_ALLOWED_ORIGINS")
    origins = list(_DEFAULT_ORIGINS) if raw is None else [v.strip() for v in raw.split(",") if v.strip()]
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or "*" in origin or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            raise ValueError("OMNIX_ALLOWED_ORIGINS must contain exact HTTP(S) origins")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("OMNIX_ALLOWED_ORIGINS contains an invalid port") from exc
    return list(dict.fromkeys(origins))
