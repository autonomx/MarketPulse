"""Deterministic resolution of named objective claims in generated prose."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

_NAME = r"[A-Z][A-Za-z0-9'’&.-]*(?:\s+(?:(?:of|the|and|de|la)\s+)?[A-Z][A-Za-z0-9'’&.-]*){0,5}"
_CLAIM_PATTERNS = (
    ("founded_by", re.compile(rf"\b(?i:founded by)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("formed_by", re.compile(rf"\b(?i:formed by|established by)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("created_by", re.compile(rf"\b(?i:created by|built by|designed by)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("historical_context", re.compile(rf"\b(?i:during|after|before|since the end of|in the wake of)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("belongs_to", re.compile(rf"\b(?i:belongs to|member of|aligned with|allied with)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("controlled_by", re.compile(rf"\b(?i:controlled by|governed by|operated by)\s+(?:the\s+)?(?P<name>{_NAME})")),
    ("located_in", re.compile(rf"\b(?i:lies within|located in|part of|inside)\s+(?:the\s+)?(?P<name>{_NAME})")),
)
_OBJECTIVE_SECTION_TERMS = {
    "history",
    "historical",
    "origin",
    "origins",
    "backstory",
    "founding",
    "formation",
    "timeline",
    "chronology",
    "overview",
    "operations",
    "function",
    "relationships",
    "geography",
    "location",
}
_SUBJECTIVE_SECTION_TERMS = {
    "rumor",
    "rumour",
    "belief",
    "opinion",
    "quote",
    "voice",
    "secret",
    "sensory",
    "personality",
    "appearance",
}
_SUBJECTIVE_FIELDS = {
    "appearance",
    "personality",
    "speech_style",
    "quote",
    "quotes",
    "secrets",
    "rumors",
    "rumours",
    "beliefs",
    "sensory_profile",
    "short_summary",
    "name",
    "title",
}
_NAMED_SUFFIXES = {
    "war",
    "wars",
    "order",
    "council",
    "corporation",
    "corp",
    "company",
    "institute",
    "academy",
    "syndicate",
    "compact",
    "alliance",
    "union",
    "city",
    "region",
    "district",
    "mountains",
    "reach",
    "network",
    "protocol",
    "accord",
    "treaty",
    "uprising",
    "revolution",
    "crisis",
}


@dataclass(frozen=True)
class ObjectiveNamedClaim:
    subject_id: str
    predicate: str
    mentioned_name: str
    resolution: str
    source_section: str
    assertion_mode: str
    path: str
    resolved_entity_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "mentioned_name": self.mentioned_name,
            "resolution": self.resolution,
            "source_section": self.source_section,
            "assertion_mode": self.assertion_mode,
            "path": self.path,
            "resolved_entity_ids": list(self.resolved_entity_ids),
        }


@dataclass(frozen=True)
class ObjectiveNamedClaimIssue:
    code: str
    claim: ObjectiveNamedClaim
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            **self.claim.as_dict(),
            "message": self.message,
            "severity": "error",
            "blocking": True,
        }


class ObjectiveNamedClaimCompilationError(ValueError):
    def __init__(self, issues: Sequence[ObjectiveNamedClaimIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.claim.subject_id}:{issue.claim.predicate}:"
            f"{issue.claim.mentioned_name}"
            for issue in self.issues
        )
        super().__init__("objective_named_claim_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "objective_named_claim_integrity_failed",
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


def _normalise_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^[\s]*(?:the|a|an)\s+", "", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _entity_aliases(entity: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("name", "title", "short_name", "slug"):
        rendered = str(entity.get(field) or "").strip()
        if rendered:
            values.append(rendered)
    for field in ("aliases", "alternate_names", "aka", "also_known_as"):
        raw = entity.get(field)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return tuple(dict.fromkeys(values))


def _registry(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, set[str]], set[str]]:
    aliases: dict[str, set[str]] = {}
    entity_ids: set[str] = set()
    for raw_topic in topic_rows:
        candidate = _candidate(_mapping(raw_topic))
        for entity in _rows(candidate.get("entities")):
            entity_id = str(entity.get("id") or "").strip()
            if not entity_id:
                continue
            entity_ids.add(entity_id)
            aliases.setdefault(_normalise_name(entity_id), set()).add(entity_id)
            for alias in _entity_aliases(entity):
                normalised = _normalise_name(alias)
                if normalised:
                    aliases.setdefault(normalised, set()).add(entity_id)
        binding = _mapping(_mapping(candidate.get("provenance")).get("entity_manifest_binding"))
        for alias, entity_id in _mapping(binding.get("rewritten_provider_ids")).items():
            rendered_id = str(entity_id or "").strip()
            normalised = _normalise_name(alias)
            if normalised and rendered_id:
                aliases.setdefault(normalised, set()).add(rendered_id)
    return aliases, entity_ids


def _is_likely_named_claim(value: str, known: bool) -> bool:
    if known:
        return True
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    if len(tokens) >= 2:
        return True
    if not tokens:
        return False
    token = tokens[0]
    lowered = token.casefold()
    return (
        token.isupper() and len(token) >= 3
        or any(character.islower() for character in token[1:])
        and any(character.isupper() for character in token[1:])
        or lowered in _NAMED_SUFFIXES
    )


def _objective_section(section: Mapping[str, Any]) -> tuple[bool, str]:
    assertion_mode = str(section.get("assertion_mode") or "").strip().casefold()
    if assertion_mode in {"subjective", "rumor", "rumour", "belief", "opinion"}:
        return False, assertion_mode
    if assertion_mode == "objective":
        return True, assertion_mode
    identity = " ".join(
        (
            str(section.get("id") or ""),
            str(section.get("title") or ""),
        )
    ).casefold()
    tokens = set(re.findall(r"[a-z0-9]+", identity))
    if tokens & _SUBJECTIVE_SECTION_TERMS:
        return False, "subjective"
    return bool(tokens & _OBJECTIVE_SECTION_TERMS), "objective"


def _walk_strings(value: Any, *, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        if value.strip():
            yield path, value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            field = str(key)
            if field in _SUBJECTIVE_FIELDS or field == "dossier":
                continue
            yield from _walk_strings(item, path=f"{path}/{field}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            yield from _walk_strings(item, path=f"{path}/{index}")


def _claim_sources(entity: Mapping[str, Any], *, entity_index: int) -> Iterable[tuple[str, str, str, str]]:
    for path, text in _walk_strings(entity, path=f"/entities/{entity_index}"):
        yield path, text, path.rsplit("/", 1)[-1], "objective"
    dossier = _mapping(entity.get("dossier"))
    for section_index, section in enumerate(_rows(dossier.get("sections"))):
        include, assertion_mode = _objective_section(section)
        if not include:
            continue
        section_id = str(section.get("id") or section.get("title") or f"section:{section_index + 1}")
        paragraphs = section.get("paragraphs")
        if isinstance(paragraphs, str):
            paragraphs = [paragraphs]
        if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)):
            continue
        for paragraph_index, paragraph in enumerate(paragraphs):
            text = str(paragraph or "").strip()
            if text:
                yield (
                    f"/entities/{entity_index}/dossier/sections/{section_index}/paragraphs/{paragraph_index}",
                    text,
                    section_id,
                    assertion_mode or "objective",
                )


def _extract_claims(
    text: str,
    *,
    subject_id: str,
    path: str,
    source_section: str,
    assertion_mode: str,
    aliases: Mapping[str, set[str]],
) -> tuple[ObjectiveNamedClaim, ...]:
    claims: list[ObjectiveNamedClaim] = []
    for predicate, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            mentioned = str(match.group("name") or "").strip(" .,:;!?()[]{}\"'")
            normalised = _normalise_name(mentioned)
            resolved = tuple(sorted(aliases.get(normalised, set())))
            if not _is_likely_named_claim(mentioned, bool(resolved)):
                continue
            resolution = "resolved" if len(resolved) == 1 else "ambiguous" if resolved else "unresolved"
            claims.append(
                ObjectiveNamedClaim(
                    subject_id=subject_id,
                    predicate=predicate,
                    mentioned_name=mentioned,
                    resolution=resolution,
                    source_section=source_section,
                    assertion_mode=assertion_mode,
                    path=path,
                    resolved_entity_ids=resolved,
                )
            )
    unique = {
        (
            claim.subject_id,
            claim.predicate,
            claim.mentioned_name,
            claim.path,
        ): claim
        for claim in claims
    }
    return tuple(unique[key] for key in sorted(unique))


def objective_named_claims(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[ObjectiveNamedClaim, ...]:
    aliases, _entity_ids = _registry(topic_rows)
    claims: list[ObjectiveNamedClaim] = []
    for raw_topic in topic_rows:
        candidate = _candidate(_mapping(raw_topic))
        for entity_index, entity in enumerate(_rows(candidate.get("entities"))):
            subject_id = str(entity.get("id") or f"entity:{entity_index + 1}")
            for path, text, section, assertion_mode in _claim_sources(
                entity,
                entity_index=entity_index,
            ):
                claims.extend(
                    _extract_claims(
                        text,
                        subject_id=subject_id,
                        path=path,
                        source_section=section,
                        assertion_mode=assertion_mode,
                        aliases=aliases,
                    )
                )
    unique = {
        (
            claim.subject_id,
            claim.predicate,
            claim.mentioned_name,
            claim.path,
        ): claim
        for claim in claims
    }
    return tuple(unique[key] for key in sorted(unique))


def objective_named_claim_issues(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[ObjectiveNamedClaimIssue, ...]:
    issues: list[ObjectiveNamedClaimIssue] = []
    for claim in objective_named_claims(topic_rows):
        if claim.resolution == "unresolved":
            issues.append(
                ObjectiveNamedClaimIssue(
                    code="unresolved_objective_named_claim",
                    claim=claim,
                    message=(
                        "Objective prose names canon that does not resolve to a registered "
                        "entity or alias."
                    ),
                )
            )
        elif claim.resolution == "ambiguous":
            issues.append(
                ObjectiveNamedClaimIssue(
                    code="ambiguous_objective_named_claim",
                    claim=claim,
                    message=(
                        "Objective prose name resolves to multiple canonical entities and "
                        "must be disambiguated structurally."
                    ),
                )
            )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.code,
                issue.claim.subject_id,
                issue.claim.path,
                issue.claim.mentioned_name,
            ),
        )
    )


def objective_named_claim_report(
    topic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    claims = objective_named_claims(topic_rows)
    issues = objective_named_claim_issues(topic_rows)
    return {
        "schema_version": "rpg_world_objective_named_claims_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "claims": [claim.as_dict() for claim in claims],
        "checks": {
            "claim_count": len(claims),
            "resolved_claim_count": sum(
                1 for claim in claims if claim.resolution == "resolved"
            ),
            "unresolved_claim_count": sum(
                1 for claim in claims if claim.resolution == "unresolved"
            ),
            "ambiguous_claim_count": sum(
                1 for claim in claims if claim.resolution == "ambiguous"
            ),
        },
    }


def require_resolved_objective_named_claims(
    topic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    issues = objective_named_claim_issues(topic_rows)
    if issues:
        raise ObjectiveNamedClaimCompilationError(issues)
    return objective_named_claim_report(topic_rows)


__all__ = [
    "ObjectiveNamedClaim",
    "ObjectiveNamedClaimCompilationError",
    "ObjectiveNamedClaimIssue",
    "objective_named_claim_issues",
    "objective_named_claim_report",
    "objective_named_claims",
    "require_resolved_objective_named_claims",
]
