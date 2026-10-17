"""Dedicated low-latency model and accepted-first TTS execution lane."""
from __future__ import annotations

from app.config.env import environment
from app.runtime.live_voice_config import (
    live_voice_execution_lane_config as _live_voice_execution_lane_config,
)

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from app.settings.access import load_settings
from app.providers.audio_registry import get_audio_registry

from app.observability.tts_stream_diagnostics import stream_log
from app.platform.voice.contracts import TTSProvider


class TtsLanePriority(IntEnum):
    ACCEPTED = 0
    CONTINUATION = 1
    SPECULATIVE = 2


@dataclass
class _TtsTicket:
    priority: TtsLanePriority
    sequence: int
    promotion_event: threading.Event | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    enqueued_at: float = field(default_factory=time.perf_counter)
    acquired_at: float | None = None


class PriorityTtsScheduler:
    """Serialize non-concurrent TTS providers with accepted turns first.

    A newly accepted request cancels an active unaccepted speculative stream.
    A speculative first clause that becomes authoritative can be promoted in
    place and is then protected from later accepted-turn preemption.

    Speculative provider work uses a smaller codec chunk than audible accepted
    work. This does not change the authoritative request/cache contract; it only
    gives the scheduler more frequent safe cancellation boundaries while hidden
    speculative CUDA generation is running.
    """

    def __init__(self, *, log: Callable[..., None] = stream_log) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._waiting: list[_TtsTicket] = []
        self._active: _TtsTicket | None = None
        self._sequence = 0
        self._log = log

    def stream(
        self,
        provider: TTSProvider,
        *,
        text: str,
        speaker: str | None,
        language: str,
        kwargs: dict[str, Any],
        priority: TtsLanePriority,
        should_stop: Callable[[], bool] | None = None,
        promotion_event: threading.Event | None = None,
    ) -> Iterator[tuple[Any, int, Any]]:
        ticket = self._acquire(priority, promotion_event)
        stream: Any = None
        effective_kwargs = dict(kwargs)
        requested_chunk_size = effective_kwargs.get("chunk_size")
        effective_priority = self._effective_priority(ticket)
        if effective_priority == TtsLanePriority.SPECULATIVE:
            speculative_chunk_steps = _env_int(
                "OMNIX_LIVE_TTS_SPECULATIVE_CHUNK_STEPS",
                1,
                minimum=1,
                maximum=4,
            )
            if isinstance(requested_chunk_size, int) and requested_chunk_size > 0:
                effective_kwargs["chunk_size"] = min(
                    requested_chunk_size,
                    speculative_chunk_steps,
                )
        self._log(
            "gateway-live-voice-lane",
            "scheduler",
            "tts_lane_stream_started",
            sequence=ticket.sequence,
            priority=int(priority),
            effective_priority=int(effective_priority),
            wait_ms=round(
                ((ticket.acquired_at or time.perf_counter()) - ticket.enqueued_at)
                * 1000.0,
                3,
            ),
            requested_chunk_size=requested_chunk_size,
            effective_chunk_size=effective_kwargs.get("chunk_size"),
            provider_name=getattr(provider, "provider_name", None),
        )
        try:
            if self._stopped(ticket, should_stop):
                return
            stream = provider.generate_audio_stream(
                text=text,
                speaker=speaker,
                language=language,
                **effective_kwargs,
            )
            for chunk in stream:
                if self._stopped(ticket, should_stop):
                    return
                yield chunk
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            self._release(ticket)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "active_priority": (
                    int(self._effective_priority(self._active))
                    if self._active is not None
                    else None
                ),
                "active_cancelled": bool(
                    self._active and self._active.cancel_event.is_set()
                ),
                "waiting": [
                    int(self._effective_priority(ticket))
                    for ticket in self._waiting
                ],
            }

    def clear(self) -> None:
        with self._condition:
            if self._active is not None:
                self._active.cancel_event.set()
            for ticket in self._waiting:
                ticket.cancel_event.set()
            self._waiting.clear()
            self._condition.notify_all()

    def notify_priority_change(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _acquire(
        self,
        priority: TtsLanePriority,
        promotion_event: threading.Event | None,
    ) -> _TtsTicket:
        with self._condition:
            self._sequence += 1
            ticket = _TtsTicket(
                priority=priority,
                sequence=self._sequence,
                promotion_event=promotion_event,
            )
            active_priority = (
                int(self._effective_priority(self._active))
                if self._active is not None
                else None
            )
            self._waiting.append(ticket)
            self._log(
                "gateway-live-voice-lane",
                "scheduler",
                "tts_lane_ticket_enqueued",
                sequence=ticket.sequence,
                priority=int(priority),
                effective_priority=int(self._effective_priority(ticket)),
                active_priority=active_priority,
                waiting_count=len(self._waiting),
            )
            if (
                priority == TtsLanePriority.ACCEPTED
                and self._active is not None
                and self._effective_priority(self._active)
                == TtsLanePriority.SPECULATIVE
            ):
                self._active.cancel_event.set()
                self._log(
                    "gateway-live-voice-lane",
                    "scheduler",
                    "speculative_tts_preempt_requested",
                    active_sequence=self._active.sequence,
                    accepted_sequence=ticket.sequence,
                    active_ms=round(
                        (
                            time.perf_counter()
                            - (self._active.acquired_at or self._active.enqueued_at)
                        )
                        * 1000.0,
                        3,
                    ),
                )
            while True:
                if ticket.cancel_event.is_set():
                    self._waiting = [item for item in self._waiting if item is not ticket]
                    self._log(
                        "gateway-live-voice-lane",
                        "scheduler",
                        "tts_lane_ticket_cancelled_before_start",
                        sequence=ticket.sequence,
                        priority=int(priority),
                        wait_ms=round(
                            (time.perf_counter() - ticket.enqueued_at) * 1000.0,
                            3,
                        ),
                    )
                    raise RuntimeError("tts_lane_ticket_cancelled")
                next_ticket = min(
                    self._waiting,
                    key=lambda item: (
                        int(self._effective_priority(item)),
                        item.sequence,
                    ),
                )
                if self._active is None and next_ticket is ticket:
                    self._waiting.remove(ticket)
                    self._active = ticket
                    ticket.started = True
                    ticket.acquired_at = time.perf_counter()
                    self._log(
                        "gateway-live-voice-lane",
                        "scheduler",
                        "tts_lane_ticket_acquired",
                        sequence=ticket.sequence,
                        priority=int(priority),
                        effective_priority=int(self._effective_priority(ticket)),
                        wait_ms=round(
                            (ticket.acquired_at - ticket.enqueued_at) * 1000.0,
                            3,
                        ),
                        waiting_count=len(self._waiting),
                    )
                    return ticket
                self._condition.wait(timeout=0.025)

    def _release(self, ticket: _TtsTicket) -> None:
        with self._condition:
            if self._active is ticket:
                self._active = None
            self._waiting = [item for item in self._waiting if item is not ticket]
            now = time.perf_counter()
            self._log(
                "gateway-live-voice-lane",
                "scheduler",
                "tts_lane_ticket_released",
                sequence=ticket.sequence,
                priority=int(ticket.priority),
                effective_priority=int(self._effective_priority(ticket)),
                active_ms=round(
                    (now - (ticket.acquired_at or ticket.enqueued_at)) * 1000.0,
                    3,
                ),
                total_ms=round((now - ticket.enqueued_at) * 1000.0, 3),
                cancelled=ticket.cancel_event.is_set(),
                waiting_count=len(self._waiting),
            )
            self._condition.notify_all()

    @staticmethod
    def _effective_priority(ticket: _TtsTicket) -> TtsLanePriority:
        if ticket.promotion_event is not None and ticket.promotion_event.is_set():
            return TtsLanePriority.ACCEPTED
        return ticket.priority

    @staticmethod
    def _stopped(
        ticket: _TtsTicket,
        should_stop: Callable[[], bool] | None,
    ) -> bool:
        return ticket.cancel_event.is_set() or bool(should_stop and should_stop())


_TTS_SCHEDULER = PriorityTtsScheduler()
_DEDICATED_TTS_LOCK = threading.RLock()
_DEDICATED_TTS_PROVIDER: Any = None
_DEDICATED_TTS_KEY: str | None = None
_DEDICATED_TTS_PROVIDER_NAME: str | None = None


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = environment().get(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def live_voice_tts_scheduler() -> PriorityTtsScheduler:
    return _TTS_SCHEDULER


def live_voice_tts_lane_is_warm() -> bool:
    """True when resolving the live lane needs no settings load or provider start."""
    config = _live_voice_execution_lane_config()
    if not config.dedicated_tts or not config.tts_provider_name:
        return True
    with _DEDICATED_TTS_LOCK:
        return (
            _DEDICATED_TTS_PROVIDER is not None
            and _DEDICATED_TTS_PROVIDER_NAME == config.tts_provider_name
        )


def resolve_live_voice_tts_provider(default_provider: TTSProvider) -> tuple[TTSProvider, str]:
    """Return an optional separately instantiated provider for the live lane."""
    global _DEDICATED_TTS_KEY, _DEDICATED_TTS_PROVIDER, _DEDICATED_TTS_PROVIDER_NAME

    config = _live_voice_execution_lane_config()
    provider_name = config.tts_provider_name
    if not config.dedicated_tts or not provider_name:
        return default_provider, "shared"

    # Dedicated live TTS is an explicit process-level configuration. Once that
    # provider is started, return it directly on the phrase hot path instead of
    # reloading application settings for every clause. A process restart/reset
    # remains the boundary for changing the dedicated provider configuration.
    with _DEDICATED_TTS_LOCK:
        if (
            _DEDICATED_TTS_PROVIDER is not None
            and _DEDICATED_TTS_PROVIDER_NAME == provider_name
        ):
            return _DEDICATED_TTS_PROVIDER, "dedicated"

    settings = load_settings()
    provider_settings = dict(settings.get(provider_name, {}) or {})
    from app.runtime.capabilities import RuntimeCapabilities, RuntimeCapability
    from app.runtime.config import get_runtime_config

    runtime_config = get_runtime_config()
    capabilities = RuntimeCapabilities.from_config(runtime_config)
    from app.providers.catalog import LOCAL_DEVICE, provider_supports

    local_gpu = provider_supports(provider_name, LOCAL_DEVICE, "tts")
    remote_qwen = (
        local_gpu
        and runtime_config.use_remote_tts
        and runtime_config.tts is not None
    )
    cache_key = json.dumps(
        {
            "provider": provider_name,
            "settings": provider_settings,
            "transport": "http" if remote_qwen else "local",
            "endpoint": runtime_config.tts.url if remote_qwen else None,
        },
        sort_keys=True,
        default=str,
    )
    with _DEDICATED_TTS_LOCK:
        if (
            _DEDICATED_TTS_PROVIDER is not None
            and _DEDICATED_TTS_KEY == cache_key
        ):
            _DEDICATED_TTS_PROVIDER_NAME = provider_name
            return _DEDICATED_TTS_PROVIDER, "dedicated"

        previous = _DEDICATED_TTS_PROVIDER
        if previous is not None:
            stop = getattr(previous, "stop", None)
            if callable(stop):
                stop()

        if remote_qwen:
            from app.providers.qwen_http_gateway import QwenHttpGatewayProvider

            provider = QwenHttpGatewayProvider(runtime_config.tts.url, provider_name=provider_name)
        else:
            capabilities.require(RuntimeCapability.RUN_LOCAL_TTS)
            registry = get_audio_registry()
            if local_gpu:
                provider_config = provider_settings
            else:
                provider_config = {
                    "base_url": provider_settings.get("base_url"),
                    "timeout": provider_settings.get("timeout", 300),
                    "max_retries": provider_settings.get("max_retries", 3),
                    "extra_params": provider_settings.get("extra_params", {}),
                }
            provider = registry.create_tts_provider(
                provider_name,
                config=provider_config,
            )
        if provider is None:
            raise RuntimeError(f"live_tts_provider_unavailable:{provider_name}")
        start = getattr(provider, "start", None)
        if callable(start):
            result = start()
            if isinstance(result, dict) and result.get("running") is False:
                raise RuntimeError(
                    f"live_tts_provider_start_failed:{provider_name}"
                )
        _DEDICATED_TTS_PROVIDER = provider
        _DEDICATED_TTS_KEY = cache_key
        _DEDICATED_TTS_PROVIDER_NAME = provider_name
        return provider, "dedicated"


def reset_live_voice_execution_lane_for_tests() -> None:
    global _DEDICATED_TTS_KEY, _DEDICATED_TTS_PROVIDER, _DEDICATED_TTS_PROVIDER_NAME
    _TTS_SCHEDULER.clear()
    with _DEDICATED_TTS_LOCK:
        provider = _DEDICATED_TTS_PROVIDER
        _DEDICATED_TTS_PROVIDER = None
        _DEDICATED_TTS_KEY = None
        _DEDICATED_TTS_PROVIDER_NAME = None
    stop = getattr(provider, "stop", None)
    if callable(stop):
        stop()


__all__ = [
    "PriorityTtsScheduler",
    "TtsLanePriority",
    "live_voice_tts_lane_is_warm",
    "live_voice_tts_scheduler",
    "reset_live_voice_execution_lane_for_tests",
    "resolve_live_voice_tts_provider",
]
