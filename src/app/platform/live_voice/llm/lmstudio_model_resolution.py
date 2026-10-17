"""Prefer LM Studio's loaded LLM and use the configured model only as fallback.

LM Studio treats a supplied model identifier as a request to use or load that
model. A stale configured model therefore overrides a model that the user just
loaded in the LM Studio UI. Resolve the active loaded instance through the
native model-management API before each chat request, with a very short cache to
avoid duplicate discovery during speculative/final request pairs.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from app.providers.base import ConnectionError as ProviderConnectionError
from app.providers.lmstudio_provider import LMStudioProvider

from app.observability.tts_stream_diagnostics import stream_log

_DEFAULT_CACHE_TTL_SECONDS = 0.25
_DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 0.75
_DEFAULT_TRANSPORT_FALLBACK_MAX_SECONDS = 1.5
_HTTP_STATUS_RE = re.compile(r"\bHTTP error(?:\s+|:\s*)(\d{3})\b", re.IGNORECASE)
_NO_TRANSPORT_FALLBACK_HTTP_STATUSES = frozenset({401, 403, 408, 429})
_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class _LoadedModel:
    instance_id: str
    model_key: str
    display_name: str
    context_length: int | None = None

    def matches(self, value: str) -> bool:
        normalized = value.strip().casefold()
        return bool(normalized) and normalized in {
            self.instance_id.casefold(),
            self.model_key.casefold(),
            self.display_name.casefold(),
        }


@dataclass(frozen=True)
class _Discovery:
    available: bool
    endpoint: str | None
    models: tuple[_LoadedModel, ...]


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    discovery: _Discovery


_DISCOVERY_CACHE: WeakKeyDictionary[LMStudioProvider, _CacheEntry] = WeakKeyDictionary()


def _float_setting(name: str, fallback: float, *, minimum: float, maximum: float) -> float:
    raw = (_env_str(name) or "").strip()
    try:
        value = float(raw) if raw else fallback
    except ValueError:
        value = fallback
    return min(maximum, max(minimum, value))


def _cache_ttl_seconds() -> float:
    return _float_setting(
        "OMNIX_LMSTUDIO_MODEL_DISCOVERY_CACHE_SECONDS",
        _DEFAULT_CACHE_TTL_SECONDS,
        minimum=0.0,
        maximum=30.0,
    )


def _discovery_timeout_seconds(provider: LMStudioProvider) -> float:
    configured = _float_setting(
        "OMNIX_LMSTUDIO_MODEL_DISCOVERY_TIMEOUT_SECONDS",
        _DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        minimum=0.05,
        maximum=5.0,
    )
    try:
        provider_timeout = float(provider.config.timeout)
    except (TypeError, ValueError):
        provider_timeout = configured
    return max(0.05, min(configured, provider_timeout))


def _transport_fallback_max_seconds() -> float:
    return _float_setting(
        "OMNIX_LMSTUDIO_TRANSPORT_FALLBACK_MAX_SECONDS",
        _DEFAULT_TRANSPORT_FALLBACK_MAX_SECONDS,
        minimum=0.0,
        maximum=10.0,
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _deduplicate(models: list[_LoadedModel]) -> tuple[_LoadedModel, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[_LoadedModel] = []
    for model in models:
        key = (model.model_key.casefold(), model.instance_id.casefold())
        if not any(key) or key in seen:
            continue
        seen.add(key)
        unique.append(model)
    return tuple(unique)


def _parse_v1_models(payload: Any) -> tuple[_LoadedModel, ...] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None
    loaded: list[_LoadedModel] = []
    for model in payload["models"]:
        if not isinstance(model, dict):
            continue
        model_type = str(model.get("type") or "").strip().casefold()
        if model_type == "embedding":
            continue
        model_key = str(model.get("key") or "").strip()
        display_name = str(model.get("display_name") or model_key).strip()
        instances = model.get("loaded_instances")
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            instance_id = str(instance.get("id") or model_key).strip()
            request_model = model_key or instance_id
            if not request_model:
                continue
            config = instance.get("config")
            context_length = (
                _positive_int(config.get("context_length"))
                if isinstance(config, dict)
                else None
            )
            loaded.append(
                _LoadedModel(
                    instance_id=instance_id or request_model,
                    model_key=request_model,
                    display_name=display_name or request_model,
                    context_length=context_length,
                )
            )
    return _deduplicate(loaded)


def _parse_v0_models(payload: Any) -> tuple[_LoadedModel, ...] | None:
    raw_models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return None
    loaded: list[_LoadedModel] = []
    for model in raw_models:
        if not isinstance(model, dict):
            continue
        model_type = str(model.get("type") or "").strip().casefold()
        if model_type in {"embedding", "embeddings"}:
            continue
        state = str(model.get("state") or "").strip().casefold().replace("_", "-")
        if not state.startswith("loaded"):
            continue
        model_id = str(model.get("id") or model.get("model") or "").strip()
        if not model_id:
            continue
        loaded.append(
            _LoadedModel(
                instance_id=model_id,
                model_key=model_id,
                display_name=str(model.get("name") or model_id).strip(),
                context_length=_positive_int(
                    model.get("context_length")
                    or model.get("max_context_length")
                ),
            )
        )
    return _deduplicate(loaded)


def _fetch_json(provider: LMStudioProvider, endpoint: str) -> Any:
    response = provider._make_request(
        "get",
        endpoint,
        timeout=_discovery_timeout_seconds(provider),
    )
    return response.json()


def _discover_loaded_models_uncached(provider: LMStudioProvider) -> _Discovery:
    try:
        parsed = _parse_v1_models(_fetch_json(provider, "/api/v1/models"))
    except (AttributeError, OSError, ProviderConnectionError, TypeError, ValueError):
        parsed = None
    if parsed is not None:
        return _Discovery(available=True, endpoint="/api/v1/models", models=parsed)

    try:
        parsed = _parse_v0_models(_fetch_json(provider, "/api/v0/models"))
    except (AttributeError, OSError, ProviderConnectionError, TypeError, ValueError):
        parsed = None
    if parsed is not None:
        return _Discovery(available=True, endpoint="/api/v0/models", models=parsed)
    return _Discovery(available=False, endpoint=None, models=())


def _discover_loaded_models(provider: LMStudioProvider) -> tuple[_Discovery, bool]:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(provider)
        if cached is not None and cached.expires_at >= now:
            return cached.discovery, True

    discovery = _discover_loaded_models_uncached(provider)
    ttl = _cache_ttl_seconds()
    with _CACHE_LOCK:
        _DISCOVERY_CACHE[provider] = _CacheEntry(
            expires_at=time.monotonic() + ttl,
            discovery=discovery,
        )
    return discovery, False


def resolve_lmstudio_model(
    provider: LMStudioProvider,
    requested_model: str | None,
) -> tuple[str | None, dict[str, Any]]:
    explicit = str(requested_model or "").strip()
    configured = str(provider.config.model or "").strip()
    if explicit:
        return explicit, {
            "source": "explicit_request",
            "selected_model": explicit,
            "selected_model_key": explicit,
            "selected_instance_id": None,
            "selected_context_length": None,
            "configured_fallback": configured or None,
            "discovery_available": None,
            "discovery_endpoint": None,
            "discovery_cache_hit": None,
            "loaded_model_count": None,
        }

    discovery, cache_hit = _discover_loaded_models(provider)
    selected: str | None
    selected_model: _LoadedModel | None = None
    source: str
    if discovery.models:
        configured_match = next(
            (model for model in discovery.models if configured and model.matches(configured)),
            None,
        )
        selected_model = configured_match or discovery.models[0]
        # The v1 model list intentionally distinguishes the model key accepted by
        # inference APIs from the loaded instance ID used to identify that runtime
        # instance. Sending a custom instance ID as the chat model can be rejected.
        selected = selected_model.model_key
        source = (
            "loaded_model_key_config_match"
            if configured_match is not None
            else "loaded_model_key"
        )
    elif discovery.available and configured:
        selected = configured
        source = "configured_fallback_no_loaded_model"
    elif discovery.available:
        selected = None
        source = "runtime_default_no_loaded_model"
    else:
        # Do not send a possibly stale configured model when we could not prove
        # that LM Studio has no loaded model. Omitting it lets LM Studio use its
        # runtime default and avoids an unintended auto-load.
        selected = None
        source = "runtime_default_discovery_unavailable"

    return selected, {
        "source": source,
        "selected_model": selected,
        "selected_model_key": (
            selected_model.model_key if selected_model is not None else selected
        ),
        "selected_instance_id": (
            selected_model.instance_id if selected_model is not None else None
        ),
        "selected_context_length": (
            selected_model.context_length if selected_model is not None else None
        ),
        "configured_fallback": configured or None,
        "discovery_available": discovery.available,
        "discovery_endpoint": discovery.endpoint,
        "discovery_cache_hit": cache_hit,
        "loaded_model_count": len(discovery.models),
        "loaded_model_keys": [model.model_key for model in discovery.models],
        "loaded_instance_ids": [model.instance_id for model in discovery.models],
    }


def _clear_lmstudio_model_discovery_cache() -> None:
    """Clear the short-lived cache; exposed for deterministic tests."""
    with _CACHE_LOCK:
        _DISCOVERY_CACHE.clear()


def _http_status(error: BaseException) -> int | None:
    match = _HTTP_STATUS_RE.search(str(error or ""))
    return int(match.group(1)) if match is not None else None


def _should_retry_with_openai_transport(
    error: BaseException,
    *,
    elapsed_seconds: float,
) -> bool:
    if elapsed_seconds > _transport_fallback_max_seconds():
        return False
    message = str(error or "")
    if "http error" not in message.casefold():
        return False
    status = _http_status(error)
    return status not in _NO_TRANSPORT_FALLBACK_HTTP_STATUSES


def _update_active_diagnostics(
    resolved_model: str | None,
    diagnostics: dict[str, Any],
    *,
    transport_fallback: bool | None = None,
) -> None:
    try:
        from app.platform.live_voice.llm import lmstudio_diagnostics as diagnostics_runtime

        active = diagnostics_runtime._ACTIVE_CALL.get()
    except (AttributeError, ImportError):
        return
    if active is None:
        return
    previous_model = active.get("model_id")
    if previous_model and previous_model != resolved_model:
        active.setdefault("configured_model_id", previous_model)
    active.update(
        {
            "model_id": resolved_model,
            "selected_instance_id": diagnostics.get("selected_instance_id"),
            "selected_context_length": diagnostics.get("selected_context_length"),
        }
    )
    if transport_fallback is not None:
        active["transport_fallback"] = transport_fallback
        active["transport_endpoint"] = (
            "/v1/chat/completions"
            if transport_fallback
            else "/api/v0/chat/completions"
        )


def _log_transport_fallback(
    *,
    model: str | None,
    stream: bool,
    error: BaseException,
    elapsed_seconds: float,
) -> None:
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_transport_fallback",
        model_id=model,
        stream=bool(stream),
        failed_endpoint="/api/v0/chat/completions",
        fallback_endpoint="/v1/chat/completions",
        http_status=_http_status(error),
        error_type=type(error).__name__,
        elapsed_ms=round(elapsed_seconds * 1000.0, 3),
    )


def _stream_with_transport_fallback(
    primary: Iterator[Any],
    *,
    retry_chat_completion: Callable[[], Iterator[Any]],
    resolved_model: str | None,
    diagnostics: dict[str, Any],
    started: float,
) -> Iterator[Any]:
    emitted = False
    try:
        for chunk in primary:
            emitted = True
            yield chunk
    except ProviderConnectionError as exc:
        elapsed_seconds = time.perf_counter() - started
        if emitted or not _should_retry_with_openai_transport(
            exc,
            elapsed_seconds=elapsed_seconds,
        ):
            raise
        _log_transport_fallback(
            model=resolved_model,
            stream=True,
            error=exc,
            elapsed_seconds=elapsed_seconds,
        )
        _update_active_diagnostics(
            resolved_model,
            diagnostics,
            transport_fallback=True,
        )
        yield from retry_chat_completion()


def chat_completion_with_loaded_model(
    provider: LMStudioProvider,
    messages: list[Any],
    *,
    model: str | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> Any:
    """Resolve the loaded model and execute with a bounded transport fallback."""
    resolved_model, diagnostics = resolve_lmstudio_model(provider, model)
    log_fields = dict(diagnostics)
    model_source = log_fields.pop("source")
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_model_resolved",
        stream=bool(stream),
        model_source=model_source,
        **log_fields,
    )
    _update_active_diagnostics(
        resolved_model,
        diagnostics,
        transport_fallback=False,
    )
    call_kwargs = dict(kwargs)
    include_metrics = bool(call_kwargs.get("include_metrics", False))
    started = time.perf_counter()

    def execute(*, include_metrics_override: bool | None = None) -> Any:
        execute_kwargs = dict(call_kwargs)
        if include_metrics_override is not None:
            execute_kwargs["include_metrics"] = include_metrics_override
        return provider.chat_completion(
            messages,
            model=resolved_model,
            stream=stream,
            _use_configured_model=False,
            **execute_kwargs,
        )

    try:
        result = execute()
    except ProviderConnectionError as exc:
        elapsed_seconds = time.perf_counter() - started
        if not include_metrics or not _should_retry_with_openai_transport(
            exc,
            elapsed_seconds=elapsed_seconds,
        ):
            raise
        _log_transport_fallback(
            model=resolved_model,
            stream=stream,
            error=exc,
            elapsed_seconds=elapsed_seconds,
        )
        _update_active_diagnostics(
            resolved_model,
            diagnostics,
            transport_fallback=True,
        )
        return execute(include_metrics_override=False)

    if not stream or not include_metrics:
        return result
    return _stream_with_transport_fallback(
        result,
        retry_chat_completion=lambda: execute(include_metrics_override=False),
        resolved_model=resolved_model,
        diagnostics=diagnostics,
        started=started,
    )


__all__ = [
    "chat_completion_with_loaded_model",
    "resolve_lmstudio_model",
]
