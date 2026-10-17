"""Persistence interfaces for canonical narrative responses."""
from __future__ import annotations

from threading import RLock
from typing import Protocol

from .contracts import CanonicalNarrativeResponse


class NarrativeResponseConflict(RuntimeError):
    pass


class NarrativeResponseRepository(Protocol):
    def save(self, response: CanonicalNarrativeResponse) -> CanonicalNarrativeResponse: ...

    def get(self, response_id: str) -> CanonicalNarrativeResponse | None: ...

    def get_for_turn(self, campaign_id: str, turn_id: str) -> CanonicalNarrativeResponse | None: ...


class InMemoryNarrativeResponseRepository:
    """Thread-safe idempotent repository used before PostgreSQL materialization."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._responses: dict[str, CanonicalNarrativeResponse] = {}
        self._turn_index: dict[tuple[str, str], str] = {}

    def save(self, response: CanonicalNarrativeResponse) -> CanonicalNarrativeResponse:
        response = response.with_content_hash()
        key = (response.campaign_id, response.turn_id)
        with self._lock:
            existing_id = self._turn_index.get(key)
            if existing_id:
                existing = self._responses[existing_id]
                if existing.content_hash != response.content_hash:
                    raise NarrativeResponseConflict(
                        f"turn already has different canonical response: {response.campaign_id}/{response.turn_id}"
                    )
                return existing
            stored = self._responses.get(response.response_id)
            if stored:
                if stored.content_hash != response.content_hash:
                    raise NarrativeResponseConflict(f"response id reused with different content: {response.response_id}")
                return stored
            self._responses[response.response_id] = response
            self._turn_index[key] = response.response_id
            return response

    def get(self, response_id: str) -> CanonicalNarrativeResponse | None:
        with self._lock:
            return self._responses.get(response_id)

    def get_for_turn(self, campaign_id: str, turn_id: str) -> CanonicalNarrativeResponse | None:
        with self._lock:
            response_id = self._turn_index.get((campaign_id, turn_id))
            return self._responses.get(response_id) if response_id else None

    def list_campaign(self, campaign_id: str) -> tuple[CanonicalNarrativeResponse, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (response for response in self._responses.values() if response.campaign_id == campaign_id),
                    key=lambda response: (response.revision, response.turn_id, response.response_id),
                )
            )
