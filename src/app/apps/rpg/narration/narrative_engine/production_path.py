"""Final fail-closed production certification for canonical RPG presentation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .certification import certify_narrative_persistence_and_delivery
from .projections import canonical_consumer_bundle, legacy_response_projection
from .publisher_guard import CANONICAL_PUBLISHER
from .serialization import canonical_response_from_dict


class NarrativeProductionPathError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarrativeProductionCertification:
    passed: bool
    response_id: str
    content_hash: str
    checks: Mapping[str, bool]
    violations: tuple[str, ...]
    diagnostics: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "response_id": self.response_id,
            "content_hash": self.content_hash,
            "checks": dict(self.checks),
            "violations": list(self.violations),
            "diagnostics": dict(self.diagnostics),
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def retire_legacy_presentation_ownership(result: dict[str, Any]) -> dict[str, Any]:
    """Keep compatibility fields, but overwrite them only from canonical projections."""
    canonical_raw = result.get("canonical_narrative_response")
    if not isinstance(canonical_raw, Mapping):
        raise NarrativeProductionPathError("canonical narrative response is required")
    response = canonical_response_from_dict(canonical_raw)
    legacy = legacy_response_projection(response)
    bundle = canonical_consumer_bundle(response)
    for key in (
        "narration",
        "final_narration",
        "summary",
        "npc",
        "dialogue_blocks",
        "visible_response",
    ):
        result[key] = legacy[key]
    result["narrative_projections"] = bundle
    result["legacy_presentation_ownership_retired"] = True
    result["legacy_compatibility_fields_source"] = "canonical_projection_only"
    result["canonical_narrative_source"] = CANONICAL_PUBLISHER

    nested = _mapping(result.get("result"))
    if nested:
        for key in (
            "narration",
            "final_narration",
            "summary",
            "npc",
            "dialogue_blocks",
            "visible_response",
        ):
            nested[key] = legacy[key]
        nested["narrative_projections"] = bundle
        nested["legacy_presentation_ownership_retired"] = True
        nested["legacy_compatibility_fields_source"] = "canonical_projection_only"
        nested["canonical_narrative_source"] = CANONICAL_PUBLISHER
        result["result"] = nested
    return result


def _latest_interaction(result: Mapping[str, Any]) -> dict[str, Any]:
    session = _mapping(result.get("session"))
    runtime = _mapping(session.get("runtime_state"))
    interactions = runtime.get("recent_interactions")
    if not isinstance(interactions, list):
        return {}
    response_id = str(
        _mapping(result.get("canonical_narrative_response")).get("response_id")
        or ""
    )
    for value in reversed(interactions):
        row = _mapping(value)
        if str(row.get("narrative_response_id") or "") == response_id:
            return row
    return _mapping(interactions[-1]) if interactions else {}


def certify_production_narrative_result(
    result: Mapping[str, Any],
) -> NarrativeProductionCertification:
    canonical_raw = result.get("canonical_narrative_response")
    if not isinstance(canonical_raw, Mapping):
        return NarrativeProductionCertification(
            passed=False,
            response_id="",
            content_hash="",
            checks={"canonical_response_present": False},
            violations=("canonical_response_present",),
            diagnostics={},
        )

    response = canonical_response_from_dict(canonical_raw)
    expected_bundle = canonical_consumer_bundle(response)
    expected_legacy = legacy_response_projection(response)
    bundle = _mapping(result.get("narrative_projections"))
    telemetry = _mapping(result.get("narrative_publisher_telemetry"))
    visible = _mapping(result.get("visible_response"))
    latest = _latest_interaction(result)
    persistence = certify_narrative_persistence_and_delivery(response)
    checks = {
        "canonical_response_present": True,
        "canonical_source_owned": (
            str(result.get("canonical_narrative_source") or "")
            == CANONICAL_PUBLISHER
        ),
        "publisher_guard_owned": (
            str(result.get("narrative_publisher") or "") == CANONICAL_PUBLISHER
        ),
        "zero_alternate_publishers": (
            telemetry.get("zero_alternate_publishers") is True
            and int(telemetry.get("alternate_publish_count") or 0) == 0
        ),
        "bundle_response_id_matches": (
            bundle.get("response_id") == response.response_id
        ),
        "bundle_hash_matches": (
            bundle.get("content_hash") == response.content_hash
        ),
        "visible_projection_matches": (
            visible == expected_bundle["visible_response"]
        ),
        "narration_projection_matches": (
            str(result.get("narration") or "") == expected_legacy["narration"]
        ),
        "summary_projection_matches": (
            str(result.get("summary") or "") == expected_legacy["summary"]
        ),
        "npc_projection_matches": (
            _mapping(result.get("npc")) == expected_legacy["npc"]
        ),
        "compatibility_source_is_projection_only": (
            result.get("legacy_compatibility_fields_source")
            == "canonical_projection_only"
        ),
        "legacy_ownership_retired": (
            result.get("legacy_presentation_ownership_retired") is True
        ),
        "roundtrip_and_delivery_certified": persistence.get("passed") is True,
        "no_legacy_publisher_marker": not bool(
            result.get("legacy_publisher") or result.get("visible_publisher")
        ),
        "session_projection_matches": (
            not latest
            or (
                latest.get("narrative_response_id") == response.response_id
                and latest.get("narrative_content_hash") == response.content_hash
                and _mapping(latest.get("visible_response"))
                == expected_bundle["visible_response"]
            )
        ),
    }
    violations = tuple(name for name, passed in checks.items() if not passed)
    return NarrativeProductionCertification(
        passed=not violations,
        response_id=response.response_id,
        content_hash=response.content_hash,
        checks=checks,
        violations=violations,
        diagnostics={
            "publisher_telemetry": telemetry,
            "persistence_and_delivery": persistence,
            "block_ids": [block.block_id for block in response.blocks],
            "consumer_bundle_schema": bundle.get("schema_version"),
        },
    )


def enforce_production_narrative_result(result: dict[str, Any]) -> dict[str, Any]:
    retired = retire_legacy_presentation_ownership(result)
    certification = certify_production_narrative_result(retired)
    retired["narrative_production_certification"] = certification.as_dict()
    if not certification.passed:
        raise NarrativeProductionPathError(
            "canonical RPG narrative production certification failed: "
            + ", ".join(certification.violations)
        )
    from app.apps.rpg.narration.narrative_retirement import record_narrative_retirement

    try:
        return record_narrative_retirement(retired)
    except Exception as exc:
        raise NarrativeProductionPathError(
            f"canonical RPG narrative retirement proof failed: {exc}"
        ) from exc
