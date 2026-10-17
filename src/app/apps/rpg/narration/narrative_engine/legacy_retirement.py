"""Executable proof that retired prose publishers cannot own production output."""
from __future__ import annotations

from dataclasses import dataclass
from app.caching.bounded_cache import bounded_lru_cache
from pathlib import Path
from typing import Any, Mapping


_RETIRED_IMPORT_TOKENS = (
    "app.apps.rpg.narration.response_generation.legacy_bridge",
    "app.apps.rpg.narration.ai.world_scene_narrator_runtime",
    "world_scene_narrator_runtime",
    "first_call_dialogue_result",
    "legacy_response_generator",
    "_legacy_narrate_scene",
)
_RETIRED_PUBLICATION_TOKENS = (
    "legacy_visible_prose_consumed = True",
    'legacy_visible_prose_consumed"] = True',
    'result["legacy_publisher"]',
    'result["visible_publisher"]',
)
_PRODUCTION_OWNER_PATHS = (
    "src/app/apps/rpg/edge/api/turn_pipeline.py",
    "src/app/apps/rpg/session/turn_presenter.py",
    "src/app/apps/rpg/session/narrative_engine_bridge.py",
    "src/app/apps/rpg/narration/narrative_engine/consumer_publish.py",
    "src/app/apps/rpg/narration/narrative_engine/production_path.py",
)
_COMPATIBILITY_ONLY_PATHS = (
    "src/app/apps/rpg/narration/response_generation/legacy_bridge.py",
    "src/app/apps/rpg/narration/ai/world_scene_narrator.py",
    "src/app/apps/rpg/narration/ai/world_scene_narrator_runtime.py",
)


@dataclass(frozen=True)
class LegacyPublisherRetirementAudit:
    passed: bool
    checks: Mapping[str, bool]
    violations: tuple[str, ...]
    production_owner_paths: tuple[str, ...]
    compatibility_only_paths: tuple[str, ...]
    forbidden_hits: Mapping[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "violations": list(self.violations),
            "production_owner_paths": list(self.production_owner_paths),
            "compatibility_only_paths": list(self.compatibility_only_paths),
            "forbidden_hits": {
                path: list(tokens) for path, tokens in self.forbidden_hits.items()
            },
            "legacy_publisher_deletion_certified": self.passed,
            "deletion_scope": "visible_production_ownership",
        }


def audit_legacy_publisher_retirement(
    repository_root: Path,
) -> LegacyPublisherRetirementAudit:
    """Fail when any production owner imports or publishes through retired code."""

    root = repository_root.resolve()
    sources: dict[str, str] = {}
    missing_paths: list[str] = []
    for relative in _PRODUCTION_OWNER_PATHS:
        path = root / relative
        if not path.is_file():
            missing_paths.append(relative)
            continue
        sources[relative] = path.read_text(encoding="utf-8")

    forbidden_hits: dict[str, tuple[str, ...]] = {}
    for relative, source in sources.items():
        hits = tuple(
            token
            for token in (*_RETIRED_IMPORT_TOKENS, *_RETIRED_PUBLICATION_TOKENS)
            if token in source
        )
        if hits:
            forbidden_hits[relative] = hits

    gateway = sources.get("src/app/apps/rpg/edge/api/turn_pipeline.py", "")
    presenter = sources.get("src/app/apps/rpg/session/turn_presenter.py", "")
    bridge = sources.get("src/app/apps/rpg/session/narrative_engine_bridge.py", "")
    publisher = sources.get("src/app/apps/rpg/narration/narrative_engine/consumer_publish.py", "")
    production = sources.get("src/app/apps/rpg/narration/narrative_engine/production_path.py", "")
    compatibility_present = tuple(
        relative for relative in _COMPATIBILITY_ONLY_PATHS if (root / relative).is_file()
    )
    checks = {
        "production_owner_files_present": not missing_paths,
        "retired_imports_deleted_from_production_owners": not forbidden_hits,
        "gateway_uses_single_turn_presenter": "present_authoritative_turn(" in gateway,
        "gateway_uses_canonical_consumer_publisher": (
            "attach_canonical_consumer_bundle(" in gateway
        ),
        "presenter_requires_one_canonical_response": (
            "TurnPresentationInvariantError" in presenter
            and "turn_presentation_request_count" in presenter
        ),
        "bridge_marks_legacy_prose_unconsumed": (
            'result["legacy_visible_prose_consumed"] = False' in bridge
        ),
        "publisher_uses_canonical_guard": "publish_canonical_bundle(" in publisher,
        "compatibility_fields_projection_only": (
            'result["legacy_compatibility_fields_source"] = "canonical_projection_only"'
            in production
        ),
        "production_certification_fails_closed": (
            "NarrativeProductionPathError" in production
            and "if not certification.passed" in production
        ),
    }
    violations = [name for name, passed in checks.items() if not passed]
    violations.extend(f"missing:{path}" for path in missing_paths)
    violations.extend(f"forbidden:{path}" for path in sorted(forbidden_hits))
    return LegacyPublisherRetirementAudit(
        passed=not violations,
        checks=checks,
        violations=tuple(violations),
        production_owner_paths=_PRODUCTION_OWNER_PATHS,
        compatibility_only_paths=compatibility_present,
        forbidden_hits=forbidden_hits,
    )


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def production_legacy_retirement_audit() -> LegacyPublisherRetirementAudit:
    return audit_legacy_publisher_retirement(Path(__file__).resolve().parents[6])


