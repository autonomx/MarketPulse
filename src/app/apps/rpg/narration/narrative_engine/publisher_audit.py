"""Static production-path audit for Narrative Engine publisher ownership."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PublisherOwnershipAudit:
    passed: bool
    checks: dict[str, bool]
    violations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "violations": list(self.violations),
        }


def audit_publisher_ownership(repository_root: Path) -> PublisherOwnershipAudit:
    gateway = repository_root / "src" / "app" / "apps" / "rpg" / "edge" / "api" / "turn_pipeline.py"
    publisher = repository_root / "src" / "app" / "apps" / "rpg" / "narration" / "narrative_engine" / "consumer_publish.py"
    guard = repository_root / "src" / "app" / "apps" / "rpg" / "narration" / "narrative_engine" / "publisher_guard.py"
    bridge = repository_root / "src" / "app" / "apps" / "rpg" / "session" / "narrative_engine_bridge.py"
    sources = {
        "gateway": gateway.read_text(encoding="utf-8"),
        "publisher": publisher.read_text(encoding="utf-8"),
        "guard": guard.read_text(encoding="utf-8"),
        "bridge": bridge.read_text(encoding="utf-8"),
    }
    forbidden_gateway_imports = (
        "world_scene_narrator_runtime",
        "environmental_narration_runtime",
        "first_call_dialogue_result",
        "legacy_response_generator",
    )
    checks = {
        "gateway_uses_canonical_consumer_publisher": "attach_canonical_consumer_bundle" in sources["gateway"],
        "publisher_uses_guard": "publish_canonical_bundle" in sources["publisher"],
        "guard_names_single_owner": 'CANONICAL_PUBLISHER = "unified_narrative_engine_v1"' in sources["guard"],
        "gateway_has_no_legacy_publisher_import": not any(
            token in sources["gateway"] for token in forbidden_gateway_imports
        ),
        "bridge_only_builds_canonical_response": (
            "canonical_narrative_response" in sources["bridge"]
            and "publish_canonical_bundle" not in sources["bridge"]
        ),
        "compatibility_fields_are_projected_after_canonical": (
            'result["narrative_projections"]' in sources["publisher"]
            and 'result["visible_response"]' in sources["publisher"]
        ),
    }
    violations = tuple(name for name, passed in checks.items() if not passed)
    return PublisherOwnershipAudit(
        passed=not violations,
        checks=checks,
        violations=violations,
    )
