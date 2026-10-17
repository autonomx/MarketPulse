"""ASGI request guard for Host, browser Origin and unsafe HTTP methods."""
from __future__ import annotations
from app.config.env import env_str as _env_str

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.runtime.net import allowed_origins as configured_origins

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _parse_host(value: str) -> tuple[str, int | None] | None:
    if not value or any(char.isspace() for char in value) or any(char in value for char in "/\\?#@,"):
        return None
    try:
        parsed = urlsplit("//" + value)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if value.endswith(":"):
            return None
        hostname = parsed.hostname.lower()
        port = parsed.port
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None:
            authority += f":{port}"
        if value.lower() != authority:
            return None
        return hostname, port
    except ValueError:
        return None


class RequestGuardMiddleware:
    """Validate at construction, then protect HTTP and WebSocket requests."""

    def __init__(
        self, app: ASGIApp, *, allowed_hosts: Sequence[str] | None = None,
        allowed_origins: Sequence[str] | None = None,
    ) -> None:
        self.app = app
        hosts = allowed_hosts
        if hosts is None:
            extras = [v.strip() for v in (_env_str("OMNIX_ALLOWED_HOSTS", "") or "").split(",") if v.strip()]
            hosts = [*_DEFAULT_HOSTS, *extras]
        parsed_hosts = [_parse_host(host) for host in hosts]
        if any(host is None or "*" in host[0] for host in parsed_hosts):
            raise ValueError("OMNIX_ALLOWED_HOSTS must contain exact hostnames or IP addresses")
        self.allowed_hosts = frozenset(parsed_hosts)
        self.allowed_origins = frozenset(configured_origins() if allowed_origins is None else allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[str]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value.decode("latin-1"))
        hosts = headers.get(b"host", [])
        host = _parse_host(hosts[0]) if len(hosts) == 1 else None
        if host is None or (host not in self.allowed_hosts and (host[0], None) not in self.allowed_hosts):
            await self._reject(scope, receive, send, 421, "disallowed_host")
            return
        unsafe = scope["type"] == "http" and scope.get("method", "").upper() in _UNSAFE_METHODS
        if unsafe or scope["type"] == "websocket":
            origins = headers.get(b"origin", [])
            if origins and (len(origins) != 1 or origins[0] not in self.allowed_origins):
                await self._reject(scope, receive, send, 403, "disallowed_origin")
                return
        if unsafe:
            clients = headers.get(b"x-omnix-client", [])
            if len(clients) != 1 or not clients[0].strip():
                await self._reject(scope, receive, send, 403, "missing_client_header")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
        else:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
