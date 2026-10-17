"""Generation-aware cancellation for realtime live speech."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CancelScope:
    """Tracks the active realtime generation.

    A response producer captures ``generation`` when it starts. If another
    response is cancelled or superseded, the generation increments and older
    producer output becomes stale.
    """

    generation: int = 0
    discarding: bool = False
    reason: str | None = None

    def cancel(self, reason: str = "client_cancelled") -> int:
        self.generation += 1
        self.discarding = True
        self.reason = reason
        return self.generation

    def is_stale(self, generation: int | None) -> bool:
        return generation is not None and generation != self.generation

    def should_drop(self, generation: int | None) -> bool:
        return self.discarding and self.is_stale(generation)

    def response_done(self, generation: int | None = None) -> None:
        if generation is None or generation == self.generation:
            self.discarding = False
            self.reason = None
