"""Canonical evidence and operating policy for the interactive RPG response release."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INTERACTIVE_RELEASE_VERSION = "rpg_interactive_response_release_v1"
LOCAL_LIVE_SMOKE_ENV = "OMNIX_RPG_LIVE_SMOKE"
TARGET_DIALOGUE_MEDIAN_SECONDS = 1.5
TARGET_DIALOGUE_P95_SECONDS = 2.5
MAX_BROWSER_COMMIT_VISIBLE_MS = 50.0
REQUIRED_PROVIDER_FREE_CHECKS = (
    "RPG Phase 0 architecture compliance",
    "RPG deterministic PR gates",
    "Live Chat hardening gates",
)


@dataclass(frozen=True)
class ReleasePhaseEvidence:
    phase: int
    title: str
    pull_request: int
    exact_head_sha: str
    merge_sha: str
    provider_free_ci: bool = True


PHASE_EVIDENCE: tuple[ReleasePhaseEvidence, ...] = (
    ReleasePhaseEvidence(
        1,
        "Guarantee exactly-once foreground RPG turns",
        1336,
        "1de86df0d87b2177bebae2642892540e277535e5",
        "014b0caf3ac7760d816809af9b857d036eff555f",
    ),
    ReleasePhaseEvidence(
        2,
        "Canonicalize RPG visible responses",
        1337,
        "4af7452fbf38e501067d27170b6a7fb5ab409498",
        "861973326a18616941224d95885ada006746eaac",
    ),
    ReleasePhaseEvidence(
        3,
        "Return compact RPG turn responses",
        1338,
        "43a920879cd39e6f00b4d70a33305f034f18da81",
        "e24bbdd8b697f8e23217bbc83c246a8854a30f3d",
    ),
    ReleasePhaseEvidence(
        4,
        "Persist RPG interaction progression",
        1339,
        "1f10de935de8d30a5825b81cb1107362d9b81390",
        "3176b6759f3f8beef57cdf57332789134ee7cc14",
    ),
    ReleasePhaseEvidence(
        5,
        "Trace the full RPG foreground pipeline",
        1340,
        "a562bd644fc33d9c6638271c0bac605715245ea8",
        "c81f720f6903aa1677b6b16016d61af10d36eee9",
    ),
    ReleasePhaseEvidence(
        6,
        "Use append-only RPG interaction persistence",
        1341,
        "d07c1737cd1700a9b5da43210846641af68f5747",
        "382aa58b20ac26a0f46a359d62ecbc479db724ee",
    ),
    ReleasePhaseEvidence(
        7,
        "Enforce benchmark-quality RPG dialogue",
        1342,
        "439da20a1b00fa9a566d47bcea980b36d6a5abc4",
        "642fc90132218223769c403e4004122ce0983a58",
    ),
    ReleasePhaseEvidence(
        8,
        "Render RPG turns incrementally in the web client",
        1343,
        "75f3745d441cb70df75d8b3c5d3589df81b35664",
        "567df70d67aa7e0e62785e184c6d2df493a31eda",
    ),
    ReleasePhaseEvidence(
        9,
        "Align authoritative turns with deferred narration",
        1344,
        "a66cc1809ffedd1c81ae10b9280067c7e9f008bb",
        "6b3a029f7b2284009f1b4373db58fe39c9fec8b6",
    ),
    ReleasePhaseEvidence(
        10,
        "Make interactive RPG release gates permanent",
        1345,
        "cf6d6c1a1c453fb7ae0c11f7735708466a336ae9",
        "635cdbd181da12cad06d87cc9346806ef4edcd37",
    ),
    ReleasePhaseEvidence(
        11,
        "Finalize interactive RPG release and local validation",
        1346,
        "3d190582333f3fec7011a069ebad309cddbc5eeb",
        "8b11adfda8aedb40a6aad11f4125a010f14aa1bb",
    ),
)


def local_live_acceptance_criteria() -> dict[str, Any]:
    return {
        "format_version": INTERACTIVE_RELEASE_VERSION,
        "minimum_distinct_interactions": 3,
        "same_submission_replay_must_match_interaction_id": True,
        "maximum_turn_response_bytes": 50_000,
        "required_contract_version": "rpg_turn_response_v2",
        "required_visible_text": True,
        "required_monotonic_interaction_ids": True,
        "target_median_seconds": TARGET_DIALOGUE_MEDIAN_SECONDS,
        "target_p95_seconds": TARGET_DIALOGUE_P95_SECONDS,
        "maximum_browser_commit_visible_ms": MAX_BROWSER_COMMIT_VISIBLE_MS,
        "target_is_operator_evidence_not_ci_assertion": True,
    }
