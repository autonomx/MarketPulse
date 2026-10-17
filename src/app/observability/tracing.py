"""Optional OpenTelemetry tracing (WP-10.4).

Tracing is off unless ``OMNIX_OTEL_ENABLED=true`` and the optional packages
(``requirements/tracing.in``) are installed. The exporter is OTLP over HTTP,
configured by the standard ``OTEL_EXPORTER_OTLP_*`` variables. When it is off,
``span`` is a no-op and nothing from ``opentelemetry`` is imported.

``configure_tracing`` instruments httpx and psycopg for the process;
``instrument_app`` adds the FastAPI server spans to one application. Manual
spans cover job execution, RPG turn stages, live speech stages and agent
steps. The active trace id is added to log lines (``trace_id``).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config.env import env_bool, env_str

logger = logging.getLogger(__name__)

_TRACER_NAME = "omnix"
# Probes and scrapes would otherwise dominate the traces.
_UNTRACED_URLS = "/health$,/health/ready$,/metrics$"
_lock = threading.Lock()
_provider: Any = None
_tracer: Any = None


def tracing_enabled() -> bool:
    return env_bool("OMNIX_OTEL_ENABLED", False)


def tracing_active() -> bool:
    return _tracer is not None


def configure_tracing(*, service_name: str, exporter: Any = None) -> bool:
    """Start tracing for this process; ``exporter`` replaces OTLP (tests use an in-memory one).

    Returns whether tracing is active. Without ``exporter``, tracing starts only
    when ``OMNIX_OTEL_ENABLED`` is true; missing packages leave it off with a warning.
    """
    global _provider, _tracer
    if exporter is None and not tracing_enabled():
        return False
    with _lock:
        if _tracer is not None:
            return True
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        except ImportError:
            logger.warning("OMNIX_OTEL_ENABLED is set but OpenTelemetry is not installed (requirements/tracing.lock.txt)")
            return False
        name = env_str("OTEL_SERVICE_NAME", None) or service_name
        provider = TracerProvider(resource=Resource.create({"service.name": name}))
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        else:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        # Statement text stays in the span; no SQL comments are added to queries.
        PsycopgInstrumentor().instrument(tracer_provider=provider, enable_commenter=False)
        _provider = provider
        _tracer = provider.get_tracer(_TRACER_NAME)
    logger.info("OpenTelemetry tracing enabled service=%s", name)
    return True


def shutdown_tracing() -> None:
    """Flush spans and remove the process instrumentation (process exit and tests)."""
    global _provider, _tracer
    with _lock:
        provider, _provider, _tracer = _provider, None, None
        if provider is None:
            return
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        HTTPXClientInstrumentor().uninstrument()
        PsycopgInstrumentor().uninstrument()
    provider.shutdown()


def instrument_app(app: Any) -> None:
    """Add server spans to a FastAPI application when tracing is active."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider, excluded_urls=_UNTRACED_URLS)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """A child span of the current one; yields ``None`` when tracing is off."""
    tracer = _tracer
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name, attributes=_attributes(attributes)) as current:
        yield current


def record_span(name: str, *, start_ms: int, end_ms: int, **attributes: Any) -> None:
    """Record an already finished stage (event-driven pipelines mark times rather than wrap calls)."""
    tracer = _tracer
    if tracer is None or end_ms < start_ms:
        return
    finished = tracer.start_span(name, start_time=start_ms * 1_000_000, attributes=_attributes(attributes))
    finished.end(end_time=end_ms * 1_000_000)


def set_span_attributes(**attributes: Any) -> None:
    """Annotate the current span (for example the server span of an agent step)."""
    if _tracer is None:
        return
    from opentelemetry import trace

    current = trace.get_current_span()
    if current.is_recording():
        current.set_attributes(_attributes(attributes))


def annotate(current: Any, fields: dict[str, Any]) -> None:
    """Copy a stage's scalar measurements onto its span (nested values are left out)."""
    if current is None or not current.is_recording():
        return
    scalars = {
        key: value for key, value in fields.items()
        if isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 256)
    }
    current.set_attributes(_attributes(scalars))


def current_trace_id() -> str | None:
    """The active trace id as 32 hex characters, for log lines."""
    if _tracer is None:
        return None
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def _attributes(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        result[f"omnix.{key}"] = value if isinstance(value, (str, bool, int, float)) else str(value)
    return result


__all__ = [
    "annotate", "configure_tracing", "current_trace_id", "instrument_app", "record_span", "set_span_attributes",
    "shutdown_tracing", "span", "tracing_active", "tracing_enabled",
]
