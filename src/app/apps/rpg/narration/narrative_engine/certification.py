"""Deterministic save/load, replay, and delivery certification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .authority import DeliveryMode
from .contracts import CanonicalNarrativeResponse, stable_hash
from .delivery import NarrativeDeliveryCoordinator
from .projections import replay_projection
from .serialization import canonical_response_from_dict


@dataclass(frozen=True)
class NarrativeCertificationReport:
    passed: bool
    response_id: str
    content_hash: str
    checks: Mapping[str, bool]
    diagnostics: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "response_id": self.response_id,
            "content_hash": self.content_hash,
            "checks": dict(self.checks),
            "diagnostics": dict(self.diagnostics),
        }


def certify_narrative_roundtrip(
    response: CanonicalNarrativeResponse,
) -> NarrativeCertificationReport:
    frozen = response.with_content_hash()
    payload = frozen.as_dict()
    replay_payload = replay_projection(frozen)
    restored = canonical_response_from_dict(payload)
    replayed = canonical_response_from_dict(replay_payload)
    expected_order = tuple(block.block_id for block in frozen.blocks)
    restored_hash = stable_hash(restored.content_payload())
    replayed_hash = stable_hash(replayed.content_payload())
    checks = {
        "response_id_preserved": restored.response_id == frozen.response_id == replayed.response_id,
        "content_hash_preserved": restored.content_hash == frozen.content_hash == replayed.content_hash,
        "content_hash_recomputed": restored_hash == frozen.content_hash == replayed_hash,
        "turn_id_preserved": restored.turn_id == frozen.turn_id == replayed.turn_id,
        "campaign_id_preserved": restored.campaign_id == frozen.campaign_id == replayed.campaign_id,
        "block_order_preserved": (
            tuple(block.block_id for block in restored.blocks) == expected_order
            and tuple(block.block_id for block in replayed.blocks) == expected_order
        ),
        "block_text_preserved": (
            tuple(block.text for block in restored.blocks) == tuple(block.text for block in frozen.blocks)
            and tuple(block.text for block in replayed.blocks) == tuple(block.text for block in frozen.blocks)
        ),
    }
    return NarrativeCertificationReport(
        passed=all(checks.values()),
        response_id=frozen.response_id,
        content_hash=frozen.content_hash,
        checks=checks,
        diagnostics={
            "block_ids": list(expected_order),
            "serialized_schema_version": payload.get("schema_version"),
            "replay_schema_version": replay_payload.get("schema_version"),
            "replay_projection_version": replay_payload.get("projection_version"),
            "restored_hash": restored_hash,
            "replayed_hash": replayed_hash,
        },
    )


def certify_delivery_equivalence(
    response: CanonicalNarrativeResponse,
) -> NarrativeCertificationReport:
    coordinator = NarrativeDeliveryCoordinator()
    blocking = coordinator.prepare(response, DeliveryMode.BLOCKING)
    deferred_pending = coordinator.prepare(response, DeliveryMode.DEFERRED)
    deferred_complete = coordinator.complete_deferred(deferred_pending)
    expected_blocks = tuple(block.block_id for block in response.blocks)
    checks = {
        "blocking_hash_matches": blocking.content_hash == response.with_content_hash().content_hash,
        "deferred_pending_hash_matches": deferred_pending.content_hash == blocking.content_hash,
        "deferred_complete_hash_matches": deferred_complete.content_hash == blocking.content_hash,
        "block_order_matches": (
            tuple(block.block_id for block in blocking.blocks) == expected_blocks
            and tuple(block.block_id for block in deferred_complete.blocks) == expected_blocks
        ),
        "blocking_complete": blocking.delivery.status == "complete",
        "deferred_pending": deferred_pending.delivery.status == "pending",
        "deferred_complete": deferred_complete.delivery.status == "complete",
        "deferred_delivers_same_blocks": deferred_complete.delivery.delivered_block_ids == expected_blocks,
    }
    return NarrativeCertificationReport(
        passed=all(checks.values()),
        response_id=blocking.response_id,
        content_hash=blocking.content_hash,
        checks=checks,
        diagnostics={
            "blocking_mode": blocking.delivery.mode.value,
            "deferred_mode": deferred_complete.delivery.mode.value,
            "delivered_block_ids": list(deferred_complete.delivery.delivered_block_ids),
        },
    )


def certify_narrative_persistence_and_delivery(
    response: CanonicalNarrativeResponse,
) -> dict[str, Any]:
    roundtrip = certify_narrative_roundtrip(response)
    delivery = certify_delivery_equivalence(response)
    return {
        "passed": roundtrip.passed and delivery.passed,
        "response_id": response.response_id,
        "content_hash": response.with_content_hash().content_hash,
        "roundtrip": roundtrip.as_dict(),
        "delivery": delivery.as_dict(),
    }
