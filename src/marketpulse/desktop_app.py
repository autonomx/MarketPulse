"""The desktop gateway application (roadmap MP-3.2): Omnix's gateway plus the built web app.

In Omnix, nginx serves the single-page app and adds the security headers, and proxies the gateway paths. On the
desktop there is no nginx: this raw ASGI wrapper does both in front of ``app.composition.production:app``.

- ``lifespan`` goes to the gateway unchanged (it initializes on lifespan; a Starlette ``Mount`` would not run it).
- ``/api``, ``/events``, ``/health`` and ``/ready`` (and websockets) go to the gateway.
- Everything else is a file from the built web app, or ``index.html`` for an app route.
- Every HTTP response gets nginx's security headers; app pages and assets also get its Content-Security-Policy.
"""
from __future__ import annotations

import mimetypes
import os
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

GATEWAY_PREFIXES = ("/api", "/events", "/health", "/ready")
# The same policy nginx sets for the app in Omnix (deploy/docker/nginx/omnix.conf).
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' data: blob:; worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"microphone=(self), display-capture=(self), camera=(), geolocation=(), payment=(), usb=()"),
)


def is_gateway_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in GATEWAY_PREFIXES)


def _with_headers(send: Send, extra: tuple[tuple[bytes, bytes], ...]) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            present = {name.lower() for name, _ in message.get("headers", [])}
            message["headers"] = [*message.get("headers", []), *(item for item in extra if item[0] not in present)]
        await send(message)

    return wrapped


class DesktopApplication:
    def __init__(self, gateway: ASGIApp, web_dist: Path | None) -> None:
        self.gateway = gateway
        self.web_dist = web_dist.resolve() if web_dist is not None else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan" or scope["type"] == "websocket":
            await self.gateway(scope, receive, send)
            return
        path = scope.get("path", "/")
        if is_gateway_path(path):
            await self.gateway(scope, receive, _with_headers(send, SECURITY_HEADERS))
            return
        await self._static(scope, receive, send, path)

    def _file_for(self, path: str) -> tuple[Path, bool] | None:
        """(file, is_asset): the requested file inside the build, or index.html for an app route."""
        if self.web_dist is None:
            return None
        relative = path.lstrip("/")
        if relative:
            candidate = (self.web_dist / relative).resolve()
            if candidate.is_relative_to(self.web_dist) and candidate.is_file():
                return candidate, relative.startswith("assets/")
            if "." in relative.rsplit("/", 1)[-1]:
                # A missing file (has an extension) is a 404, not the app.
                return None
        index = self.web_dist / "index.html"
        return (index, False) if index.is_file() else None

    async def _static(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        method = scope.get("method", "GET")
        found = self._file_for(path) if method in {"GET", "HEAD"} else None
        headers = [*SECURITY_HEADERS, (b"content-security-policy", CONTENT_SECURITY_POLICY.encode())]
        if found is None:
            status, body = (404, b"Not found") if method in {"GET", "HEAD"} else (405, b"Method not allowed")
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()), *headers]})
            await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})
            return
        file, is_asset = found
        data = file.read_bytes()
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        cache = b"public, max-age=31536000, immutable" if is_asset else b"no-cache"
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", content_type.encode()), (b"content-length", str(len(data)).encode()),
            (b"cache-control", cache), *headers]})
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else data})


def _create() -> DesktopApplication:
    from app.composition.production import ProductionApplication

    dist = os.environ.get("MARKETPULSE_WEB_DIST", "").strip()
    return DesktopApplication(ProductionApplication(), Path(dist) if dist else None)


mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")

app = _create()
