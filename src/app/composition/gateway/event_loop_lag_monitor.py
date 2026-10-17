"""Gateway event-loop lag watchdog with contention snapshots.

The watchdog intentionally runs on a daemon thread. It can therefore observe when
FastAPI's asyncio loop stops advancing and record Python thread stacks without
waiting for the loop itself to recover. Stack payloads contain code locations
only; frame locals and user content are never inspected.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import asyncio
import os
import sys
import threading
import time
import traceback
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI

from app.observability.tts_stream_diagnostics import active_streams_snapshot, stream_log

_ROUTE_SENTINEL = "_omnix_event_loop_lag_monitor_registered"
_MONITOR_STATE_KEY = "_omnix_event_loop_lag_monitor"
_DEFAULT_INTERVAL_SECONDS = 0.025
_DEFAULT_THRESHOLD_SECONDS = 0.025
_DEFAULT_REFRESH_SECONDS = 0.250
_DEFAULT_STACK_LIMIT = 10


def _env_flag(name: str, default: str = "1") -> bool:
    return (_env_str(name, default) or default).strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _safe_code_path(filename: str) -> str:
    """Keep useful repository context without persisting a full user path."""
    path = Path(filename)
    parts = path.parts
    for marker in ("src", "apps", "scripts", "tests"):
        if marker in parts:
            return "/".join(parts[parts.index(marker) :])
    return path.name


def classify_stack(stack: list[dict[str, Any]]) -> str:
    """Classify a sanitized Python stack into a likely contention source."""
    text = " ".join(
        f"{frame.get('file', '')} {frame.get('function', '')}" for frame in stack
    ).casefold()

    if any(
        token in text
        for token in (
            "qwen3_tts",
            "faster_qwen3_tts",
            "speech_tokenizer",
            "generate_voice_clone",
            "tts_provider",
        )
    ):
        return "tts_model_or_waveform_decoder"
    if any(
        token in text
        for token in (
            "lmstudio",
            "openrouter",
            "cerebras",
            "llamacpp",
            "stream_provider_reply",
            "chat_generation",
            "generate_chat",
            "provider_reply",
        )
    ):
        return "llm_streaming"
    if any(token in text for token in ("torch/", "torch\\", "transformers", "tokenizers")):
        return "pytorch_or_tokenizer_runtime"
    if "tts_live_call_websocket" in text and any(
        token in text for token in (" event.wait", " threading.py wait", " acquire")
    ):
        return "tts_first_frame_wait"
    if any(token in text for token in ("app/composition/gateway", "starlette", "fastapi", "uvicorn")):
        return "gateway_or_framework_callback"
    if any(token in text for token in ("asyncio", "selectors.py", "proactor_events")):
        return "asyncio_idle_or_io"
    if any(token in text for token in ("logging", "queue.py", "queuehandler")):
        return "diagnostics_logging"
    return "other_python_work"


def _thread_stacks(
    *,
    event_loop_thread_id: int | None,
    stack_limit: int,
) -> list[dict[str, Any]]:
    frames = sys._current_frames()
    threads = {thread.ident: thread for thread in threading.enumerate() if thread.ident is not None}
    payload: list[dict[str, Any]] = []

    for thread_id, frame in frames.items():
        thread = threads.get(thread_id)
        extracted = traceback.extract_stack(frame, limit=stack_limit)
        stack = [
            {
                "file": _safe_code_path(item.filename),
                "line": int(item.lineno or 0),
                "function": item.name,
            }
            for item in extracted
        ]
        payload.append(
            {
                "thread_id": thread_id,
                "thread_name": thread.name if thread is not None else "unknown",
                "daemon": bool(thread.daemon) if thread is not None else None,
                "is_event_loop_thread": thread_id == event_loop_thread_id,
                "category": classify_stack(stack),
                "top_frame": stack[-1] if stack else None,
                "stack": stack,
            }
        )

    payload.sort(
        key=lambda item: (
            not bool(item["is_event_loop_thread"]),
            str(item["thread_name"]),
            int(item["thread_id"]),
        )
    )
    return payload


def _runtime_parallelism() -> dict[str, Any]:
    details: dict[str, Any] = {
        "python_thread_count": threading.active_count(),
        "python_switch_interval_ms": round(sys.getswitchinterval() * 1000, 3),
        "cpu_count": os.cpu_count(),
    }
    torch_module = sys.modules.get("torch")
    if torch_module is None:
        return details

    with suppress(Exception):
        details["torch_num_threads"] = int(torch_module.get_num_threads())
    with suppress(Exception):
        details["torch_num_interop_threads"] = int(torch_module.get_num_interop_threads())
    with suppress(Exception):
        cuda = torch_module.cuda
        details["cuda_available"] = bool(cuda.is_available())
        if details["cuda_available"]:
            details["cuda_memory_allocated_bytes"] = int(cuda.memory_allocated())
            details["cuda_memory_reserved_bytes"] = int(cuda.memory_reserved())
    return details


def capture_runtime_contention_snapshot(
    *,
    event_loop_thread_id: int | None,
    stack_limit: int = _DEFAULT_STACK_LIMIT,
) -> dict[str, Any]:
    """Capture code-location-only evidence for a current runtime stall."""
    stacks = _thread_stacks(
        event_loop_thread_id=event_loop_thread_id,
        stack_limit=max(2, stack_limit),
    )
    event_loop_stack = next(
        (item for item in stacks if item.get("is_event_loop_thread")),
        None,
    )
    suspected = [
        {
            "thread_name": item["thread_name"],
            "thread_id": item["thread_id"],
            "category": item["category"],
            "top_frame": item["top_frame"],
        }
        for item in stacks
        if item["category"]
        not in {
            "asyncio_idle_or_io",
            "diagnostics_logging",
            "tts_first_frame_wait",
        }
        and not str(item["thread_name"]).startswith("omnix-event-loop-watchdog")
    ]
    return {
        "active_tts_streams": active_streams_snapshot(),
        "event_loop_thread": event_loop_stack,
        "suspected_blockers": suspected[:12],
        "thread_stacks": stacks[:24],
        "runtime_parallelism": _runtime_parallelism(),
    }


class GatewayEventLoopLagMonitor:
    """Track loop heartbeat progress from an independent watchdog thread."""

    def __init__(
        self,
        *,
        interval_seconds: float | None = None,
        threshold_seconds: float | None = None,
        refresh_seconds: float | None = None,
        stack_limit: int | None = None,
        log: Callable[..., None] = stream_log,
    ) -> None:
        self.interval_seconds = interval_seconds or _env_float(
            "OMNIX_EVENT_LOOP_LAG_INTERVAL_SECONDS",
            _DEFAULT_INTERVAL_SECONDS,
            minimum=0.005,
        )
        self.threshold_seconds = threshold_seconds or _env_float(
            "OMNIX_EVENT_LOOP_LAG_THRESHOLD_SECONDS",
            _DEFAULT_THRESHOLD_SECONDS,
            minimum=0.005,
        )
        self.refresh_seconds = refresh_seconds or _env_float(
            "OMNIX_EVENT_LOOP_LAG_REFRESH_SECONDS",
            _DEFAULT_REFRESH_SECONDS,
            minimum=0.050,
        )
        self.stack_limit = stack_limit or _env_int(
            "OMNIX_EVENT_LOOP_LAG_STACK_LIMIT",
            _DEFAULT_STACK_LIMIT,
            minimum=2,
            maximum=32,
        )
        self._log = log
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._last_heartbeat_at = time.perf_counter()
        self._event_loop_thread_id: int | None = None
        self._stall_started_at: float | None = None
        self._last_snapshot_at: float | None = None
        self._stall_sequence = 0

    def start(self) -> None:
        if self._heartbeat_task is not None:
            return
        self._stop_event.clear()
        self._event_loop_thread_id = threading.get_ident()
        with self._lock:
            self._last_heartbeat_at = time.perf_counter()
            self._stall_started_at = None
            self._last_snapshot_at = None
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="omnix-event-loop-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        self._log(
            "gateway-event-loop",
            "runtime",
            "event_loop_lag_monitor_started",
            interval_ms=round(self.interval_seconds * 1000, 3),
            threshold_ms=round(self.threshold_seconds * 1000, 3),
            refresh_ms=round(self.refresh_seconds * 1000, 3),
            event_loop_thread_id=self._event_loop_thread_id,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        thread = self._watchdog_thread
        self._watchdog_thread = None
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 0.25)
        self._log("gateway-event-loop", "runtime", "event_loop_lag_monitor_stopped")

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.interval_seconds)
            recovered: tuple[int, float] | None = None
            now = time.perf_counter()
            with self._lock:
                if self._stall_started_at is not None:
                    recovered = (
                        self._stall_sequence,
                        max(0.0, now - self._stall_started_at),
                    )
                    self._stall_started_at = None
                    self._last_snapshot_at = None
                self._last_heartbeat_at = now
            if recovered is not None:
                sequence, duration = recovered
                self._log(
                    "gateway-event-loop",
                    "runtime",
                    "event_loop_lag_recovered",
                    stall_sequence=sequence,
                    stall_duration_ms=round(duration * 1000, 3),
                    active_tts_streams=active_streams_snapshot(),
                )

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            now = time.perf_counter()
            event_name: str | None = None
            sequence = 0
            lag_seconds = 0.0
            with self._lock:
                lag_seconds = max(
                    0.0,
                    now - self._last_heartbeat_at - self.interval_seconds,
                )
                if lag_seconds < self.threshold_seconds:
                    continue
                if self._stall_started_at is None:
                    self._stall_sequence += 1
                    sequence = self._stall_sequence
                    self._stall_started_at = self._last_heartbeat_at + self.interval_seconds
                    self._last_snapshot_at = now
                    event_name = "event_loop_lag_detected"
                elif (
                    self._last_snapshot_at is None
                    or now - self._last_snapshot_at >= self.refresh_seconds
                ):
                    sequence = self._stall_sequence
                    self._last_snapshot_at = now
                    event_name = "event_loop_lag_still_blocked"

            if event_name is None:
                continue
            snapshot = capture_runtime_contention_snapshot(
                event_loop_thread_id=self._event_loop_thread_id,
                stack_limit=self.stack_limit,
            )
            self._log(
                "gateway-event-loop",
                "runtime",
                event_name,
                stall_sequence=sequence,
                observed_lag_ms=round(lag_seconds * 1000, 3),
                expected_interval_ms=round(self.interval_seconds * 1000, 3),
                **snapshot,
            )


def register_event_loop_lag_monitor(gateway: FastAPI) -> None:
    if getattr(gateway.state, _ROUTE_SENTINEL, False):
        return
    setattr(gateway.state, _ROUTE_SENTINEL, True)
    monitor = GatewayEventLoopLagMonitor()
    setattr(gateway.state, _MONITOR_STATE_KEY, monitor)

    async def startup() -> None:
        if _env_flag("OMNIX_EVENT_LOOP_LAG_MONITOR", "1"):
            monitor.start()

    async def shutdown() -> None:
        await monitor.stop()

    from .feature_registry import FeatureLifecycle, register_feature_lifecycle
    register_feature_lifecycle(gateway, FeatureLifecycle(__name__, (startup,), (shutdown,)))
