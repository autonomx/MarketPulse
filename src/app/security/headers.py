"""Security response headers for the gateway (WP-4.10).

The gateway serves the API and a few HTML pages (API docs, agent previews);
the web app itself is served by Vite or the ingress, which set their own
policy. Headers a route already set are kept.
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Not a page: nothing may load, frame or run it.
API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# Gateway HTML (Swagger UI, previews) needs its scripts; it still cannot be framed.
HTML_CONTENT_SECURITY_POLICY = "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
PERMISSIONS_POLICY = (
    "microphone=(self), display-capture=(self), camera=(), geolocation=(), payment=(), usb=()"
)
HSTS = "max-age=31536000; includeSubDomains"


def _https(scope: Scope) -> bool:
    if scope.get("scheme") == "https":
        return True
    for key, value in scope.get("headers", []):
        if key.lower() == b"x-forwarded-proto":
            return value.decode("latin-1").split(",")[0].strip().lower() == "https"
    return False


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = _https(scope)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                html = headers.get("content-type", "").lower().startswith("text/html")
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
                # API data is personal; browsers and shared caches keep none
                # of it unless a route chose its own policy (ASVS 8.2.1).
                headers.setdefault("Cache-Control", "no-store")
                content_type = headers.get("content-type", "").lower()
                if content_type.startswith(("application/json", "application/problem+json")):
                    # A browser navigating to an API URL saves the JSON
                    # instead of rendering it (ASVS 14.4.2); fetch ignores it.
                    headers.setdefault("Content-Disposition", 'attachment; filename="api.json"')
                headers.setdefault(
                    "Content-Security-Policy",
                    HTML_CONTENT_SECURITY_POLICY if html else API_CONTENT_SECURITY_POLICY,
                )
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["SecurityHeadersMiddleware"]
