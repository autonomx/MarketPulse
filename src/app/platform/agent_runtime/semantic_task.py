"""Semantic task contract and deterministic compiler for generalized Agent routing.

The LLM may describe user intent and references. Omnix owns all derivation of
lanes, profiles, capabilities, evidence policy, and ambiguity handling.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import (
    EvidenceCoverage,
    EvidenceDecision,
    EvidencePolicy,
    EvidenceRequirement,
    EvidenceSourceOption,
    RetrievalPolicy,
    SubjectRef,
)
from .evidence import (
    evidence_coverage_from_subject,
    freshness_max_age_seconds,
    merge_evidence_requirements,
    resolve_subject,
)


SemanticTarget = Literal[
    "conversation",
    "workspace",
    "repository",
    "repository_ci",
    "operations",
    "home",
    "home_energy",
    "email",
    "calendar",
    "contacts",
    "market",
    "market_quote",
    "market_filing",
    "market_status",
    "weather",
    "software_release",
    "public_web",
]
SemanticOperationKind = Literal[
    "read",
    "inspect",
    "modify",
    "execute",
    "validate",
    "send",
    "draft",
    "create",
    "research",
    "compare",
    "explain",
    "compose",
]
SemanticAmbiguity = Literal[
    "none",
    "resolvable_from_context",
    "clarification_required",
]
SemanticObjectiveRelation = Literal[
    "none",
    "continue",
    "resume",
    "revise",
]
SemanticRetrievalMode = Literal[
    "unspecified",
    "lookup",
    "verify",
    "filter",
    "discover",
]
SemanticWorkspaceSurface = Literal[
    "web_ui",
    "api",
    "cli",
    "backend",
    "data",
    "configuration",
]


class SemanticSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: SemanticTarget
    reference: str = Field(min_length=1, max_length=240)
    kind: str | None = Field(default=None, max_length=80)


class SemanticOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SemanticOperationKind
    target: SemanticTarget
    subject_reference: str | None = Field(default=None, max_length=240)


class SemanticDataDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: SemanticTarget
    freshness: Literal["timeless", "current", "as_of_date"] = "current"
    as_of_date: datetime | None = None
    subject_reference: str | None = Field(default=None, max_length=240)
    required: bool = True
    # Retrieval shape is semantic, not authority.  The deterministic scheduler
    # uses it to distinguish bounded reads from open-ended discovery without
    # relying on fuzzy multi_step/autonomous flags.
    retrieval_mode: SemanticRetrievalMode = "unspecified"

    @model_validator(mode="after")
    def validate_temporal_identity(self) -> "SemanticDataDependency":
        if self.freshness == "as_of_date" and self.as_of_date is None:
            raise ValueError("as_of_date freshness requires as_of_date")
        if self.freshness != "as_of_date" and self.as_of_date is not None:
            raise ValueError("as_of_date is only valid with as_of_date freshness")
        return self


class SemanticTask(BaseModel):
    """Untrusted semantic description of the user's requested task.

    This model deliberately contains no lane, profile, capability, evidence
    source class, trust floor, or fallback-policy fields. Those are Omnix policy.
    Workspace surfaces describe meaning only; they never grant authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str = Field(min_length=1, max_length=160)
    subjects: list[SemanticSubject] = Field(default_factory=list, max_length=12)
    operations: list[SemanticOperation] = Field(default_factory=list, max_length=16)
    data_dependencies: list[SemanticDataDependency] = Field(default_factory=list, max_length=12)
    workspace_surfaces: list[SemanticWorkspaceSurface] = Field(default_factory=list, max_length=6)
    autonomous: bool = False
    multi_step: bool = False
    objective_relation: SemanticObjectiveRelation = "none"
    request_completeness: Literal["self_contained", "context_dependent"] = "self_contained"
    replay_target: Literal["latest_authoritative", "base_objective"] = "latest_authoritative"
    ambiguity: SemanticAmbiguity = "none"
    candidate_interpretations: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    reason_code: str = Field(default="semantic_task", min_length=1, max_length=96)

    @field_validator("intent", mode="before")
    @classmethod
    def bound_descriptive_intent(cls, value: Any) -> Any:
        """Keep non-authoritative intent prose from invalidating a valid task.

        The structured provider can occasionally exceed JSON-schema maxLength
        even after correction. Intent is descriptive only: deterministic policy
        derives every lane/profile/action/evidence decision from typed fields,
        so truncating this prose cannot grant or widen authority.
        """

        if isinstance(value, str):
            return value.strip()[:160]
        return value

    @model_validator(mode="after")
    def normalize_ambiguity(self) -> "SemanticTask":
        if self.ambiguity == "none" and self.candidate_interpretations:
            return self.model_copy(update={"candidate_interpretations": []})
        return self


class SemanticCompilerAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    detail: str
    rejected_operation: str | None = None


class SemanticTaskCompilation(BaseModel):
    """Deterministic execution interpretation derived from SemanticTask."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lane: Literal["chat", "agent"]
    profile_id: Literal[
        "coding",
        "house",
        "research",
        "personal-assistant",
        "ops",
        "trading-research",
    ] | None = None
    action_intents: list[str] = Field(default_factory=list)
    workspace_surfaces: list[SemanticWorkspaceSurface] = Field(default_factory=list)
    evidence_decision: EvidenceDecision = Field(default_factory=EvidenceDecision)
    ambiguity: SemanticAmbiguity = "none"
    requires_clarification: bool = False
    reason_code: str = "semantic_task"
    anomalies: list[SemanticCompilerAnomaly] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    retrieval_modes: list[SemanticRetrievalMode] = Field(default_factory=list)
    multi_step: bool = False
    autonomous: bool = False


_OPERATION_ACTIONS: dict[tuple[str, str], str] = {
    ("read", "workspace"): "workspace_read",
    ("inspect", "workspace"): "workspace_read",
    ("read", "repository"): "workspace_read",
    ("inspect", "repository"): "workspace_read",
    # Remote CI inspection is external read authority, not permission to run
    # arbitrary local commands.  Local workspace execution is granted only by
    # an explicit workspace/repository execute/validate operation.
    ("read", "repository_ci"): "repo_ci_read",
    ("inspect", "repository_ci"): "repo_ci_read",
    ("research", "repository_ci"): "repo_ci_read",
    ("compare", "repository_ci"): "repo_ci_read",
    ("validate", "repository_ci"): "repo_ci_read",
    ("modify", "workspace"): "workspace_mutate",
    ("modify", "repository"): "workspace_mutate",
    ("create", "workspace"): "workspace_mutate",
    ("create", "repository"): "workspace_mutate",
    ("execute", "workspace"): "workspace_execute",
    ("execute", "repository"): "workspace_execute",
    ("validate", "workspace"): "workspace_execute",
    ("validate", "repository"): "workspace_execute",
    ("compare", "workspace"): "workspace_read",
    ("compare", "repository"): "workspace_read",
    ("read", "operations"): "ops_read",
    ("inspect", "operations"): "ops_read",
    ("execute", "operations"): "ops_execute",
    ("validate", "operations"): "ops_execute",
    ("read", "home"): "home_read",
    ("inspect", "home"): "home_read",
    ("modify", "home"): "home_mutate",
    ("execute", "home"): "home_mutate",
    ("validate", "home"): "home_read",
    ("compare", "home"): "home_read",
    ("read", "home_energy"): "home_read",
    ("inspect", "home_energy"): "home_read",
    ("research", "home_energy"): "home_read",
    ("validate", "home_energy"): "home_read",
    ("compare", "home_energy"): "home_read",
    ("read", "email"): "email_read",
    ("inspect", "email"): "email_read",
    ("draft", "email"): "email_draft",
    ("compose", "email"): "email_draft",
    ("send", "email"): "email_send",
    ("read", "calendar"): "calendar_read",
    ("inspect", "calendar"): "calendar_read",
    ("create", "calendar"): "calendar_create",
    ("modify", "calendar"): "calendar_create",
    ("read", "contacts"): "contacts_read",
    ("inspect", "contacts"): "contacts_read",
    ("read", "market"): "market_read",
    ("inspect", "market"): "market_read",
    ("research", "market"): "market_read",
    ("compare", "market"): "market_read",
    ("read", "market_quote"): "market_read",
    ("inspect", "market_quote"): "market_read",
    ("research", "market_quote"): "market_read",
    ("compare", "market_quote"): "market_read",
    ("read", "market_filing"): "market_read",
    ("research", "market_filing"): "market_read",
    ("compare", "market_filing"): "market_read",
    ("inspect", "market_filing"): "market_read",
    ("read", "market_status"): "market_read",
    ("inspect", "market_status"): "market_read",
    ("research", "market_status"): "market_read",
    ("compare", "market_status"): "market_read",
    ("validate", "market"): "market_read",
    ("validate", "market_quote"): "market_read",
    ("validate", "market_filing"): "market_read",
    ("validate", "market_status"): "market_read",
    ("read", "weather"): "research_read",
    ("inspect", "weather"): "research_read",
    ("research", "weather"): "research_read",
    ("compare", "weather"): "research_read",
    ("read", "software_release"): "research_read",
    ("inspect", "software_release"): "research_read",
    ("research", "software_release"): "research_read",
    ("compare", "software_release"): "research_read",
    ("read", "public_web"): "research_read",
    ("inspect", "public_web"): "research_read",
    ("research", "public_web"): "research_read",
    ("compare", "public_web"): "research_read",
    ("validate", "weather"): "research_read",
    ("validate", "software_release"): "research_read",
    ("validate", "public_web"): "research_read",
}


_ACTION_PROFILES: dict[str, str] = {
    "repo_ci_read": "coding",
    "workspace_read": "coding",
    "workspace_execute": "coding",
    "workspace_mutate": "coding",
    "ops_read": "ops",
    "ops_execute": "ops",
    "home_read": "house",
    "home_mutate": "house",
    "email_read": "personal-assistant",
    "email_draft": "personal-assistant",
    "email_send": "personal-assistant",
    "calendar_read": "personal-assistant",
    "calendar_create": "personal-assistant",
    "contacts_read": "personal-assistant",
    "market_read": "trading-research",
    "research_read": "research",
}

_SUBJECT_PROFILES: dict[str, str] = {
    "workspace": "coding",
    "repository": "coding",
    "repository_ci": "coding",
    "operations": "ops",
    "home": "house",
    "home_energy": "house",
    "email": "personal-assistant",
    "calendar": "personal-assistant",
    "contacts": "personal-assistant",
    "market": "trading-research",
    "market_quote": "trading-research",
    "market_filing": "trading-research",
    "market_status": "trading-research",
    "weather": "research",
    "software_release": "research",
    "public_web": "research",
}


# Evidence policy is runtime policy, not LLM output.
# target -> (source_class, trust_floor, fallback_policy)
_EVIDENCE_POLICY: dict[str, tuple[str, str, str]] = {
    "repository": ("repo_contents", "authoritative", "fail_closed"),
    "repository_ci": ("repo_ci_state", "authoritative", "fail_closed"),
    "home": ("home_state", "authoritative", "fail_closed"),
    "home_energy": ("home_energy", "authoritative", "fail_closed"),
    "email": ("email_state", "authoritative", "fail_closed"),
    "calendar": ("calendar_state", "authoritative", "fail_closed"),
    "market": ("market_news", "reputable", "allow_fallback"),
    "market_quote": ("market_quote", "authoritative", "fail_closed"),
    "market_filing": ("company_filing", "primary", "fail_closed"),
    "market_status": ("market_status", "authoritative", "fail_closed"),
    "weather": ("weather_state", "authoritative", "fail_closed"),
    "software_release": ("software_release", "primary", "allow_fallback"),
    "public_web": ("general_current_web", "reputable", "allow_fallback"),
}


def semantic_task_profile_ids(task: SemanticTask) -> tuple[str, ...]:
    """Return deterministic profile domains named by semantic task facts.

    This is descriptive only: it grants no capabilities. TurnPlan uses the
    domains to detect when a continuation crosses the active executor boundary
    even if the LLM describes only the newly-added portion of the objective.
    """

    profiles: set[str] = set()
    for operation in task.operations:
        action = _OPERATION_ACTIONS.get((operation.kind, operation.target))
        profile = _ACTION_PROFILES.get(action or "")
        if profile is not None:
            profiles.add(profile)
    for dependency in task.data_dependencies:
        if not dependency.required:
            continue
        profile = _SUBJECT_PROFILES.get(dependency.target)
        if profile is not None:
            profiles.add(profile)
    for subject in task.subjects:
        profile = _SUBJECT_PROFILES.get(subject.target)
        if profile is not None:
            profiles.add(profile)

    # Match the compiler's deliberate market+public-web specialization.
    if profiles == {"research", "trading-research"}:
        return ("trading-research",)
    return tuple(sorted(profiles))


_PUBLIC_READ_TARGETS = {
    "market",
    "market_quote",
    "market_filing",
    "market_status",
    "weather",
    "software_release",
    "public_web",
}


def _reference_for_target(task: SemanticTask, target: str) -> str | None:
    """Resolve a parser-supplied subject reference without making it authority."""

    for dependency in task.data_dependencies:
        if dependency.target == target and dependency.subject_reference:
            return dependency.subject_reference.strip() or None
    for operation in task.operations:
        if operation.target == target and operation.subject_reference:
            return operation.subject_reference.strip() or None
    for subject in task.subjects:
        if subject.target == target and subject.reference:
            return subject.reference.strip() or None
    return None


def _explicit_location_subject(reference: str) -> SubjectRef | None:
    clean = " ".join(str(reference or "").split()).strip(" .!?")
    if not clean:
        return None
    canonical = clean.casefold()
    return SubjectRef(
        type="location",
        canonical_id=canonical,
        display_name=clean,
    )


def _coverage_for_dependency(
    source_class: str,
    subject: SubjectRef | None,
    reference: str | None,
    dependency: SemanticDataDependency,
) -> EvidenceCoverage | None:
    subject_coverage = evidence_coverage_from_subject(subject)
    if subject_coverage is not None:
        return subject_coverage

    clean = " ".join(str(reference or "").split()).strip()
    if not clean:
        return None
    normalized = re.sub(r"[^a-z0-9._:/+-]+", "-", clean.casefold()).strip("-")
    if source_class == "software_release" and normalized:
        return EvidenceCoverage(
            kind="software_package",
            coverage_key=f"software_package:{normalized}",
        )

    return EvidenceCoverage(
        kind="semantic_dependency",
        coverage_key=(
            f"semantic_dependency:{dependency.target}:{normalized}"
        )[:320],
    )


def _requirement_trace_id(
    source_class: str,
    dependency: SemanticDataDependency,
) -> str:
    digest = hashlib.sha256(
        dependency.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()[:12]
    return f"semantic-task-{source_class}-{digest}"


def _evidence_requirement(
    latest_user_message: str,
    dependency: SemanticDataDependency,
    task: SemanticTask,
) -> EvidenceRequirement | None:
    policy = _EVIDENCE_POLICY.get(dependency.target)
    if policy is None or not dependency.required:
        return None
    source_class, trust_floor, fallback_policy = policy
    freshness = dependency.freshness
    reference = dependency.subject_reference or _reference_for_target(task, dependency.target)
    if source_class == "weather_state":
        location_reference = dependency.subject_reference
        if not location_reference:
            location_reference = next(
                (
                    subject.reference
                    for subject in task.subjects
                    if subject.target == dependency.target
                    and str(subject.kind or "").casefold()
                    in {"location", "place", "city", "region"}
                ),
                None,
            )
        subject = (
            _explicit_location_subject(location_reference)
            if location_reference
            else resolve_subject(reference or latest_user_message, source_class)
        )
    else:
        subject = resolve_subject(reference or latest_user_message, source_class)
        if (
            subject is None
            and reference
            and source_class in {"market_quote", "market_news", "company_filing"}
        ):
            subject = resolve_subject(f"stock {reference}", source_class)

    coverage = _coverage_for_dependency(source_class, subject, reference, dependency)
    return EvidenceRequirement(
        id=_requirement_trace_id(source_class, dependency),
        source_class=source_class,
        subject=subject,
        coverage=coverage,
        freshness=freshness,
        trust_floor=cast(Any, trust_floor),
        acceptable_sources=[
            EvidenceSourceOption(
                source_class=source_class,
                trust_floor=cast(Any, trust_floor),
                preference=0,
            )
        ],
        fallback_policy=cast(Any, fallback_policy),
        as_of_date=dependency.as_of_date,
        max_age_seconds=(
            freshness_max_age_seconds(source_class)
            if freshness == "current"
            else None
        ),
    )


def _profile_for_actions(actions: list[str]) -> tuple[str | None, list[SemanticCompilerAnomaly]]:
    profiles = {
        _ACTION_PROFILES[action]
        for action in actions
        if action in _ACTION_PROFILES
    }
    if not profiles:
        return None, []
    if len(profiles) == 1:
        return next(iter(profiles)), []
    # Market research is a specialization of public research. The trading
    # research profile already contains governed web-search authority, so a
    # market task that also needs public-web research is one profile, not a
    # cross-domain composite.
    if profiles == {"research", "trading-research"}:
        return "trading-research", []
    ordered = sorted(profiles)
    return None, [
        SemanticCompilerAnomaly(
            code="unsupported_composite_profiles",
            detail="semantic task spans profiles: " + ", ".join(ordered),
        )
    ]


def _has_stateful_actions(actions: list[str]) -> bool:
    return any(
        action == "repo_ci_read"
        or action.startswith(("workspace_", "ops_", "home_", "email_", "calendar_", "contacts_"))
        for action in actions
    )


def _retrieval_modes(
    task: SemanticTask,
    dependencies: list[SemanticDataDependency],
) -> list[SemanticRetrievalMode]:
    """Return the canonical retrieval shape used by the execution scheduler.

    New SemanticTask parsers describe retrieval shape explicitly.  The fallback
    below exists only for compatibility with older injected/test SemanticTasks;
    production parser output is expected to set retrieval_mode for every
    external dependency.
    """

    external = [
        dependency
        for dependency in dependencies
        if dependency.required and dependency.target in _PUBLIC_READ_TARGETS
    ]
    explicit = [
        dependency.retrieval_mode
        for dependency in external
        if dependency.retrieval_mode != "unspecified"
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    if not external:
        return []

    # Compatibility only: old v2 tasks represented discovery as research/compare
    # plus multi_step.  Do not use autonomous by itself because one bounded
    # lookup may still be performed autonomously.
    has_open_ended_operation = any(
        operation.target in _PUBLIC_READ_TARGETS
        and operation.kind in {"research", "compare"}
        for operation in task.operations
    )
    if task.multi_step and has_open_ended_operation:
        return ["discover"]
    return ["lookup"]


def compile_semantic_task(
    latest_user_message: str,
    task: SemanticTask,
    *,
    routing_environment: Any | None = None,
) -> SemanticTaskCompilation:
    """Compile semantic facts into an execution domain without granting authority."""

    actions, anomalies = _compiled_actions(task)

    profile_id, profile_anomalies = _profile_for_actions(actions)
    anomalies.extend(profile_anomalies)

    _check_subject_domains(task, actions, anomalies)

    dependencies, profile_id = _dependency_profile(task, profile_id, anomalies)

    # Some private/stateful reads inherently depend on the current private state.
    # Mutation does not automatically imply inbox/calendar reads; the parser must
    # state those dependencies when the task actually depends on them.
    _add_implicit_dependencies(task, actions, dependencies)

    requirements = _compiled_requirements(dependencies, latest_user_message, task, anomalies)

    # A selected Local folder is authoritative for local repository contents.
    # Local environment state does not grant an action, but once the semantic
    # task already asks for workspace work it prevents a redundant external
    # github.read_repo evidence grant. Remote CI remains external/current.
    requirements = _without_local_repository_evidence(routing_environment, actions, requirements)

    evidence_decision = _compiled_evidence_decision(latest_user_message, requirements, task)

    lane, profile_id, requires_clarification, retrieval_modes = _compiled_lane(dependencies, task, actions, anomalies, profile_id)

    # Existing execution callers already transport action_intents into the
    # deterministic authority compiler. Preserve the dedicated typed surface
    # field as canonical while carrying a namespaced compatibility marker until
    # every caller can pass semantic_workspace_surfaces explicitly. The marker
    # is ignored by lane/profile derivation above and grants nothing by itself.
    authority_actions = list(actions)
    authority_actions.extend(
        f"workspace_surface:{surface}"
        for surface in dict.fromkeys(task.workspace_surfaces)
    )

    return SemanticTaskCompilation(
        lane=lane,
        profile_id=profile_id,
        action_intents=authority_actions,
        workspace_surfaces=list(dict.fromkeys(task.workspace_surfaces)),
        evidence_decision=evidence_decision,
        ambiguity=task.ambiguity,
        requires_clarification=requires_clarification,
        reason_code=task.reason_code,
        anomalies=anomalies,
        denied_actions=list(
            dict.fromkeys(
                anomaly.rejected_operation
                for anomaly in anomalies
                if anomaly.rejected_operation
            )
        ),
        retrieval_modes=retrieval_modes,
        multi_step=task.multi_step,
        autonomous=task.autonomous,
    )


def _compiled_actions(task):
    """Map operations to action intents; unsupported operations become anomalies."""
    actions: list[str] = []
    anomalies: list[SemanticCompilerAnomaly] = []
    for operation in task.operations:
        action = _OPERATION_ACTIONS.get((operation.kind, operation.target))
        if action is not None:
            if action not in actions:
                actions.append(action)
            continue
        # Conversation is response-only by contract. Any parser verb attached
        # to it is non-authoritative and cannot justify an execution lane.
        if operation.target == "conversation":
            continue
        # Explanation/composition are presentation semantics even when the
        # model attaches a topical domain. They never grant domain authority by
        # themselves. compose+email is already mapped above to email_draft.
        if operation.kind in {"explain", "compose"}:
            continue
        anomalies.append(
            SemanticCompilerAnomaly(
                code="unsupported_semantic_operation",
                detail=f"{operation.kind}:{operation.target}",
                rejected_operation=f"{operation.kind}:{operation.target}",
            )
        )
    return actions, anomalies


def _check_subject_domains(task, actions, anomalies):
    """Flag actions whose domain contradicts the single domain the subjects imply."""
    subject_profiles = {
        _SUBJECT_PROFILES[subject.target]
        for subject in task.subjects
        if subject.target in _SUBJECT_PROFILES
    }
    research_subject_targets = {
        subject.target
        for subject in task.subjects
        if _SUBJECT_PROFILES.get(subject.target) == "research"
    }
    if len(subject_profiles) == 1:
        expected_profile = next(iter(subject_profiles))
        for action in actions:
            action_profile = _ACTION_PROFILES.get(action)
            if action_profile is None or action_profile == expected_profile:
                continue
            # Public-web research is an intentional specialization of the
            # trading-research profile. The trading profile has governed
            # research.web_search authority, so a descriptive public_web subject
            # must not veto an explicitly typed market operation. Keep this
            # narrow: weather/software_release subjects still require the
            # research profile and therefore continue to fail closed.
            public_web_subject_specialization = bool(
                expected_profile == "research"
                and action_profile == "trading-research"
                and research_subject_targets
                and research_subject_targets <= {"public_web"}
            )
            if (
                expected_profile == "trading-research"
                and action_profile == "research"
            ) or public_web_subject_specialization:
                continue
            anomalies.append(
                SemanticCompilerAnomaly(
                    code="unexpected_cross_domain_action",
                    detail=(
                        f"subjects imply {expected_profile}, but semantic operation "
                        f"implies {action_profile}"
                    ),
                    rejected_operation=action,
                )
            )


def _dependency_profile(task, profile_id, anomalies):
    """The profile implied by required dependencies, and conflicts with the action profile."""
    dependencies: list[SemanticDataDependency] = list(task.data_dependencies)

    # A dependency may be the only authority-bearing semantic fact (for
    # example: "finish the recommendation with current sources" can be a
    # conversation compose operation plus a software-release dependency).
    # Derive the profile from those dependencies instead of requiring the LLM to
    # duplicate the same fact as a read operation.
    dependency_profiles = {
        _SUBJECT_PROFILES[dependency.target]
        for dependency in dependencies
        if dependency.required and dependency.target in _SUBJECT_PROFILES
    }
    if profile_id is None and len(dependency_profiles) == 1:
        profile_id = next(iter(dependency_profiles))
    elif profile_id is not None:
        incompatible = {
            dependency_profile
            for dependency_profile in dependency_profiles
            if dependency_profile != profile_id
            and not (
                profile_id == "trading-research"
                and dependency_profile == "research"
            )
        }
        if incompatible:
            anomalies.append(
                SemanticCompilerAnomaly(
                    code="unexpected_cross_domain_action",
                    detail=(
                        f"actions imply {profile_id}, but dependencies imply "
                        + ", ".join(sorted(incompatible))
                    ),
                )
            )
    return dependencies, profile_id


def _add_implicit_dependencies(task, actions, dependencies):
    """Add the current-state dependencies that private reads and external targets always have."""
    implicit_dependencies: list[tuple[str, str]] = []
    if any(
        operation.target == "home"
        and _OPERATION_ACTIONS.get((operation.kind, operation.target))
        in {"home_read", "home_mutate"}
        for operation in task.operations
    ):
        implicit_dependencies.append(("home", "current"))
    if any(
        operation.target == "home_energy"
        and _OPERATION_ACTIONS.get((operation.kind, operation.target)) == "home_read"
        for operation in task.operations
    ):
        implicit_dependencies.append(("home_energy", "current"))
    if "email_read" in actions:
        implicit_dependencies.append(("email", "current"))
    if "calendar_read" in actions:
        implicit_dependencies.append(("calendar", "current"))

    # These semantic targets inherently name external/current state. The model
    # may state the dependency explicitly (and override freshness), but Omnix
    # does not rely on it remembering a duplicate field before issuing evidence
    # policy.
    implicit_external_freshness = {
        "repository_ci": "current",
        "market": "timeless",
        "market_quote": "current",
        "market_filing": "timeless",
        "market_status": "current",
        "weather": "current",
        "software_release": "timeless",
        "public_web": "timeless",
    }
    for operation in task.operations:
        freshness = implicit_external_freshness.get(operation.target)
        if freshness is not None and operation.kind in {
            "read",
            "inspect",
            "research",
            "compare",
            "validate",
        }:
            implicit_dependencies.append((operation.target, freshness))

    for target, freshness in implicit_dependencies:
        if not any(dep.target == target for dep in dependencies):
            dependencies.append(
                SemanticDataDependency(
                    target=cast(Any, target),
                    freshness=cast(Any, freshness),
                    subject_reference=_reference_for_target(task, target),
                    required=True,
                )
            )


def _compiled_requirements(dependencies, latest_user_message, task, anomalies):
    """Evidence requirements for the dependencies; market and filing evidence needs a resolved subject."""
    requirements: list[EvidenceRequirement] = []
    for dependency in dependencies:
        requirement = _evidence_requirement(latest_user_message, dependency, task)
        if requirement is None:
            continue
        dynamic_market_discovery = "discover" in _retrieval_modes(task, dependencies)
        if (
            requirement.source_class in {"market_quote", "company_filing"}
            and requirement.subject is None
            and not dynamic_market_discovery
        ):
            anomalies.append(
                SemanticCompilerAnomaly(
                    code="unresolved_evidence_subject",
                    detail=(
                        f"{requirement.source_class} requires a resolved subject; "
                        f"semantic target {dependency.target} did not provide one"
                    ),
                    rejected_operation=dependency.target,
                )
            )
        requirements.append(requirement)

    requirements = merge_evidence_requirements(requirements)
    return requirements


def _without_local_repository_evidence(routing_environment, actions, requirements):
    """A selected Local folder answers repository-contents evidence for workspace work."""
    active_workspace = str(
        getattr(routing_environment, "active_workspace", None)
        or (
            routing_environment.get("active_workspace")
            if isinstance(routing_environment, dict)
            else ""
        )
        or ""
    ).strip()
    if active_workspace and set(actions) & {
        "workspace_read",
        "workspace_mutate",
        "workspace_execute",
    }:
        requirements = [
            requirement
            for requirement in requirements
            if requirement.source_class != "repo_contents"
        ]
    return requirements


def _compiled_evidence_decision(latest_user_message, requirements, task):
    """The evidence policy: required sources, forbidden external access and attribution, from the latest message."""
    lowered = " ".join(str(latest_user_message or "").casefold().split())
    external_forbidden = any(
        phrase in lowered
        for phrase in (
            "without using the web",
            "without the web",
            "do not use the web",
            "don't use the web",
            "never use the web",
            "without using the internet",
            "do not use the internet",
            "don't use the internet",
            "from memory only",
        )
    )
    attribution = (
        "required"
        if any(
            phrase in lowered
            for phrase in ("with sources", "cite sources", "citations", "sourced")
        )
        else "when_used"
    )
    evidence_decision = EvidenceDecision(
        policy=EvidencePolicy(
            requirement="required" if requirements else "none",
            external_access="forbidden" if external_forbidden else "allowed",
            requirements=requirements,
            user_visible_attribution=cast(Any, attribution),
            retrieval=RetrievalPolicy(strategy="adaptive"),
        ),
        confidence=1.0,
        reason=f"semantic_task_compiler:{task.reason_code}"[:240],
        classifier="deterministic",
    )
    return evidence_decision


def _compiled_lane(dependencies, task, actions, anomalies, profile_id):
    """The execution lane and profile: clarification, stateful work, bounded public reads or research."""
    retrieval_modes = _retrieval_modes(task, dependencies)
    public_read_only = bool(actions) and set(actions) <= {"research_read", "market_read"}
    public_dependency = any(
        dependency.required and dependency.target in _PUBLIC_READ_TARGETS
        for dependency in dependencies
    )
    discovery_required = "discover" in retrieval_modes
    stateful = _has_stateful_actions(actions)
    unsafe_semantic_anomaly = any(
        anomaly.code in {
            "unsupported_composite_profiles",
            "unexpected_cross_domain_action",
            "unsupported_semantic_operation",
            "unresolved_evidence_subject",
        }
        for anomaly in anomalies
    )
    requires_clarification = (
        task.ambiguity == "clarification_required"
        or unsafe_semantic_anomaly
    )

    if requires_clarification:
        lane: Literal["chat", "agent"] = "chat"
    elif stateful:
        lane = "agent"
    elif public_read_only or public_dependency:
        # Retrieval shape, not multi_step/autonomous, chooses the execution
        # scheduler for read-only external work. lookup/verify/filter are
        # bounded governed Chat reads; discover has an unknown result/source set
        # and therefore requires the durable research Agent.
        lane = "agent" if discovery_required else "chat"
    elif task.autonomous and task.multi_step:
        # Compatibility for non-external autonomous task types.  Public/current
        # research never reaches this branch.
        lane = "agent"
    else:
        lane = "chat"

    if lane == "agent" and profile_id is None:
        # Autonomous reasoning/research without stateful operations uses the
        # research profile. This is derived policy, not model-selected profile.
        profile_id = "research"
    return lane, profile_id, requires_clarification, retrieval_modes


def semantic_task_from_legacy(decision: object) -> SemanticTask:
    """Compatibility adapter for tests/extensions that still emit v1 decisions."""

    operations: list[SemanticOperation] = []
    action_map: dict[str, tuple[str, str]] = {
        "workspace_read": ("inspect", "workspace"),
        "workspace_execute": ("execute", "workspace"),
        "workspace_mutate": ("modify", "workspace"),
        "home_read": ("inspect", "home"),
        "home_mutate": ("modify", "home"),
        "email_read": ("read", "email"),
        "email_draft": ("draft", "email"),
        "email_send": ("send", "email"),
        "calendar_read": ("read", "calendar"),
        "calendar_create": ("create", "calendar"),
        "contacts_read": ("read", "contacts"),
        "research_read": ("research", "public_web"),
        "market_read": ("research", "market"),
    }
    for raw in list(getattr(decision, "action_intents", []) or []):
        mapped = action_map.get(str(raw))
        if mapped is None:
            continue
        operations.append(SemanticOperation(kind=cast(Any, mapped[0]), target=cast(Any, mapped[1])))

    source_to_target = {
        "general_current_web": "public_web",
        "breaking_news": "public_web",
        "market_news": "market",
        "company_filing": "market_filing",
        "software_release": "software_release",
        "repo_contents": "repository",
        "repo_ci_state": "repository_ci",
        "home_state": "home",
        "home_energy": "home_energy",
        "calendar_state": "calendar",
        "email_state": "email",
        "market_quote": "market_quote",
        "market_status": "market_status",
        "weather_state": "weather",
    }
    dependencies: list[SemanticDataDependency] = []
    for row in list(getattr(decision, "evidence_requirements", []) or []):
        target = source_to_target.get(str(getattr(row, "source_class", "")))
        if target is None:
            continue
        dependencies.append(
            SemanticDataDependency(
                target=cast(Any, target),
                freshness=cast(Any, str(getattr(row, "freshness", "current") or "current")),
                required=True,
            )
        )

    return SemanticTask(
        intent=str(
            getattr(decision, "primary_intent", None)
            or "legacy_semantic_task"
        )[:160],
        operations=operations,
        data_dependencies=dependencies,
        autonomous=str(getattr(decision, "lane", "chat")) == "agent",
        multi_step=bool(getattr(decision, "multi_step", False)),
        ambiguity="none",
        confidence=float(getattr(decision, "confidence", 0.75) or 0.75),
        reason_code="legacy_semantic_v1",
    )


__all__ = [
    "SemanticAmbiguity",
    "SemanticCompilerAnomaly",
    "SemanticDataDependency",
    "SemanticOperation",
    "SemanticRetrievalMode",
    "SemanticSubject",
    "SemanticTask",
    "SemanticTaskCompilation",
    "SemanticWorkspaceSurface",
    "compile_semantic_task",
    "semantic_task_from_legacy",
]
