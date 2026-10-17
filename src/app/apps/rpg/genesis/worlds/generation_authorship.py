"""Trusted authorship and field-level origin checks for World Forge content."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


# ``enum.StrEnum`` was added in Python 3.11.  The RPG launcher still supports
# Python 3.10 environments, so provide its small required surface locally.
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str(self.value)


class AuthorshipClass(StrEnum):
    LLM_AUTHORED = "llm_authored"
    HUMAN_AUTHORED = "human_authored"
    HUMAN_EDITED_LLM = "human_edited_llm"
    MACHINE_STRUCTURED = "machine_structured"
    LEGACY_UNKNOWN = "legacy_unknown"
    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class AuthorshipPolicy(StrEnum):
    LLM_REQUIRED = "llm_required"
    AUTHORED_REQUIRED = "authored_required"
    MACHINE_ALLOWED = "machine_allowed"
    STRUCTURAL_ONLY = "structural_only"


_ALLOWED_LORE_CLASSES = {
    AuthorshipClass.LLM_AUTHORED.value,
    AuthorshipClass.HUMAN_AUTHORED.value,
    AuthorshipClass.HUMAN_EDITED_LLM.value,
}
_STRUCTURAL_KEYS = {
    "id",
    "entity_id",
    "topic_id",
    "fact_id",
    "field_id",
    "source_id",
    "target_id",
    "subject",
    "predicate",
    "kind",
    "type",
    "source",
    "authority",
    "approved_authority",
    "visibility",
    "status",
    "schema_version",
    "value_type",
    "semantic_role",
    "generator",
    "provider",
    "model",
    "prompt_version",
    "generator_version",
    "response_format",
    "finish_reason",
}
_MACHINE_CONTAINERS = {
    "provenance",
    "authorship",
    "origin_ledger",
    "generation_artifact",
    "lookup",
    "validation",
    "dependency_hashes",
    "dependency_trust",
    "quick_facts",
    "presentation",
}
_EXPLICIT_LORE_KEYS = {
    "name",
    "summary",
    "short_summary",
    "description",
    "content",
    "expanded_description",
    "display_text",
    "body",
    "text",
    "full_text",
    "summary_120",
    "summary_500",
    "paragraph",
    "paragraphs",
    "quote",
    "subtitle",
    "premise",
    "setup",
    "history",
    "backstory",
    "personality",
    "appearance",
    "atmosphere",
    "sensory_profile",
    "goal",
    "goals",
    "motive",
    "motives",
    "pressure",
    "pressures",
    "current_pressure",
    "current_state",
    "next_tick_change",
    "beliefs",
    "values",
    "customs",
    "rumour",
    "rumours",
    "hooks",
    "stakes",
    "outcomes",
    "complications",
    "objectives",
    "initial_evidence",
    "speech_style",
    "behaviour",
    "behavior",
    "distinction",
}
_BLOCKED_MARKERS = {
    "deterministic_fallback",
    "deterministic_profile_fixture_v1",
    "deterministic_world_forge_v1",
}


class AuthorshipValidationError(ValueError):
    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        super().__init__(
            "world_lore_authorship_invalid:"
            + json.dumps(self.report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def string_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pointer_segment(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _walk_strings(
    value: Any,
    *,
    segments: tuple[str, ...] = (),
    parent: Mapping[str, Any] | None = None,
) -> Iterable[tuple[str, str, str, Mapping[str, Any] | None, tuple[str, ...]]]:
    if isinstance(value, Mapping):
        current = dict(value)
        for key, item in current.items():
            key_text = str(key)
            yield from _walk_strings(
                item,
                segments=(*segments, key_text),
                parent=current,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _walk_strings(
                item,
                segments=(*segments, str(index)),
                parent=parent,
            )
        return
    if isinstance(value, str) and value.strip():
        path = "/" + "/".join(_pointer_segment(segment) for segment in segments)
        key = segments[-1] if segments else ""
        yield path, key, value, parent, segments


def _json_object_string(value: str) -> bool:
    try:
        return isinstance(json.loads(value), Mapping)
    except Exception:
        return False


def _looks_like_identifier(value: str) -> bool:
    text = value.strip()
    if not text or any(character.isspace() for character in text):
        return False
    return ":" in text or text.startswith(("sha256:", "ent_", "fact_", "topic_"))


def is_lore_string(
    path: str,
    key: str,
    value: str,
    parent: Mapping[str, Any] | None,
    segments: tuple[str, ...],
) -> bool:
    """Return whether a string is authored lore rather than machine presentation."""

    if any(segment in _MACHINE_CONTAINERS for segment in segments[:-1]):
        return False
    if key in _STRUCTURAL_KEYS or key.endswith(("_id", "_ids", "_hash", "_version")):
        return False
    if key in {"title", "label"} and "sections" in segments and "dossier" in segments:
        return False
    if key == "content" and isinstance(parent, Mapping):
        if str(parent.get("source") or "") == "profile_structured_fact_compiler_v1":
            return not _json_object_string(value)
    if key in _EXPLICIT_LORE_KEYS:
        return True
    if _looks_like_identifier(value):
        return False
    # Unknown free-form strings are treated as authored by default. This fails closed
    # when a new lore field is added without an explicit machine policy.
    return True


def lore_string_leaves(value: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for path, key, text, parent, segments in _walk_strings(value):
        if is_lore_string(path, key, text, parent, segments):
            rows.append({"path": path, "value": text, "content_hash": string_hash(text)})
    return tuple(rows)


def _payload_without_authorship(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    provenance = payload.get("provenance")
    if isinstance(provenance, Mapping):
        normalized = deepcopy(dict(provenance))
        normalized.pop("authorship", None)
        payload["provenance"] = normalized
    return payload


def _artifact_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = deepcopy(dict(value))
    artifact.pop("artifact_hash", None)
    return artifact


def build_generation_artifact(
    candidate: Mapping[str, Any],
    *,
    run_id: str,
    job_id: str,
    topic_id: str,
    provider: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Build the server-owned artifact persisted in a topic-result provider record."""

    provider_row = dict(provider)
    settings_row = dict(settings or {})
    parsed_hash = content_hash(_payload_without_authorship(candidate))
    recovery = provider_row.get("structured_recovery")
    recovery = dict(recovery) if isinstance(recovery, Mapping) else {}
    raw_hash = str(
        provider_row.get("raw_response_hash")
        or provider_row.get("provider_response_hash")
        or recovery.get("original_candidate_hash")
        or ""
    )
    raw_hash_kind = "provider_response"
    if not raw_hash:
        # Older providers expose only the parsed typed response. Keep the artifact
        # server-owned and explicit about this weaker evidence so migration can find it.
        raw_hash = parsed_hash
        raw_hash_kind = "parsed_payload_fallback"
    transformations: list[str] = []
    method = str(recovery.get("method") or "")
    if method:
        transformations.append(method)
    transformations.extend(str(value) for value in recovery.get("repair_codes") or ())
    identity_seed = {
        "run_id": run_id,
        "job_id": job_id,
        "topic_id": topic_id,
        "parsed_payload_hash": parsed_hash,
        "provider": str(provider_row.get("provider") or ""),
        "model": str(provider_row.get("model") or ""),
    }
    artifact = {
        "schema_version": "rpg_world_generation_artifact_v1",
        "generation_artifact_id": "genart:" + content_hash(identity_seed)[:32],
        "generation_run_id": run_id,
        "job_id": job_id,
        "topic_id": topic_id,
        "provider": str(provider_row.get("provider") or ""),
        "model": str(provider_row.get("model") or ""),
        "generator": str(provider_row.get("generator") or ""),
        "generator_version": str(settings_row.get("generator_version") or ""),
        "prompt_version": str(settings_row.get("prompt_version") or ""),
        "raw_response_hash": raw_hash,
        "raw_response_hash_kind": raw_hash_kind,
        "parsed_payload_hash": parsed_hash,
        "attempt": max(1, int(attempt or 1)),
        "authorship_class": AuthorshipClass.LLM_AUTHORED.value,
        "transformations": list(dict.fromkeys(transformations)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact["artifact_hash"] = content_hash(_artifact_without_hash(artifact))
    return artifact


def _origin_entry(
    row: Mapping[str, str],
    *,
    authorship_class: str,
    artifact_id: str = "",
    event_id: str = "",
    parent_origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(row["path"]),
        "authorship_class": authorship_class,
        "generation_artifact_id": artifact_id,
        "human_edit_event_id": event_id,
        "source_json_pointer": str(row["path"]),
        "content_hash": str(row["content_hash"]),
    }
    if parent_origin:
        entry["parent_origin"] = {
            "authorship_class": str(parent_origin.get("authorship_class") or ""),
            "generation_artifact_id": str(parent_origin.get("generation_artifact_id") or ""),
            "human_edit_event_id": str(parent_origin.get("human_edit_event_id") or ""),
            "content_hash": str(parent_origin.get("content_hash") or ""),
        }
    return entry


def attach_llm_authorship(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _payload_without_authorship(candidate)
    artifact_id = str(artifact.get("generation_artifact_id") or "")
    ledger = [
        _origin_entry(
            row,
            authorship_class=AuthorshipClass.LLM_AUTHORED.value,
            artifact_id=artifact_id,
        )
        for row in lore_string_leaves(payload)
    ]
    provenance = dict(payload.get("provenance") or {})
    provenance["authorship"] = {
        "schema_version": "rpg_world_field_origin_ledger_v1",
        "authorship_class": AuthorshipClass.LLM_AUTHORED.value,
        "generation_artifact_id": artifact_id,
        "origin_ledger": ledger,
    }
    payload["provenance"] = provenance
    return payload


def attach_human_authorship(
    candidate: Mapping[str, Any],
    *,
    event_id: str,
    prior_candidate: Mapping[str, Any] | None = None,
    edited_llm: bool = False,
) -> dict[str, Any]:
    """Attach human origin while retaining unchanged trusted origins where possible."""

    payload = _payload_without_authorship(candidate)
    prior = dict(prior_candidate or {})
    prior_authorship = dict(dict(prior.get("provenance") or {}).get("authorship") or {})
    prior_ledger = {
        str(row.get("path") or ""): dict(row)
        for row in prior_authorship.get("origin_ledger") or ()
        if isinstance(row, Mapping)
    }
    human_class = (
        AuthorshipClass.HUMAN_EDITED_LLM.value
        if edited_llm
        else AuthorshipClass.HUMAN_AUTHORED.value
    )
    ledger: list[dict[str, Any]] = []
    for row in lore_string_leaves(payload):
        previous = prior_ledger.get(str(row["path"]))
        if previous and str(previous.get("content_hash") or "") == str(row["content_hash"]):
            ledger.append(previous)
            continue
        ledger.append(
            _origin_entry(
                row,
                authorship_class=human_class,
                event_id=event_id,
                parent_origin=previous,
            )
        )
    provenance = dict(payload.get("provenance") or {})
    provenance["authorship"] = {
        "schema_version": "rpg_world_field_origin_ledger_v1",
        "authorship_class": human_class,
        "human_edit_event_id": event_id,
        "origin_ledger": ledger,
    }
    payload["provenance"] = provenance
    return payload


def _blocked_marker_rows(value: Any, path: str = "") -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}/{_pointer_segment(key)}"
            key_text = str(key)
            if key_text in {"quality_enriched", "generated_from_legacy", "presentation_derived_from_structured_facts"} and item is True:
                blocked.append({"path": child, "code": key_text})
            elif key_text == "used_llm" and item is False:
                blocked.append({"path": child, "code": "used_llm_false"})
            elif key_text == "generator" and str(item) in _BLOCKED_MARKERS:
                blocked.append({"path": child, "code": str(item)})
            blocked.extend(_blocked_marker_rows(item, child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            blocked.extend(_blocked_marker_rows(item, f"{path}/{index}"))
    return blocked


def validate_publishable_authorship(
    candidate: Mapping[str, Any],
    *,
    server_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate every lore string against a server artifact or explicit human event."""

    payload = dict(candidate)
    provenance = dict(payload.get("provenance") or {})
    authorship = dict(provenance.get("authorship") or {})
    ledger = {
        str(row.get("path") or ""): dict(row)
        for row in authorship.get("origin_ledger") or ()
        if isinstance(row, Mapping)
    }
    artifact = dict(server_artifact or {})
    artifact_id = str(artifact.get("generation_artifact_id") or "")
    blockers: list[dict[str, Any]] = list(_blocked_marker_rows(payload))

    if artifact:
        expected_artifact_hash = content_hash(_artifact_without_hash(artifact))
        if str(artifact.get("artifact_hash") or "") != expected_artifact_hash:
            blockers.append({"path": "/provenance/authorship", "code": "generation_artifact_hash_mismatch"})
        expected_payload_hash = content_hash(_payload_without_authorship(payload))
        if str(artifact.get("parsed_payload_hash") or "") != expected_payload_hash:
            blockers.append({"path": "/", "code": "generation_artifact_payload_hash_mismatch"})
        if not str(artifact.get("provider") or "") or not str(artifact.get("model") or ""):
            blockers.append({"path": "/provenance/authorship", "code": "generation_artifact_provider_or_model_missing"})
        if str(artifact.get("authorship_class") or "") != AuthorshipClass.LLM_AUTHORED.value:
            blockers.append({"path": "/provenance/authorship", "code": "generation_artifact_not_llm_authored"})

    leaves = lore_string_leaves(payload)
    for row in leaves:
        path = str(row["path"])
        origin = ledger.get(path)
        if origin is None:
            blockers.append({"path": path, "code": "trusted_authorship_missing"})
            continue
        if str(origin.get("content_hash") or "") != str(row["content_hash"]):
            blockers.append({"path": path, "code": "origin_content_hash_mismatch"})
            continue
        authorship_class = str(origin.get("authorship_class") or "")
        if authorship_class not in _ALLOWED_LORE_CLASSES:
            blockers.append({"path": path, "code": "authorship_class_not_publishable", "authorship_class": authorship_class})
            continue
        if authorship_class == AuthorshipClass.LLM_AUTHORED.value:
            if not artifact_id:
                blockers.append({"path": path, "code": "server_generation_artifact_missing"})
            elif str(origin.get("generation_artifact_id") or "") != artifact_id:
                blockers.append({"path": path, "code": "origin_artifact_mismatch"})
        elif not str(origin.get("human_edit_event_id") or ""):
            blockers.append({"path": path, "code": "human_authorship_event_missing"})

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for blocker in blockers:
        unique[(str(blocker.get("path") or ""), str(blocker.get("code") or ""))] = blocker
    ordered = [unique[key] for key in sorted(unique)]
    return {
        "schema_version": "rpg_world_publishable_authorship_report_v1",
        "publishable": not ordered,
        "lore_string_count": len(leaves),
        "origin_count": len(ledger),
        "generation_artifact_id": artifact_id,
        "blocked_paths": ordered,
    }


def require_publishable_authorship(
    candidate: Mapping[str, Any],
    *,
    server_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = validate_publishable_authorship(candidate, server_artifact=server_artifact)
    if not report["publishable"]:
        raise AuthorshipValidationError(report)
    return report


def prove_structural_repair_non_authoring(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove structural repair preserved the multiset of authored string values."""

    before_values = Counter(row["value"] for row in lore_string_leaves(before))
    after_values = Counter(row["value"] for row in lore_string_leaves(after))
    added = list((after_values - before_values).elements())
    removed = list((before_values - after_values).elements())
    report = {
        "schema_version": "rpg_world_structural_repair_proof_v1",
        "non_authoring": not added and not removed,
        "added_string_hashes": sorted(string_hash(value) for value in added),
        "removed_string_hashes": sorted(string_hash(value) for value in removed),
    }
    if not report["non_authoring"]:
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v1",
                "publishable": False,
                "blocked_paths": [
                    {"path": "/", "code": "structural_repair_changed_lore_strings"}
                ],
                "structural_repair": report,
            }
        )
    return report


__all__ = [
    "AuthorshipClass",
    "AuthorshipPolicy",
    "AuthorshipValidationError",
    "attach_human_authorship",
    "attach_llm_authorship",
    "build_generation_artifact",
    "content_hash",
    "lore_string_leaves",
    "prove_structural_repair_non_authoring",
    "require_publishable_authorship",
    "string_hash",
    "validate_publishable_authorship",
]
