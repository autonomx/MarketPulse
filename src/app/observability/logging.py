"""Structured logging with request and job context (WP-10.1, WP-10.2).

``configure_logging`` installs one stream handler on the root logger. Every
record carries the ids bound with ``log_context``: the request, job, attempt,
agent run, workspace, user (hashed) and feature. ``OMNIX_LOG_FORMAT`` selects
``text`` (default) or ``json``; ``OMNIX_LOG_LEVEL`` sets the root level and
``OMNIX_LOG_LEVELS`` per-logger levels (``"uvicorn.access=WARNING,omnix.tts=DEBUG"``).

``RequestContextMiddleware`` gives every HTTP request and WebSocket an id: a
valid inbound ``X-Request-ID`` is kept, otherwise one is generated. The id is
bound for the request's log lines, stored in ``scope["state"]["request_id"]``
and returned in the ``X-Request-ID`` response header.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from app.config.env import env_str, environment
from app.observability.tracing import current_trace_id, set_span_attributes

CONTEXT_FIELDS = ("request_id", "job_id", "attempt", "run_id", "workspace_id", "user_id", "feature")
# Fields each record carries: the bound ids plus the active trace (WP-10.4).
RECORD_FIELDS = (*CONTEXT_FIELDS, "trace_id")
_context: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar("omnix_log_context", default={})
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{8,128}")
_HANDLER_MARKER = "_omnix_configured_handler"


def _hashed_user(value: str) -> str:
    # Log lines name a user without carrying the account identifier itself.
    return "user:" + hashlib.sha256(f"omnix-log-user\0{value}".encode()).hexdigest()[:16]


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind ids for every log line emitted inside the block (nested blocks add to it)."""
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"unknown log context fields: {sorted(unknown)}")
    values = {name: str(value) for name, value in fields.items() if value is not None and value != ""}
    if "user_id" in values:
        values["user_id"] = _hashed_user(values["user_id"])
    token = _context.set({**_context.get(), **values})
    try:
        yield
    finally:
        _context.reset(token)


def current_log_context() -> dict[str, str]:
    return dict(_context.get())


class ContextFilter(logging.Filter):
    """Copy the bound ids onto each record (``None`` when unbound)."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _context.get()
        for name in CONTEXT_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, context.get(name))
        if not hasattr(record, "trace_id"):
            record.trace_id = current_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in RECORD_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        ids = " ".join(
            f"{name}={getattr(record, name)}" for name in RECORD_FIELDS if getattr(record, name, None) is not None
        )
        return f"{line} [{ids}]" if ids else line


def configure_logging(*, log_format: str | None = None, level: str | None = None, stream: Any = None) -> logging.Handler:
    """Install (or replace) the process's one structured log handler."""
    chosen = (log_format or env_str("OMNIX_LOG_FORMAT", "text") or "text").strip().lower()
    if chosen not in {"text", "json"}:
        raise ValueError("OMNIX_LOG_FORMAT must be text or json")
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if chosen == "json" else TextFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel((level or env_str("OMNIX_LOG_LEVEL", "INFO") or "INFO").strip().upper() or "INFO")
    for entry in (env_str("OMNIX_LOG_LEVELS", "") or "").split(","):
        name, _, value = entry.partition("=")
        if name.strip() and value.strip():
            logging.getLogger(name.strip()).setLevel(value.strip().upper())
    return handler


def process_log_name() -> str:
    """This process's name in per-process log files.

    ``OMNIX_INSTANCE_NAME`` when set (one per replica), else the process role.
    Each process rotates its own file, so a rotation never renames a file
    another process is writing.
    """
    raw = (
        environment().get("OMNIX_INSTANCE_NAME", "")
        or environment().get("OMNIX_GATEWAY_BACKGROUND_ROLE", "")
        or "worker"
    ).strip()
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return name[:64] or "worker"


def request_id_from_header(value: str | None) -> str:
    """A valid inbound request id, or a new one."""
    if value and _REQUEST_ID.fullmatch(value):
        return value
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Bind a request id for each HTTP request and WebSocket (pure ASGI, streaming-safe)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        inbound = next(
            (value.decode("latin-1") for key, value in scope.get("headers", []) if key == b"x-request-id"),
            None,
        )
        request_id = request_id_from_header(inbound)
        scope.setdefault("state", {})["request_id"] = request_id
        # The server span (when tracing is on) names the request id it serves.
        set_span_attributes(request_id=request_id)
        header = (b"x-request-id", request_id.encode("ascii"))

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] in {"http.response.start", "websocket.accept"}:
                headers = [item for item in message.get("headers", []) if item[0].lower() != b"x-request-id"]
                message = {**message, "headers": [*headers, header]}
            await send(message)

        with log_context(request_id=request_id):
            await self.app(scope, receive, send_with_id)


__all__ = [
    "process_log_name",
    "CONTEXT_FIELDS",
    "RECORD_FIELDS",
    "ContextFilter",
    "JsonFormatter",
    "RequestContextMiddleware",
    "TextFormatter",
    "configure_logging",
    "current_log_context",
    "log_context",
    "request_id_from_header",
]
