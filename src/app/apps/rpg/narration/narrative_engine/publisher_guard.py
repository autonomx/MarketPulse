"""Production publisher ownership guard for canonical RPG narrative responses."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping


CANONICAL_PUBLISHER = "unified_narrative_engine_v1"


class LegacyNarrativePublisherError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarrativePublisherTelemetry:
    canonical_publish_count: int
    alternate_publish_count: int
    rejected_alternate_count: int
    last_response_id: str
    last_content_hash: str
    last_publisher: str

    @property
    def zero_alternate_publishers(self) -> bool:
        return self.alternate_publish_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_publish_count": self.canonical_publish_count,
            "alternate_publish_count": self.alternate_publish_count,
            "rejected_alternate_count": self.rejected_alternate_count,
            "zero_alternate_publishers": self.zero_alternate_publishers,
            "last_response_id": self.last_response_id,
            "last_content_hash": self.last_content_hash,
            "last_publisher": self.last_publisher,
        }


class NarrativePublisherGuard:
    """Allow one visible publication owner and reject every alternate owner."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._canonical_publish_count = 0
        self._alternate_publish_count = 0
        self._rejected_alternate_count = 0
        self._last_response_id = ""
        self._last_content_hash = ""
        self._last_publisher = ""

    def publish(
        self,
        *,
        publisher: str,
        response_id: str,
        content_hash: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = str(publisher or "").strip()
        with self._lock:
            if normalized != CANONICAL_PUBLISHER:
                self._alternate_publish_count += 1
                self._rejected_alternate_count += 1
                self._last_publisher = normalized or "unknown"
                raise LegacyNarrativePublisherError(
                    f"alternate RPG narrative publisher rejected: {normalized or 'unknown'}"
                )
            if not response_id or not content_hash:
                raise ValueError("canonical publication requires response_id and content_hash")
            self._canonical_publish_count += 1
            self._last_response_id = response_id
            self._last_content_hash = content_hash
            self._last_publisher = normalized
            return dict(payload)

    def snapshot(self) -> NarrativePublisherTelemetry:
        with self._lock:
            return NarrativePublisherTelemetry(
                canonical_publish_count=self._canonical_publish_count,
                alternate_publish_count=self._alternate_publish_count,
                rejected_alternate_count=self._rejected_alternate_count,
                last_response_id=self._last_response_id,
                last_content_hash=self._last_content_hash,
                last_publisher=self._last_publisher,
            )

    def reset_for_tests(self) -> None:
        with self._lock:
            self._canonical_publish_count = 0
            self._alternate_publish_count = 0
            self._rejected_alternate_count = 0
            self._last_response_id = ""
            self._last_content_hash = ""
            self._last_publisher = ""


publisher_guard = NarrativePublisherGuard()


def publish_canonical_bundle(
    bundle: Mapping[str, Any],
    *,
    publisher: str = CANONICAL_PUBLISHER,
) -> tuple[dict[str, Any], NarrativePublisherTelemetry]:
    response_id = str(bundle.get("response_id") or "")
    content_hash = str(bundle.get("content_hash") or "")
    published = publisher_guard.publish(
        publisher=publisher,
        response_id=response_id,
        content_hash=content_hash,
        payload=bundle,
    )
    return published, publisher_guard.snapshot()
