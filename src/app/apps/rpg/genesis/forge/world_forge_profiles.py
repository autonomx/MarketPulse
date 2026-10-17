"""Typed, deterministic ontology contracts for genre-neutral World Forge graphs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

FieldValueType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "enum",
    "entity_ref",
    "entity_ref_list",
    "structured_object",
]

_ALLOWED_FIELD_TYPES: frozenset[str] = frozenset(
    {
        "string",
        "integer",
        "number",
        "boolean",
        "enum",
        "entity_ref",
        "entity_ref_list",
        "structured_object",
    }
)
_ALLOWED_PAGE_KINDS = {"document", "collection"}
_ALLOWED_IMAGE_ROLES = {
    "none",
    "portrait",
    "scene",
    "landscape",
    "emblem",
    "icon",
    "illustration",
    "cover",
    "map",
}
_ALLOWED_AUTHORING_GROUPS = {"world", "lore", "game-master"}


@dataclass(frozen=True)
class ProfileValidationIssue:
    code: str
    item_id: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "item_id": self.item_id, "message": self.message}


class GenreProfileValidationError(ValueError):
    def __init__(self, issues: tuple[ProfileValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__(
            "genre_profile_invalid:"
            + ";".join(f"{issue.code}:{issue.item_id}" for issue in issues)
        )


@dataclass(frozen=True)
class FieldDefinition:
    field_id: str
    value_type: FieldValueType
    required: bool = False
    semantic_role: str = ""
    allowed_target_domains: tuple[str, ...] = ()
    enum_values: tuple[str, ...] = ()
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "value_type": self.value_type,
            "required": self.required,
            "semantic_role": self.semantic_role,
            "allowed_target_domains": list(self.allowed_target_domains),
            "enum_values": list(self.enum_values),
            "description": self.description,
        }


@dataclass(frozen=True)
class DomainTargetRange:
    quick: tuple[int, int] = (1, 1)
    standard: tuple[int, int] = (1, 1)
    epic: tuple[int, int] = (1, 1)

    def target(self, depth: str) -> int:
        lower, upper = {
            "quick": self.quick,
            "standard": self.standard,
            "epic": self.epic,
        }.get(str(depth).casefold(), self.standard)
        return lower if str(depth).casefold() == "quick" else round((lower + upper) / 2)

    def as_dict(self) -> dict[str, list[int]]:
        return {
            "quick": list(self.quick),
            "standard": list(self.standard),
            "epic": list(self.epic),
        }

    def validate(self, item_id: str) -> tuple[ProfileValidationIssue, ...]:
        issues: list[ProfileValidationIssue] = []
        for name, value in (
            ("quick", self.quick),
            ("standard", self.standard),
            ("epic", self.epic),
        ):
            if len(value) != 2:
                issues.append(
                    ProfileValidationIssue(
                        "invalid_target_range",
                        f"{item_id}.{name}",
                        "Target range must contain a minimum and maximum.",
                    )
                )
                continue
            lower, upper = value
            if lower < 1 or upper < lower or upper > 50:
                issues.append(
                    ProfileValidationIssue(
                        "invalid_target_range",
                        f"{item_id}.{name}",
                        "Target range must satisfy 1 <= minimum <= maximum <= 50.",
                    )
                )
        return tuple(issues)


@dataclass(frozen=True)
class DomainDefinition:
    domain_id: str
    title: str
    entity_kind: str
    dependencies: tuple[str, ...] = ()
    generator_role: str = "world_forge"
    required_before_launch: bool = False
    visibility_default: str = "game_master_canon"
    fields: tuple[FieldDefinition, ...] = ()
    target_range: DomainTargetRange = field(default_factory=DomainTargetRange)
    semantic_roles: tuple[str, ...] = ()
    category: str = "domain"
    generation_guidance: Mapping[str, Any] = field(default_factory=dict)

    def field_map(self) -> dict[str, FieldDefinition]:
        return {value.field_id: value for value in self.fields}

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "title": self.title,
            "entity_kind": self.entity_kind,
            "dependencies": list(self.dependencies),
            "generator_role": self.generator_role,
            "required_before_launch": self.required_before_launch,
            "visibility_default": self.visibility_default,
            "fields": [value.as_dict() for value in self.fields],
            "target_range": self.target_range.as_dict(),
            "semantic_roles": list(self.semantic_roles),
            "category": self.category,
            "generation_guidance": dict(self.generation_guidance),
        }


@dataclass(frozen=True)
class LaunchRequirements:
    required_domain_ids: tuple[str, ...] = ()
    required_semantic_roles: tuple[str, ...] = (
        "starting_context",
        "initial_actors",
        "initial_conflict",
    )

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "required_domain_ids": list(self.required_domain_ids),
            "required_semantic_roles": list(self.required_semantic_roles),
        }


@dataclass(frozen=True)
class RuntimeCapabilityDefaults:
    values: Mapping[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, bool]:
        return {str(key): bool(value) for key, value in sorted(self.values.items())}


@dataclass(frozen=True)
class GenreProfile:
    profile_id: str
    version: int
    display_name: str
    domains: tuple[DomainDefinition, ...]
    aliases: tuple[str, ...] = ()
    parent_profile_ids: tuple[str, ...] = ()
    modifier_ids: tuple[str, ...] = ()
    genre_tags: tuple[str, ...] = ()
    launch_requirements: LaunchRequirements = field(default_factory=LaunchRequirements)
    runtime_capability_defaults: RuntimeCapabilityDefaults = field(
        default_factory=RuntimeCapabilityDefaults
    )
    provenance: Mapping[str, Any] = field(default_factory=dict)
    scope: Literal["built_in", "registry", "world_local"] = "world_local"

    def domain_map(self) -> dict[str, DomainDefinition]:
        return {domain.domain_id: domain for domain in self.domains}

    def as_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "profile_id": self.profile_id,
            "version": self.version,
            "display_name": self.display_name,
            "domains": [domain.as_dict() for domain in self.domains],
            "aliases": list(self.aliases),
            "parent_profile_ids": list(self.parent_profile_ids),
            "modifier_ids": list(self.modifier_ids),
            "genre_tags": list(self.genre_tags),
            "launch_requirements": self.launch_requirements.as_dict(),
            "runtime_capability_defaults": self.runtime_capability_defaults.as_dict(),
            "provenance": dict(self.provenance),
            "scope": self.scope,
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(
            self.as_dict(include_hash=False),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def validate(self) -> tuple[ProfileValidationIssue, ...]:
        issues: list[ProfileValidationIssue] = []
        domain_map = self.domain_map()
        if not self.profile_id.strip():
            issues.append(ProfileValidationIssue("missing_profile_id", "profile", "Profile ID is required."))
        if self.version < 1:
            issues.append(ProfileValidationIssue("invalid_profile_version", self.profile_id, "Version must be positive."))
        if len(domain_map) != len(self.domains):
            issues.append(ProfileValidationIssue("duplicate_domain_id", self.profile_id, "Domain IDs must be unique."))

        semantic_roles: set[str] = set()
        for domain in self.domains:
            semantic_roles.update(domain.semantic_roles)
            if not domain.domain_id or not domain.entity_kind:
                issues.append(ProfileValidationIssue("invalid_domain_identity", domain.domain_id, "Domain and entity kind are required."))
            issues.extend(domain.target_range.validate(domain.domain_id))
            for dependency in domain.dependencies:
                if dependency not in domain_map:
                    issues.append(ProfileValidationIssue("unknown_domain_dependency", domain.domain_id, dependency))
            field_map = domain.field_map()
            if len(field_map) != len(domain.fields):
                issues.append(ProfileValidationIssue("duplicate_field_id", domain.domain_id, "Field IDs must be unique per domain."))
            for definition in domain.fields:
                if definition.value_type not in _ALLOWED_FIELD_TYPES:
                    issues.append(ProfileValidationIssue("unsupported_field_type", f"{domain.domain_id}.{definition.field_id}", definition.value_type))
                if definition.value_type == "enum" and not definition.enum_values:
                    issues.append(ProfileValidationIssue("enum_values_required", f"{domain.domain_id}.{definition.field_id}", "Enum fields require values."))
                if definition.value_type in {"entity_ref", "entity_ref_list"}:
                    if not definition.allowed_target_domains:
                        issues.append(ProfileValidationIssue("reference_targets_required", f"{domain.domain_id}.{definition.field_id}", "Reference fields require target domains."))
                    for target in definition.allowed_target_domains:
                        if target not in domain_map:
                            issues.append(ProfileValidationIssue("unknown_reference_target", f"{domain.domain_id}.{definition.field_id}", target))

            presentation = domain.generation_guidance.get("presentation")
            if presentation is not None:
                if not isinstance(presentation, Mapping):
                    issues.append(ProfileValidationIssue("invalid_presentation", domain.domain_id, "Presentation must be an object."))
                else:
                    page_kind = str(presentation.get("page_kind") or "")
                    image_role = str(presentation.get("image_role") or "")
                    group = str(presentation.get("group") or "")
                    card_variant = str(presentation.get("card_variant") or "")
                    if page_kind not in _ALLOWED_PAGE_KINDS:
                        issues.append(ProfileValidationIssue("unsupported_page_kind", domain.domain_id, page_kind))
                    if image_role not in _ALLOWED_IMAGE_ROLES:
                        issues.append(ProfileValidationIssue("unsupported_image_role", domain.domain_id, image_role))
                    if group not in _ALLOWED_AUTHORING_GROUPS:
                        issues.append(ProfileValidationIssue("unsupported_authoring_group", domain.domain_id, group))
                    if page_kind == "collection" and not card_variant:
                        issues.append(ProfileValidationIssue("card_variant_required", domain.domain_id, "Collection domains require a card variant."))

        for required_domain in self.launch_requirements.required_domain_ids:
            if required_domain not in domain_map:
                issues.append(ProfileValidationIssue("unknown_launch_domain", required_domain, "Launch requirement is not a profile domain."))
        for role in self.launch_requirements.required_semantic_roles:
            if role not in semantic_roles:
                issues.append(ProfileValidationIssue("missing_launch_semantic_role", role, "No profile domain provides this launch role."))

        pending = {domain.domain_id: set(domain.dependencies) for domain in self.domains}
        while pending:
            ready = {domain_id for domain_id, dependencies in pending.items() if not dependencies}
            if not ready:
                issues.append(ProfileValidationIssue("domain_dependency_cycle", self.profile_id, "Domain dependencies contain a cycle."))
                break
            for domain_id in ready:
                pending.pop(domain_id)
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        return tuple(issues)

    def require_valid(self) -> "GenreProfile":
        issues = self.validate()
        if issues:
            raise GenreProfileValidationError(issues)
        return self


def field_definition_from_dict(value: Mapping[str, Any]) -> FieldDefinition:
    return FieldDefinition(
        field_id=str(value.get("field_id") or ""),
        value_type=str(value.get("value_type") or "string"),  # type: ignore[arg-type]
        required=bool(value.get("required", False)),
        semantic_role=str(value.get("semantic_role") or ""),
        allowed_target_domains=tuple(str(item) for item in value.get("allowed_target_domains") or ()),
        enum_values=tuple(str(item) for item in value.get("enum_values") or ()),
        description=str(value.get("description") or ""),
    )


def _range(value: Any, fallback: tuple[int, int]) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)):
        return fallback
    try:
        return tuple(int(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return cast(Any, ())


def domain_definition_from_dict(value: Mapping[str, Any]) -> DomainDefinition:
    ranges = dict(value.get("target_range") or {})
    return DomainDefinition(
        domain_id=str(value.get("domain_id") or ""),
        title=str(value.get("title") or value.get("domain_id") or ""),
        entity_kind=str(value.get("entity_kind") or ""),
        dependencies=tuple(str(item) for item in value.get("dependencies") or ()),
        generator_role=str(value.get("generator_role") or "world_forge"),
        required_before_launch=bool(value.get("required_before_launch", False)),
        visibility_default=str(value.get("visibility_default") or "game_master_canon"),
        fields=tuple(field_definition_from_dict(item) for item in value.get("fields") or () if isinstance(item, Mapping)),
        target_range=DomainTargetRange(
            quick=_range(ranges.get("quick"), (1, 1)),
            standard=_range(ranges.get("standard"), (1, 1)),
            epic=_range(ranges.get("epic"), (1, 1)),
        ),
        semantic_roles=tuple(str(item) for item in value.get("semantic_roles") or ()),
        category=str(value.get("category") or "domain"),
        generation_guidance=dict(value.get("generation_guidance") or {}),
    )


def genre_profile_from_dict(value: Mapping[str, Any]) -> GenreProfile:
    launch = dict(value.get("launch_requirements") or {})
    return GenreProfile(
        profile_id=str(value.get("profile_id") or ""),
        version=int(value.get("version") or 1),
        display_name=str(value.get("display_name") or value.get("profile_id") or ""),
        domains=tuple(domain_definition_from_dict(item) for item in value.get("domains") or () if isinstance(item, Mapping)),
        aliases=tuple(str(item) for item in value.get("aliases") or ()),
        parent_profile_ids=tuple(str(item) for item in value.get("parent_profile_ids") or ()),
        modifier_ids=tuple(str(item) for item in value.get("modifier_ids") or ()),
        genre_tags=tuple(str(item) for item in value.get("genre_tags") or ()),
        launch_requirements=LaunchRequirements(
            required_domain_ids=tuple(str(item) for item in launch.get("required_domain_ids") or ()),
            required_semantic_roles=tuple(str(item) for item in launch.get("required_semantic_roles") or ("starting_context", "initial_actors", "initial_conflict")),
        ),
        runtime_capability_defaults=RuntimeCapabilityDefaults(dict(value.get("runtime_capability_defaults") or {})),
        provenance=dict(value.get("provenance") or {}),
        scope=str(value.get("scope") or "world_local"),  # type: ignore[arg-type]
    )
