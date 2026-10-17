"""Durable delivery of one already-approved canonical response.

Delivery state is intentionally separate from semantic response content. Blocking,
deferred, reconnect, and cancellation paths all reuse the same response ID and
semantic hash; none of them may call a narrative writer or regenerate prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any, Mapping, Protocol

from .authority import DeliveryMode
from .contracts import (
    CanonicalNarrativeResponse,
    DeliveryMetadata,
    NarrativeBlock,
    ordered_blocks,
)


class NarrativeDeliveryConflict(RuntimeError):
    """Raised when a cursor does not belong to the immutable canonical response."""


@dataclass(frozen=True)
class NarrativeDeliveryRecord:
    response_id: str
    semantic_hash: str
    mode: DeliveryMode
    status: str
    block_ids: tuple[str, ...]
    delivered_block_ids: tuple[str, ...] = ()
    next_index: int = 0
    revision: int = 1
    cancel_reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "semantic_hash": self.semantic_hash,
            "mode": self.mode.value,
            "status": self.status,
            "block_ids": list(self.block_ids),
            "delivered_block_ids": list(self.delivered_block_ids),
            "next_index": self.next_index,
            "revision": self.revision,
            "cancel_reason": self.cancel_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class NarrativeDeliveryAdvance:
    record: NarrativeDeliveryRecord
    published_block_id: str | None = None


@dataclass(frozen=True)
class NarrativeDeliveryEvent:
    response_id: str
    semantic_hash: str
    index: int
    block: NarrativeBlock
    status: str

    @property
    def event_id(self) -> str:
        return str(self.index)

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "semantic_hash": self.semantic_hash,
            "index": self.index,
            "event_id": self.event_id,
            "status": self.status,
            "block": self.block.as_dict(),
        }


class NarrativeDeliveryRepository(Protocol):
    def open(
        self,
        *,
        response_id: str,
        semantic_hash: str,
        mode: DeliveryMode,
        block_ids: tuple[str, ...],
        metadata: Mapping[str, Any] | None = None,
    ) -> NarrativeDeliveryRecord: ...

    def get(self, response_id: str) -> NarrativeDeliveryRecord | None: ...

    def advance(
        self,
        response_id: str,
        *,
        expected_semantic_hash: str,
    ) -> NarrativeDeliveryAdvance: ...

    def cancel(
        self,
        response_id: str,
        *,
        expected_semantic_hash: str,
        reason: str,
    ) -> NarrativeDeliveryRecord: ...


class InMemoryNarrativeDeliveryRepository:
    """Thread-safe delivery cursor used by tests and portable development."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, NarrativeDeliveryRecord] = {}

    def open(
        self,
        *,
        response_id: str,
        semantic_hash: str,
        mode: DeliveryMode,
        block_ids: tuple[str, ...],
        metadata: Mapping[str, Any] | None = None,
    ) -> NarrativeDeliveryRecord:
        with self._lock:
            existing = self._records.get(response_id)
            if existing is not None:
                self._validate(existing, semantic_hash, block_ids)
                if (
                    mode is DeliveryMode.BLOCKING
                    and not existing.complete
                    and not existing.cancelled
                ):
                    existing = replace(
                        existing,
                        mode=DeliveryMode.BLOCKING,
                        status="complete",
                        delivered_block_ids=block_ids,
                        next_index=len(block_ids),
                        revision=existing.revision + 1,
                    )
                    self._records[response_id] = existing
                return existing
            complete = mode is DeliveryMode.BLOCKING
            record = NarrativeDeliveryRecord(
                response_id=response_id,
                semantic_hash=semantic_hash,
                mode=mode,
                status="complete" if complete else "pending",
                block_ids=block_ids,
                delivered_block_ids=block_ids if complete else (),
                next_index=len(block_ids) if complete else 0,
                metadata=dict(metadata or {}),
            )
            self._records[response_id] = record
            return record

    def get(self, response_id: str) -> NarrativeDeliveryRecord | None:
        with self._lock:
            return self._records.get(response_id)

    def advance(
        self,
        response_id: str,
        *,
        expected_semantic_hash: str,
    ) -> NarrativeDeliveryAdvance:
        with self._lock:
            record = self._require(response_id)
            self._validate(record, expected_semantic_hash, record.block_ids)
            if record.complete or record.cancelled:
                return NarrativeDeliveryAdvance(record)
            if record.next_index >= len(record.block_ids):
                completed = replace(record, status="complete")
                self._records[response_id] = completed
                return NarrativeDeliveryAdvance(completed)
            block_id = record.block_ids[record.next_index]
            delivered = (*record.delivered_block_ids, block_id)
            next_index = record.next_index + 1
            updated = replace(
                record,
                status="complete" if next_index == len(record.block_ids) else "streaming",
                delivered_block_ids=delivered,
                next_index=next_index,
                revision=record.revision + 1,
            )
            self._records[response_id] = updated
            return NarrativeDeliveryAdvance(updated, block_id)

    def cancel(
        self,
        response_id: str,
        *,
        expected_semantic_hash: str,
        reason: str,
    ) -> NarrativeDeliveryRecord:
        with self._lock:
            record = self._require(response_id)
            self._validate(record, expected_semantic_hash, record.block_ids)
            if record.cancelled:
                return record
            if record.complete or record.next_index > 0:
                raise NarrativeDeliveryConflict(
                    "canonical delivery can only be cancelled before first publication"
                )
            cancelled = replace(
                record,
                status="cancelled",
                cancel_reason=reason or "cancelled_before_publication",
                revision=record.revision + 1,
            )
            self._records[response_id] = cancelled
            return cancelled

    def _require(self, response_id: str) -> NarrativeDeliveryRecord:
        record = self._records.get(response_id)
        if record is None:
            raise NarrativeDeliveryConflict(f"unknown narrative delivery: {response_id}")
        return record

    @staticmethod
    def _validate(
        record: NarrativeDeliveryRecord,
        semantic_hash: str,
        block_ids: tuple[str, ...],
    ) -> None:
        if record.semantic_hash != semantic_hash:
            raise NarrativeDeliveryConflict(
                f"semantic hash mismatch for narrative delivery {record.response_id}"
            )
        if record.block_ids != block_ids:
            raise NarrativeDeliveryConflict(
                f"canonical block order changed for narrative delivery {record.response_id}"
            )


class NarrativeDeliveryCoordinator:
    """Project and stream immutable canonical blocks through a durable cursor."""

    def prepare(
        self,
        response: CanonicalNarrativeResponse,
        mode: DeliveryMode,
    ) -> CanonicalNarrativeResponse:
        response = response.with_content_hash()
        status = "complete" if mode is DeliveryMode.BLOCKING else "pending"
        delivered = (
            tuple(block.block_id for block in ordered_blocks(response.blocks))
            if status == "complete"
            else ()
        )
        return replace(
            response,
            delivery=DeliveryMetadata(
                mode=mode,
                status=status,
                delivered_block_ids=delivered,
                metadata={
                    "semantic_hash": response.semantic_hash,
                    "next_index": len(delivered),
                },
            ),
        )

    def complete_deferred(
        self,
        response: CanonicalNarrativeResponse,
    ) -> CanonicalNarrativeResponse:
        response = response.with_content_hash()
        return replace(
            response,
            delivery=DeliveryMetadata(
                mode=DeliveryMode.DEFERRED,
                status="complete",
                delivered_block_ids=tuple(
                    block.block_id for block in ordered_blocks(response.blocks)
                ),
                metadata={
                    "semantic_hash": response.semantic_hash,
                    "next_index": len(response.blocks),
                },
            ),
        )

    def open(
        self,
        response: CanonicalNarrativeResponse,
        mode: DeliveryMode,
        repository: NarrativeDeliveryRepository,
    ) -> CanonicalNarrativeResponse:
        frozen = response.with_content_hash()
        block_ids = tuple(block.block_id for block in ordered_blocks(frozen.blocks))
        record = repository.open(
            response_id=frozen.response_id,
            semantic_hash=frozen.semantic_hash,
            mode=mode,
            block_ids=block_ids,
            metadata={"campaign_id": frozen.campaign_id, "turn_id": frozen.turn_id},
        )
        return self._project(frozen, record)

    def publish_next(
        self,
        response: CanonicalNarrativeResponse,
        repository: NarrativeDeliveryRepository,
        *,
        expected_semantic_hash: str,
    ) -> tuple[CanonicalNarrativeResponse, NarrativeDeliveryEvent | None]:
        frozen = self._verified(response, expected_semantic_hash)
        advance = repository.advance(
            frozen.response_id,
            expected_semantic_hash=frozen.semantic_hash,
        )
        projected = self._project(frozen, advance.record)
        if advance.published_block_id is None:
            return projected, None
        blocks = ordered_blocks(frozen.blocks)
        index = advance.record.next_index - 1
        block = blocks[index]
        if block.block_id != advance.published_block_id:
            raise NarrativeDeliveryConflict("delivery cursor no longer matches block order")
        return projected, NarrativeDeliveryEvent(
            response_id=frozen.response_id,
            semantic_hash=frozen.semantic_hash,
            index=index,
            block=block,
            status=advance.record.status,
        )

    def resume(
        self,
        response: CanonicalNarrativeResponse,
        repository: NarrativeDeliveryRepository,
        *,
        expected_semantic_hash: str,
        after_index: int = -1,
    ) -> tuple[NarrativeDeliveryEvent, ...]:
        frozen = self._verified(response, expected_semantic_hash)
        record = repository.get(frozen.response_id)
        if record is None:
            raise NarrativeDeliveryConflict(
                f"unknown narrative delivery: {frozen.response_id}"
            )
        self._validate_record(frozen, record)
        blocks = ordered_blocks(frozen.blocks)
        upper = min(record.next_index, len(blocks))
        return tuple(
            NarrativeDeliveryEvent(
                response_id=frozen.response_id,
                semantic_hash=frozen.semantic_hash,
                index=index,
                block=blocks[index],
                status=record.status,
            )
            for index in range(max(-1, int(after_index)) + 1, upper)
        )

    def cancel_before_publication(
        self,
        response: CanonicalNarrativeResponse,
        repository: NarrativeDeliveryRepository,
        *,
        expected_semantic_hash: str,
        reason: str = "cancelled_before_publication",
    ) -> CanonicalNarrativeResponse:
        frozen = self._verified(response, expected_semantic_hash)
        record = repository.cancel(
            frozen.response_id,
            expected_semantic_hash=frozen.semantic_hash,
            reason=reason,
        )
        return self._project(frozen, record)

    @staticmethod
    def _verified(
        response: CanonicalNarrativeResponse,
        expected_semantic_hash: str,
    ) -> CanonicalNarrativeResponse:
        frozen = response.with_content_hash()
        if not expected_semantic_hash or frozen.semantic_hash != expected_semantic_hash:
            raise NarrativeDeliveryConflict(
                f"semantic hash mismatch for narrative response {frozen.response_id}"
            )
        return frozen

    @staticmethod
    def _validate_record(
        response: CanonicalNarrativeResponse,
        record: NarrativeDeliveryRecord,
    ) -> None:
        block_ids = tuple(block.block_id for block in ordered_blocks(response.blocks))
        if record.semantic_hash != response.semantic_hash or record.block_ids != block_ids:
            raise NarrativeDeliveryConflict(
                f"delivery record differs from canonical response {response.response_id}"
            )

    def _project(
        self,
        response: CanonicalNarrativeResponse,
        record: NarrativeDeliveryRecord,
    ) -> CanonicalNarrativeResponse:
        self._validate_record(response, record)
        return replace(
            response,
            delivery=DeliveryMetadata(
                mode=record.mode,
                status=record.status,
                delivered_block_ids=record.delivered_block_ids,
                interruption_reason=record.cancel_reason or None,
                metadata={
                    **dict(record.metadata),
                    "semantic_hash": record.semantic_hash,
                    "next_index": record.next_index,
                    "revision": record.revision,
                    "block_count": len(record.block_ids),
                },
            ),
        )
