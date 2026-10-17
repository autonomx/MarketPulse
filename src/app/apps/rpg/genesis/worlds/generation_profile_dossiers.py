"""Profile-aware dossier policies for strict World Forge compilation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_POLICY_ACTOR = "actor"
_POLICY_PLACE = "place"
_POLICY_INSTITUTION = "institution"
_SUPPORTED_POLICIES = {_POLICY_ACTOR, _POLICY_PLACE, _POLICY_INSTITUTION}
_MOBILE_STATUSES = {"itinerant", "nomadic", "unplaced"}


@dataclass(frozen=True)
class ProfileDossierIssue:
    code: str
    domain_id: str
    entity_id: str
    policy: str
    missing_fields: tuple[str, ...]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "domain_id": self.domain_id,
            "entity_id": self.entity_id,
            "policy": self.policy,
            "missing_fields": list(self.missing_fields),
            "message": self.message,
            "blocking": True,
        }


class ProfileDossierCompilationError(ValueError):
    def __init__(self, issues: Sequence[ProfileDossierIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.domain_id}:{issue.entity_id}:"
            f"{','.join(issue.missing_fields)}"
            for issue in self.issues
        )
        super().__init__("profile_dossier_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "profile_dossier_integrity_failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row)


def _profile(topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    graph = _mapping(topic_graph)
    return _mapping(_mapping(graph.get("metadata")).get("resolved_profile"))


def _text(row: Mapping[str, Any], field_id: str) -> str:
    return " ".join(str(row.get(field_id) or "").split())


def _array(row: Mapping[str, Any], field_id: str) -> tuple[str, ...] | None:
    value = row.get(field_id)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    return tuple(str(item).strip() for item in value if str(item).strip())


def dossier_policy(domain: Mapping[str, Any]) -> str:
    """Resolve policy from explicit guidance, domain identity, roles, then kind."""

    guidance = _mapping(domain.get("generation_guidance"))
    explicit = str(guidance.get("dossier_policy") or "").strip().casefold()
    if explicit in _SUPPORTED_POLICIES:
        return explicit
    domain_id = str(domain.get("domain_id") or "").strip().casefold()
    if domain_id == "actors":
        return _POLICY_ACTOR
    if domain_id == "places":
        return _POLICY_PLACE
    if domain_id == "groups":
        return _POLICY_INSTITUTION
    roles = {
        str(value).strip().casefold()
        for value in domain.get("semantic_roles") or ()
        if str(value).strip()
    }
    if "initial_actors" in roles:
        return _POLICY_ACTOR
    kind = str(domain.get("entity_kind") or "").strip().casefold()
    if kind in {"actor", "npc", "person", "character"}:
        return _POLICY_ACTOR
    if kind in {"place", "location", "settlement", "site"}:
        return _POLICY_PLACE
    if kind in {"group", "faction", "institution", "organization", "organisation"}:
        return _POLICY_INSTITUTION
    return ""


def _actor_missing(entity: Mapping[str, Any]) -> tuple[str, ...]:
    minimum_text = {
        "name": 2,
        "appearance": 20,
        "personality": 20,
        "backstory": 30,
        "speech_style": 10,
    }
    missing = [
        field_id
        for field_id, minimum in minimum_text.items()
        if len(_text(entity, field_id)) < minimum
    ]
    for field_id in ("goals", "motives"):
        if not _array(entity, field_id):
            missing.append(field_id)
    for field_id in ("faction_ids", "secrets", "known_facts"):
        if _array(entity, field_id) is None:
            missing.append(field_id)
    if str(entity.get("dossier_status") or "") != "complete":
        missing.append("dossier_status")
    location_id = str(entity.get("location_id") or "").strip()
    mobility = str(entity.get("mobility_status") or "").strip().casefold()
    if not location_id and mobility not in _MOBILE_STATUSES:
        missing.append("location_id_or_mobility_status")
    return tuple(sorted(set(missing)))


def _place_missing(entity: Mapping[str, Any]) -> tuple[str, ...]:
    minimum_text = {"name": 2, "region_id": 3, "sensory_profile": 20}
    missing = [
        field_id
        for field_id, minimum in minimum_text.items()
        if len(_text(entity, field_id)) < minimum
    ]
    if str(entity.get("dossier_status") or "") != "complete":
        missing.append("dossier_status")
    return tuple(sorted(set(missing)))


def _institution_missing(entity: Mapping[str, Any]) -> tuple[str, ...]:
    missing = [] if len(_text(entity, "name")) >= 2 else ["name"]
    for field_id in ("values", "goals"):
        if not _array(entity, field_id):
            missing.append(field_id)
    return tuple(sorted(set(missing)))


def profile_dossier_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ProfileDossierIssue, ...]:
    profile = _profile(topic_graph)
    domains = {
        str(domain.get("domain_id") or ""): domain
        for domain in _rows(profile.get("domains"))
        if str(domain.get("domain_id") or "")
    }
    issues: list[ProfileDossierIssue] = []
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        domain_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        policy = dossier_policy(domains.get(domain_id, {}))
        if not policy:
            continue
        for entity_index, entity in enumerate(_rows(candidate.get("entities")), start=1):
            entity_id = str(entity.get("id") or f"{domain_id}:entity:{entity_index}")
            kind = str(entity.get("kind") or "").strip().casefold()
            if (
                (policy == _POLICY_ACTOR and kind == "npc")
                or (policy == _POLICY_PLACE and kind == "location")
                or (policy == _POLICY_INSTITUTION and kind == "faction")
            ):
                continue
            missing = {
                _POLICY_ACTOR: _actor_missing,
                _POLICY_PLACE: _place_missing,
                _POLICY_INSTITUTION: _institution_missing,
            }[policy](entity)
            if missing:
                issues.append(
                    ProfileDossierIssue(
                        code=f"incomplete_profile_{policy}_dossier",
                        domain_id=domain_id,
                        entity_id=entity_id,
                        policy=policy,
                        missing_fields=missing,
                        message=(
                            f"Profile {policy} dossier is missing required fields: "
                            + ",".join(missing)
                        ),
                    )
                )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (issue.domain_id, issue.entity_id, issue.code),
        )
    )


def profile_dossier_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    profile = _profile(topic_graph)
    domains = _rows(profile.get("domains"))
    policies = {
        str(domain.get("domain_id") or ""): dossier_policy(domain)
        for domain in domains
        if dossier_policy(domain)
    }
    issues = profile_dossier_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_generation_profile_dossiers_v1",
        "passed": not issues,
        "policies": policies,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "profile_domains": len(domains),
            "policy_domains": len(policies),
            "dossier_issues": len(issues),
        },
    }


def require_profile_dossier_quality(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = profile_dossier_issues(topic_rows, topic_graph)
    if issues:
        raise ProfileDossierCompilationError(issues)
    return profile_dossier_report(topic_rows, topic_graph)


__all__ = [
    "ProfileDossierCompilationError",
    "ProfileDossierIssue",
    "dossier_policy",
    "profile_dossier_issues",
    "profile_dossier_report",
    "require_profile_dossier_quality",
]
