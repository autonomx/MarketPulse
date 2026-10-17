"""Queued live-speech session helper."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .events import LiveSpeechEvent
from .protocol import dispatch_client_event
from .realtime import LiveSpeechRealtimeService


@dataclass
class QueuedLiveSpeechSession:
    service: LiveSpeechRealtimeService = field(default_factory=LiveSpeechRealtimeService)
    max_queue_size: int = 256
    closed: bool = False
    events_queue: asyncio.Queue[LiveSpeechEvent] = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.events_queue = asyncio.Queue(maxsize=self.max_queue_size)

    async def start(self) -> None:
        await self.put(self.service.session_created())

    async def close(self) -> None:
        self.closed = True

    async def handle(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.lock:
            for evt in dispatch_client_event(self.service, message):
                await self.put(evt)

    async def put(self, evt: LiveSpeechEvent) -> None:
        if not self.closed:
            await self.events_queue.put(evt)

    async def drain_available(self) -> list[LiveSpeechEvent]:
        drained: list[LiveSpeechEvent] = []
        while True:
            try:
                drained.append(self.events_queue.get_nowait())
            except asyncio.QueueEmpty:
                return drained
