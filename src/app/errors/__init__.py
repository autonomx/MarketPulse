"""Stable cross-package exception contracts, and the gateway's error envelope (WP-10.5)."""
from __future__ import annotations

import logging
import re
import uuid
from http import HTTPStatus
from typing import Any

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
_CODE = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
_INTERNAL_DETAIL = "Internal server error. Quote the request id when reporting it."


class LegacyPersistenceRetired(RuntimeError):
    """Raised when normal runtime attempts to use retired SQLite/JSON authority."""


class DependencyUnavailable(RuntimeError):
    """A local dependency a request needs cannot be used right now.

    The gateway answers 503 with ``code``, the message and a recovery
    ``hint``; nothing the request asked for was changed.
    """

    code = "dependency_unavailable"

    def __init__(self, message: str, *, hint: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        if code is not None:
            self.code = code


def _status_code_name(status: int) -> str:
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "http_error"


def problem_code(status: int, detail: Any) -> str:
    """A machine code: the detail when it is already one, its ``error``/``code`` field, or the status."""
    if isinstance(detail, str) and _CODE.fullmatch(detail):
        return detail
    if isinstance(detail, dict):
        for key in ("code", "error"):
            value = detail.get(key)
            if isinstance(value, str) and _CODE.fullmatch(value):
                return value
    return _status_code_name(status)


def error_code(exc: BaseException, default: str) -> str:
    """The code an exception message starts with (``code`` or ``code:cause``), else ``default``.

    Service clients raise ``RuntimeError("image_service_unreachable:<cause>")``;
    the cause can name hosts, paths or a provider's reply, so routes return
    only the code and log the rest.
    """
    head = str(exc).split(":", 1)[0].strip()
    return head if _CODE.fullmatch(head) else default


def problem_body(status: int, detail: Any, *, path: str, request_id: str | None, code: str | None = None) -> dict[str, Any]:
    """RFC 9457 problem details; ``detail`` keeps the route's own value, so existing clients read it unchanged."""
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Error"
    return {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": path,
        "request_id": request_id,
        "code": code or problem_code(status, detail),
    }


def _request_id(request: Any) -> str:
    state = request.scope.get("state") or {}
    value = state.get("request_id") if isinstance(state, dict) else None
    return str(value) if value else uuid.uuid4().hex


def install_error_envelope(app: Any) -> None:
    """Route errors, validation errors and unhandled exceptions answer with problem details.

    An unhandled exception becomes a 500 with a generic detail and the request
    id; its stack trace goes to the log, never to the response.
    """
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    def respond(request: Any, status: int, detail: Any, *, code: str | None = None,
                headers: dict[str, str] | None = None) -> JSONResponse:
        request_id = _request_id(request)
        body = problem_body(status, detail, path=request.url.path, request_id=request_id, code=code)
        return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE,
                            headers={**(headers or {}), "X-Request-ID": request_id})

    async def http_error(request: Any, exc: StarletteHTTPException) -> Any:
        if exc.status_code in {204, 304} or exc.status_code < 200:
            from starlette.responses import Response

            return Response(status_code=exc.status_code, headers=getattr(exc, "headers", None))
        return respond(request, exc.status_code, exc.detail, headers=getattr(exc, "headers", None))

    async def validation_error(request: Any, exc: RequestValidationError) -> JSONResponse:
        from fastapi.encoders import jsonable_encoder

        errors = [redact_validation_error(error) for error in exc.errors()]
        # Validation failures are security events (ASVS 7.1.3); the rejected
        # values are not logged.
        logger.info("request_validation_failed path=%s errors=%d", request.url.path, len(errors))
        return respond(request, 422, jsonable_encoder(errors), code="invalid_request")

    async def dependency_unavailable(request: Any, exc: DependencyUnavailable) -> JSONResponse:
        logger.warning("dependency_unavailable path=%s code=%s", request.url.path, exc.code)
        detail = {"code": exc.code, "message": str(exc), "hint": exc.hint}
        return respond(request, 503, detail, code=exc.code)

    async def unhandled_error(request: Any, exc: Exception) -> JSONResponse:
        from app.observability.logging import log_context

        # Starlette calls this outside the request's middleware, after its log
        # context has ended: bind the id again so the stack trace carries it.
        with log_context(request_id=_request_id(request)):
            logger.exception("unhandled_request_error path=%s", request.url.path)
        return respond(request, 500, _INTERNAL_DETAIL, code="internal_error")

    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(DependencyUnavailable, dependency_unavailable)
    app.add_exception_handler(Exception, unhandled_error)


_SECRET_KEY_MARKERS = (
    "secret", "password", "api_key", "apikey", "api-key", "token", "credential", "authorization", "private_key",
)
# Fields that are credentials only under a given parent (a webhook URL embeds its token).
_SECRET_CHILD_KEYS = {"webhook": {"url"}}
_REDACTED = "[redacted]"


def _is_secret_key(key: object, parent: object = None) -> bool:
    name = str(key).lower()
    if any(marker in name for marker in _SECRET_KEY_MARKERS):
        return True
    return name in _SECRET_CHILD_KEYS.get(str(parent).lower(), set())


def _redact(value: Any, parent: object = None) -> Any:
    if isinstance(value, dict):
        return {key: _REDACTED if _is_secret_key(key, parent) else _redact(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, parent) for item in value]
    return value


def redact_validation_error(error: dict[str, Any]) -> dict[str, Any]:
    """A validation error safe to send back: no credential's value.

    A model-level error echoes the whole request body as ``input``, and a
    field error on a credential echoes the credential itself.
    """
    redacted = dict(error)
    location = [part for part in (redacted.get("loc") or ()) if isinstance(part, str)]
    if "input" in redacted:
        secret_field = any(
            _is_secret_key(part, location[index - 1] if index else None) for index, part in enumerate(location)
        )
        parent = location[-1] if location else None
        redacted["input"] = _REDACTED if secret_field else _redact(redacted["input"], parent)
    if "ctx" in redacted:
        redacted["ctx"] = _redact(redacted["ctx"])
    return redacted


__all__ = [
    "DependencyUnavailable",
    "LegacyPersistenceRetired",
    "PROBLEM_MEDIA_TYPE",
    "error_code",
    "install_error_envelope",
    "problem_body",
    "problem_code",
    "redact_validation_error",
]
