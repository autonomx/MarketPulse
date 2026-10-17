"""Model service boundary: guarded hosts, service authentication and bounded uploads."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import logging

from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from app.observability.logging import log_context, request_id_from_header
from app.security.request_guard import RequestGuardMiddleware
from app.security.service_token import valid_service_token

logger = logging.getLogger(__name__)
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ERROR_CODES = {
    400: "invalid_request", 401: "invalid_service_token", 403: "forbidden",
    404: "not_found", 405: "method_not_allowed", 409: "conflict",
    413: "upload_too_large", 421: "disallowed_host", 422: "invalid_request",
    429: "rate_limited", 503: "model_unavailable",
}


def max_upload_bytes() -> int:
    raw = _env_str("OMNIX_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
    if raw is None:
        raw = str(DEFAULT_MAX_UPLOAD_BYTES)
    if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        raise ValueError("OMNIX_MAX_UPLOAD_BYTES must be a positive integer")
    return int(raw)


class UploadTooLarge(MultiPartException):
    """Raised before a request chunk exceeding the configured budget is delivered."""


class ModelServiceMiddleware:
    """Protect all routes, including docs and WebSockets, except GET/HEAD /health.

    Counts ASGI request bytes, before multipart parsing. The limit remains binding
    if a route catches the receive exception and tries to return another response.
    Error response bodies are replaced without buffering provider output.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limit = max_upload_bytes()
        self.guard = RequestGuardMiddleware(self._authenticated)

    async def _authenticated(self, scope: Scope, receive: Receive, send: Send) -> None:
        public_health = (
            scope["type"] == "http" and scope.get("path") == "/health"
            and scope.get("method") in {"GET", "HEAD"}
        )
        supplied = [v.decode("latin-1") for k, v in scope.get("headers", [])
                    if k.lower() == b"x-omnix-service-token"]
        if not public_health and not valid_service_token(supplied):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008,
                            "reason": "invalid_service_token"})
            else:
                await JSONResponse({}, status_code=401)(scope, receive, send)
            return
        lengths = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
        if scope["type"] == "http" and lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                await JSONResponse({}, status_code=400)(scope, receive, send)
                return
            if int(lengths[0]) > self.limit:
                await JSONResponse({}, status_code=413)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        # A valid inbound id (the gateway forwards its own) is kept, so both
        # processes log one id for the call (WP-10.2).
        request_id = request_id_from_header(next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"), None,
        ))
        scope.setdefault("state", {})["request_id"] = request_id
        consumed = 0
        oversized = False
        started = False
        closed = False
        response_start: Message | None = None
        failure_status: int | None = None

        async def bounded_receive() -> Message:
            nonlocal consumed, oversized, closed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.limit:
                    oversized = True
                    # Starlette closes partial multipart spool files on this error.
                    raise UploadTooLarge("upload_too_large")
            elif message["type"] == "websocket.receive":
                size = len(message.get("bytes") or b"") + len((message.get("text") or "").encode("utf-8"))
                if size > self.limit:
                    closed = True
                    await send({"type": "websocket.close", "code": 1009,
                                "reason": "upload_too_large"})
                    raise WebSocketDisconnect(code=1009)
            return message

        async def error_response(status: int) -> None:
            nonlocal started, closed
            code = _ERROR_CODES.get(status, "model_service_error")
            headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
            if status == 429 and response_start is not None:
                retry_after = [
                    value for key, value in response_start.get("headers", [])
                    if key.lower() == b"retry-after"
                ]
                if len(retry_after) == 1 and retry_after[0].isascii() and retry_after[0].isdigit():
                    headers["Retry-After"] = retry_after[0].decode("ascii")
            response = JSONResponse({"error": code, "request_id": request_id}, status_code=status,
                                    headers=headers)
            started = True
            await response(scope, receive, send)
            closed = True

        async def safe_send(message: Message) -> None:
            nonlocal response_start, failure_status, started, closed
            if closed:
                return
            if message["type"] == "http.response.start":
                response_start = message
                status = 413 if oversized else message["status"]
                if status >= 400:
                    failure_status = status
                return
            if message["type"] == "http.response.body":
                if not started:
                    if oversized or failure_status is not None:
                        status = 413 if oversized else failure_status
                        assert status is not None
                        await error_response(status)
                        return
                    assert response_start is not None
                    headers = [(k, v) for k, v in response_start.get("headers", [])
                               if k.lower() != b"x-request-id"]
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                    await send({**response_start, "headers": headers})
                    started = True
                await send(message)
                if not message.get("more_body", False):
                    closed = True
                return
            if message["type"] == "websocket.close":
                closed = True
            await send(message)

        try:
            with log_context(request_id=request_id):
                await self.guard(scope, bounded_receive, safe_send)
        except WebSocketDisconnect:
            if scope["type"] != "websocket":
                raise
        except Exception as exc:
            logger.error("model_service_request_failed request_id=%s error_type=%s",
                         request_id, type(exc).__name__)
            if not closed and scope["type"] == "http":
                if not started:
                    await error_response(413 if oversized else 500)
                else:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            elif not closed:
                await send({"type": "websocket.close", "code": 1011,
                            "reason": "model_service_error"})
