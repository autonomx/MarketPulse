"""Keep live-voice persistence and provider lookup off the gateway event loop."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import queue
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter

from app.providers.service import get_tts_provider

from app.observability.tts_stream_diagnostics import active_streams_snapshot, stream_log

_STATE_SENTINEL = "_omnix_live_voice_runtime_offload_registered"
_DEFAULT_PROVIDER_REFRESH_SECONDS = 5.0
_DEFAULT_DELIVERY_QUEUE_SIZE = 128
_PROVIDER_RESOLVER: CachedTtsProviderResolver | None = None
_PERSISTENCE_WORKER = None


def _provider_refresh_enabled_for_process(config=None) -> bool:
    from app.runtime.config import get_runtime_config
    from app.runtime.capabilities import RuntimeCapabilities, RuntimeCapability
    capabilities = RuntimeCapabilities.from_config(config or get_runtime_config())
    return any(capabilities.allows(value) for value in (RuntimeCapability.RUN_LOCAL_TTS, RuntimeCapability.USE_REMOTE_TTS))


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(_env_str(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(_env_str(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class DeliveryPersistenceWorker:
    """Serialize delivery checkpoints on a bounded daemon-worker queue."""

    def __init__(
        self,
        persist: Callable[[dict[str, Any]], None],
        *,
        max_queue_size: int | None = None,
        log: Callable[..., None] = stream_log,
    ) -> None:
        self._persist = persist
        self._log = log
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=max_queue_size
            or _env_int(
                "OMNIX_LIVE_VOICE_DELIVERY_QUEUE_SIZE",
                _DEFAULT_DELIVERY_QUEUE_SIZE,
                minimum=8,
                maximum=2048,
            )
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopped = False

    def enqueue(self, details: Mapping[str, Any]) -> None:
        payload = dict(details)
        if not self._ensure_started():
            self._log("gateway-live-voice-runtime", "runtime", "delivery_persistence_stopped")
            return
        try:
            self._queue.put_nowait(payload)
            return
        except queue.Full:
            pass

        # Delivery checkpoints are cumulative. Prefer the newest checkpoint when
        # persistence temporarily falls behind instead of blocking the caller.
        try:
            self._queue.get_nowait()
            self._queue.task_done()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._log(
                "gateway-live-voice-runtime",
                "runtime",
                "delivery_persistence_checkpoint_dropped",
            )

    def diagnostics(self):
        with self._lock:
            return {"queued": self._queue.qsize(), "capacity": self._queue.maxsize,
                    "stopped": self._stopped, "thread_alive": bool(self._thread and self._thread.is_alive())}

    def stop(self, timeout: float = 0.25) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def start(self) -> None:
        with self._lock:
            if not self._stopped:
                return
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Previous delivery persistence worker is still stopping")
            self._queue = queue.Queue(maxsize=self._queue.maxsize)
            self._thread = None
            self._stopped = False

    def _ensure_started(self) -> bool:
        with self._lock:
            if self._stopped:
                return False
            if self._thread is not None:
                return True
            self._thread = threading.Thread(
                target=self._run,
                name="omnix-live-voice-delivery",
                daemon=True,
            )
            self._thread.start()
            return True

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            try:
                if payload is None:
                    return
                started_at = time.perf_counter()
                self._persist(payload)
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                if elapsed_ms >= 25.0:
                    self._log(
                        "gateway-live-voice-runtime",
                        "runtime",
                        "delivery_persistence_completed",
                        elapsed_ms=round(elapsed_ms, 3),
                    )
            except Exception as exc:  # pragma: no cover - defensive persistence guard.
                self._log(
                    "gateway-live-voice-runtime",
                    "runtime",
                    "delivery_persistence_failed",
                    error_type=type(exc).__name__,
                )
            finally:
                self._queue.task_done()


class CachedTtsProviderResolver:
    """Return a warmed provider immediately and refresh settings while idle."""

    def __init__(
        self,
        resolve: Callable[..., Any],
        *,
        active_streams: Callable[[], dict[str, Any]] = active_streams_snapshot,
        refresh_seconds: float | None = None,
        log: Callable[..., None] = stream_log,
    ) -> None:
        self._resolve = resolve
        self._active_streams = active_streams
        self._refresh_seconds = refresh_seconds or _env_float(
            "OMNIX_LIVE_VOICE_PROVIDER_REFRESH_SECONDS",
            _DEFAULT_PROVIDER_REFRESH_SECONDS,
            minimum=0.25,
        )
        self._log = log
        self._lock = threading.Lock()
        self._provider: Any = None
        self._resolved_at = 0.0
        self._last_refresh_error = None
        self._refreshing = False
        self._refresh_complete = threading.Event()
        self._refresh_complete.set()
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="omnix-live-voice-provider-monitor",
                daemon=True,
            )
            self._monitor_thread.start()

    def diagnostics(self):
        with self._lock:
            return {"provider_available": self._provider is not None, "refreshing": self._refreshing,
                    "last_refresh_error_class": self._last_refresh_error,
                    "last_success_age_seconds": time.perf_counter() - self._resolved_at if self._resolved_at else None}

    def stop(self, timeout: float = 0.25) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._monitor_thread
            self._monitor_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def get(self, provider_name: str | None = None) -> Any:
        if provider_name is not None:
            return self._resolve(provider_name)

        now = time.perf_counter()
        with self._lock:
            provider = self._provider
            stale = provider is not None and now - self._resolved_at >= self._refresh_seconds

        if provider is None:
            resolved = self.refresh()
            if resolved is None:
                # Startup warming may still be in flight when the first live
                # voice request arrives. Wait for that same refresh instead of
                # returning an unavailable provider prematurely.
                self._refresh_complete.wait()
                with self._lock:
                    return self._provider
            return resolved
        if stale and not self._active_streams():
            self.refresh_in_background()
        return provider

    def is_warm(self) -> bool:
        """True when ``get()`` returns the cached provider without blocking."""
        with self._lock:
            return self._provider is not None

    def refresh(self) -> Any:
        if not self._begin_refresh():
            with self._lock:
                return self._provider
        return self._run_started_refresh()

    def refresh_in_background(self) -> bool:
        if not self._begin_refresh():
            return False
        threading.Thread(
            target=self._run_started_refresh,
            name="omnix-live-voice-provider-refresh",
            daemon=True,
        ).start()
        return True

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(self._refresh_seconds):
            if not self._active_streams():
                self.refresh()

    def _begin_refresh(self) -> bool:
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True
            self._refresh_complete.clear()
            return True

    def _run_started_refresh(self) -> Any:
        started_at = time.perf_counter()
        resolved: Any = None
        error_type: str | None = None
        try:
            resolved = self._resolve()
            return resolved
        except Exception as exc:  # pragma: no cover - preserve the previous provider.
            error_type = type(exc).__name__
            with self._lock:
                return self._provider
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            with self._lock:
                if resolved is not None:
                    self._provider = resolved
                    self._resolved_at = time.perf_counter()
                provider = self._provider
                self._last_refresh_error = error_type or ("ProviderUnavailable" if resolved is None else None)
                self._refreshing = False
                self._refresh_complete.set()
            self._log(
                "gateway-live-voice-runtime",
                "runtime",
                "tts_provider_cache_refreshed" if error_type is None else "tts_provider_cache_refresh_failed",
                elapsed_ms=round(elapsed_ms, 3),
                error_type=error_type,
                provider_class=(
                    f"{type(provider).__module__}.{type(provider).__qualname__}"
                    if provider is not None
                    else None
                ),
                provider_name=getattr(provider, "provider_name", None),
            )


def get_cached_live_tts_provider(provider_name: str | None = None) -> Any:
    """Return the warmed shared provider without coupling to the websocket module."""
    resolver = _PROVIDER_RESOLVER
    if resolver is None:
        return get_tts_provider(provider_name)
    return resolver.get(provider_name)


def register_live_voice_runtime_offload(router: APIRouter, state: Any) -> None:
    if getattr(state, _STATE_SENTINEL, False):
        return
    configure_live_voice_runtime_offload()
    setattr(state, _STATE_SENTINEL, True)
    state.live_voice_delivery_persistence_worker = _PERSISTENCE_WORKER
    state.live_voice_tts_provider_resolver = _PROVIDER_RESOLVER
    config = getattr(state, 'runtime_config', None)

    async def startup():
        _PERSISTENCE_WORKER.start()
        if not _provider_refresh_enabled_for_process(config):
            stream_log(
                'gateway-live-voice-runtime', 'runtime',
                'tts_provider_refresh_disabled_for_api_replica',
            )
            return
        _PROVIDER_RESOLVER.refresh_in_background()
        _PROVIDER_RESOLVER.start()
        stream_log('gateway-live-voice-runtime', 'runtime', 'live_voice_runtime_offload_started')

    async def shutdown():
        import asyncio
        await asyncio.to_thread(_PROVIDER_RESOLVER.stop)
        await asyncio.to_thread(_PERSISTENCE_WORKER.stop)

    router.add_event_handler("startup", startup)
    router.add_event_handler("shutdown", shutdown)


def configure_live_voice_runtime_offload() -> None:
    """Compose bounded persistence and provider resolution for live voice."""
    from app.platform.live_voice import diagnostics

    global _PROVIDER_RESOLVER, _PERSISTENCE_WORKER
    if _PERSISTENCE_WORKER is None:
        _PERSISTENCE_WORKER = DeliveryPersistenceWorker(
            diagnostics.persist_delivery_checkpoint
        )
    if _PROVIDER_RESOLVER is None:
        _PROVIDER_RESOLVER = CachedTtsProviderResolver(get_tts_provider)
    diagnostics.configure_delivery_checkpoint_dispatcher(_PERSISTENCE_WORKER.enqueue)
