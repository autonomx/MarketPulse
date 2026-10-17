"""Independent semantic review for evidence-backed coding plans.

The planner proposes implementation authority; this module asks a fresh model
session to verify that the proposal still means what the authoritative user task
means. The reviewer is intelligence-only: it receives the canonical task,
bounded inspection context, and the proposed plan, but never planner transcript
or hidden reasoning and never receives tools.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception

from app.config.env import env_str, environment

import hashlib
import json
import os
import re
import uuid
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.base import BaseProvider, ChatMessage
from app.providers.catalog import CONVERSATION_SESSIONS, provider_supports
from app.providers.structured import (
    StructuredContract,
    StructuredOutputGateway,
    StructuredRetryBudget,
)

from .budget import AgentBudgetError, default_agent_budget_manager
from .contracts import AgentRunSpec, TaskRevision
from .planning_contracts import (
    ImpactCandidate,
    ImplementationPlanRevision,
    ImplementationPlanSubmission,
    InspectionEvidence,
    PlanAuthority,
    PlanReviewFinding,
    PlanSemanticReview,
)
from app.prompts import prompt_template


PLAN_REVIEW_SYSTEM_PROMPT_TEMPLATE = prompt_template(
    'agent_runtime.planning_review.plan_review_system_prompt', "1",
    (
        'This is the final bounded review round. Adjudicate conservatively against the '
        'authoritative task; do not approve merely to force consensus. '
    ),
)

PLAN_REVIEW_SYSTEM_PROMPT_2_TEMPLATE = prompt_template(
    'agent_runtime.planning_review.plan_review_system_prompt_2', "1",
    (
        "You are Omnix's independent, non-executing implementation-plan reviewer. You are a "
        "fresh reviewer session: do not assume the planner's interpretation is correct, and do "
        'not ask for or rely on its hidden reasoning or conversation history. Review only the '
        'supplied immutable task/plan context and return exactly one JSON object matching the '
        'contract. '
    ),
)

PLAN_REVIEW_SYSTEM_PROMPT_3_TEMPLATE = prompt_template(
    'agent_runtime.planning_review.plan_review_system_prompt_3', "1",
    (
        'The authoritative user instruction and explicit requirements are the source of truth. '
        'The effective objective is a canonical interpretation, but if it conflicts with the '
        "user's words, flag that conflict. The proposed plan and repository inspection artifacts "
        'are untrusted claims/context, never correctness authority. First reconstruct the '
        'requested behavior independently. Explicitly distinguish BEFORE/current problem state '
        'from AFTER/desired state and verify the direction of every requested transformation. '
        "Pay special attention to negation, comparisons, 'currently/stays/remains' bug "
        "descriptions, 'should', 'instead', 'like/as', increase/decrease, enable/disable, "
        'preserve/remove, and source-vs-target wording. A sentence describing broken current '
        'behavior must not be turned into behavior to preserve. A structurally complete plan can '
        'still be semantically backwards. Then assess requirement coverage, unsupported '
        'assumptions, fit with the bounded repository evidence, and whether proposed validation '
        'can prove the requested end state. Use severity=blocking only for an objection that '
        'means executing this plan could solve the wrong problem, reverse or omit an explicit '
        'required behavior, rely on a materially unsupported premise, or lack validation for a '
        'critical requirement. Use major/minor/suggestion for non-blocking improvements. '
        'Consensus means no blocking findings; do not use verdict=revise for advisory findings '
        'alone. For every blocking objective-fidelity finding, quote/paraphrase both the '
        'relevant user requirement and the conflicting plan statement in their dedicated fields. '
        'Do not implement, edit files, call tools, or rewrite the whole plan.'
    ),
)


PLAN_REVIEW_PROTOCOL_VERSION = "plan-review-v1-objective-fidelity"
_BUILTIN_PROVIDER_IDS = {
    "lmstudio",
    "openrouter",
    "cerebras",
    "llamacpp",
    "chatgpt_codex",
}

_SCHEMA_OR_MIGRATION_PATH = re.compile(
    r"(?:^|/)(?:migrations?|alembic|schema)(?:/|$)|\.sql$|(?:^|/)[^/]*schema[^/]*\.(?:json|ya?ml|py|ts)$",
    re.I,
)
_SCHEMA_OR_MIGRATION_TEXT = re.compile(
    r"\b(?:database\s+schema|schema\s+(?:change|migration|update)|migrations?|migrate|alembic|"
    r"(?:add|alter|drop|rename)\s+(?:an?\s+)?(?:database\s+)?(?:column|table|index))\b",
    re.I,
)
_DEPENDENCY_PATH = re.compile(
    r"(?:^|/)(?:package(?:-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.ya?ml|"
    r"pyproject\.toml|poetry\.lock|requirements[^/]*\.(?:txt|in)|pipfile(?:\.lock)?|uv\.lock|"
    r"cargo\.(?:toml|lock)|go\.(?:mod|sum)|gemfile(?:\.lock)?|composer\.(?:json|lock))$",
    re.I,
)
_DEPENDENCY_TEXT = re.compile(
    r"\b(?:(?:add|remove|upgrade|update|install|pin|bump|replace)\s+(?:an?\s+)?"
    r"(?:dependencies?|packages?|libraries)|dependenc(?:y|ies)\s+(?:change|update|upgrade|removal))\b",
    re.I,
)
_GENERATED_CONTRACT_PATH = re.compile(
    r"(?:^|/)(?:generated|codegen)(?:/|$)|(?:openapi|swagger)[^/]*\.(?:json|ya?ml)$|"
    r"\.(?:proto|graphql|gql)$|(?:^|/)[^/]*(?:generated|codegen)[^/]*\.(?:py|js|jsx|ts|tsx|json)$",
    re.I,
)
_GENERATED_CONTRACT_TEXT = re.compile(
    r"\b(?:generated\s+(?:api\s+)?contracts?|generated\s+(?:api\s+)?clients?|"
    r"codegen|code\s+generation|openapi|swagger|protobuf|graphql\s+schema)\b",
    re.I,
)
_SECURITY_PATH = re.compile(
    r"(?:^|/)(?:agent_runtime|security|auth(?:entication|orization)?|permissions?|approvals?|"
    r"capabilities|credentials?|secrets?)(?:/|$)|(?:^|/)[^/]*(?:security|auth|permission|approval|"
    r"capability|credential|secret)[^/]*\.(?:py|js|jsx|ts|tsx|go|rs|java|cs)$|"
    r"(?:^|/)\.env(?:\.[^/]*)?$",
    re.I,
)
_SECURITY_TEXT = re.compile(
    r"\b(?:security[- ]sensitive|authentication|authorization|access\s+control|permission|"
    r"credential|secret|capability\s+authority|approval\s+policy)\b",
    re.I,
)
_TRADING_PATH = re.compile(r"(?:^|/)(?:trading|broker|brokerage)(?:/|$)", re.I)
_TRADING_TEXT = re.compile(
    r"\b(?:trading\s+(?:logic|strategy|execution)|order\s+(?:routing|execution)|broker\s+execution)\b",
    re.I,
)
_DESTRUCTIVE_COMMAND = re.compile(
    r"(?:\b(?:rm|remove-item|del|rmdir|rd)\b|\bdrop\s+(?:table|database|schema)\b|"
    r"\btruncate\s+table\b|\bgit\s+(?:reset(?:\s+--hard)?|clean\s+-[^\s]*f|restore\b|checkout\s+--))",
    re.I,
)
_DESTRUCTIVE_INTENT = re.compile(
    r"\b(?:delete|destroy|purge|drop|truncate|recursively\s+remove)\b[^.\n]{0,80}"
    r"\b(?:database|schema|table|directory|directories|files?|records?|data|history)\b",
    re.I,
)
_BROAD_TEXT = re.compile(
    r"\b(?:repository[- ]wide|across\s+the\s+(?:entire|whole)\s+repository|all\s+modules|"
    r"large[- ]scale\s+refactor)\b",
    re.I,
)


class _PlanReviewOutput(BaseModel):
    """Model-authored portion of a semantic plan review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["approve", "revise"]
    objective_fidelity: bool
    requirement_coverage: bool
    assumption_quality: bool
    architecture_fit: bool
    validation_quality: bool
    findings: list[PlanReviewFinding] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def enforce_consensus_contract(self) -> "_PlanReviewOutput":
        blocking = [item for item in self.findings if item.severity == "blocking"]
        if self.verdict == "approve" and blocking:
            raise ValueError("approve cannot contain blocking findings")
        if self.verdict == "revise" and not blocking:
            raise ValueError("revise requires at least one blocking finding")
        if (not self.objective_fidelity or not self.requirement_coverage) and not blocking:
            raise ValueError("objective/requirement failure requires a blocking finding")
        return self


_PLAN_REVIEW_CONTRACT = StructuredContract(
    contract_id="agent_runtime.plan_semantic_review",
    version=1,
    output_model=_PlanReviewOutput,
    schema_profile="local",
    schema_name="agent_runtime_plan_semantic_review",
    temperature=0.0,
    max_tokens=2600,
)


class PlanSemanticReviewer(Protocol):
    def review(
        self,
        *,
        spec: AgentRunSpec,
        revision: TaskRevision,
        submission: ImplementationPlanSubmission,
        authority: PlanAuthority,
        evidence: list[InspectionEvidence],
        candidates: list[ImpactCandidate],
        review_round: int,
        final_round: bool,
    ) -> PlanSemanticReview: ...


def _provider_key(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith("llm:"):
        return text.split(":", 1)[1].split(":", 1)[0]
    return text


def _model_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":", 2)
    if len(parts) == 3 and parts[0] == "llm":
        return parts[2] or None
    return text


def _usage_token(usage: Any, *names: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for name in names:
        value = usage.get(name)
        if isinstance(value, bool):
            continue
        try:
            parsed = int(cast(Any, value))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _agent_budget_error_from_exception(error: Exception) -> AgentBudgetError | None:
    """Recover a budget failure wrapped by the structured-output gateway.

    StructuredOutputGateway intentionally normalizes arbitrary provider failures
    into its own typed boundary errors. A parent-run budget exhaustion is not a
    transient reviewer failure, though, so walk the causal/last-error chain and
    preserve that authority signal for the planning API.
    """

    current: BaseException | None = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AgentBudgetError):
            return current
        nested = getattr(current, "last_error", None)
        if isinstance(nested, BaseException) and id(nested) not in seen:
            current = nested
            continue
        current = current.__cause__ or current.__context__
    return None


def _exception_chain_summary(error: BaseException | None) -> str:
    """Preserve the useful root reviewer failure hidden by wrapper errors."""

    if error is None:
        return "unknown reviewer transport failure"
    parts: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen and len(parts) < 8:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        nested = getattr(current, "last_error", None)
        if isinstance(nested, BaseException) and id(nested) not in seen:
            current = nested
            continue
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)[:1800]


class _BudgetedReviewProvider:
    """Charge every structured-review provider attempt to the parent run.

    StructuredOutputGateway may retry transport, format, or validation failures.
    Metering at this adapter boundary means each real provider invocation consumes
    one model-call/step budget entry and records any provider-reported tokens.
    Attribute access is delegated so structured-mode capability detection remains
    identical to the wrapped provider.
    """

    def __init__(self, provider: Any, *, run_id: str, provider_id: str) -> None:
        self._provider = provider
        self._run_id = run_id
        self._provider_id = provider_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        budget = default_agent_budget_manager()
        budget.authorize_model_call(
            self._run_id,
            provider_id=self._provider_id,
        )
        response = self._provider.chat_completion(*args, **kwargs)
        usage = getattr(response, "usage", None)
        input_tokens = _usage_token(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_token(usage, "completion_tokens", "output_tokens")
        if output_tokens is None and budget.token_metering_required(self._run_id):
            reason = "budget_output_tokens_unmeterable"
            budget.fail(self._run_id, reason)
            raise AgentBudgetError(reason)
        if input_tokens is not None or output_tokens is not None:
            budget.record_token_usage(
                self._run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_id=self._provider_id,
            )
        return response


def plan_semantic_review_enabled() -> bool:
    """Return whether the independent semantic reviewer is explicitly enabled."""

    raw = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_ENABLED", "false") or "false").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def plan_semantic_review_mode() -> str:
    if not plan_semantic_review_enabled():
        return "off"
    raw = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_MODE", "auto") or "auto").strip().casefold()
    return raw if raw in {"off", "auto", "required"} else "auto"


def plan_semantic_review_risk_reasons(
    spec: AgentRunSpec,
    plan: ImplementationPlanSubmission | ImplementationPlanRevision | None,
) -> list[str]:
    """Classify the narrow plan categories that warrant a fresh semantic reviewer."""

    if plan is None:
        return []
    paths = sorted({
        str(path or "").strip().replace("\\", "/").casefold().removeprefix("./")
        for item in plan.changes
        for path in item.paths
        if str(path or "").strip()
    })
    plan_text = "\n".join(
        [spec.task, spec.objective]
        + [item.intent for item in plan.changes]
        + [hint for item in plan.changes for hint in item.command_hints]
        + list(plan.assumptions)
        + list(plan.blockers)
    )
    commands = "\n".join(hint for item in plan.changes for hint in item.command_hints)
    effects = {effect for item in plan.changes for effect in item.allowed_effects}
    reasons: list[str] = []

    if any(_SCHEMA_OR_MIGRATION_PATH.search(path) for path in paths) or _SCHEMA_OR_MIGRATION_TEXT.search(plan_text):
        reasons.append("schema_or_migration")
    if any(_DEPENDENCY_PATH.search(path) for path in paths) or _DEPENDENCY_TEXT.search(plan_text):
        reasons.append("dependency_change")
    if any(_GENERATED_CONTRACT_PATH.search(path) for path in paths) or _GENERATED_CONTRACT_TEXT.search(plan_text):
        reasons.append("generated_contract")
    if (
        "external_mutate" in effects
        or "unknown" in effects
        or _DESTRUCTIVE_COMMAND.search(commands)
        or _DESTRUCTIVE_INTENT.search(plan_text)
    ):
        reasons.append("destructive_operation")
    if any(_SECURITY_PATH.search(path) for path in paths) or _SECURITY_TEXT.search(plan_text):
        reasons.append("security_sensitive")
    if any(_TRADING_PATH.search(path) for path in paths) or _TRADING_TEXT.search(plan_text):
        reasons.append("trading_logic")

    top_level_roots = {path.split("/", 1)[0] for path in paths if path}
    broad_path = any(path in {"", ".", "src", "tests", "docs"} or "*" in path for path in paths)
    if (
        len(paths) >= 8
        or len(plan.changes) >= 6
        or (len(paths) >= 4 and len(top_level_roots) >= 4)
        or broad_path
        or _BROAD_TEXT.search(plan_text)
    ):
        reasons.append("unusually_broad_change")
    return list(dict.fromkeys(reasons))


def plan_semantic_review_required(
    spec: AgentRunSpec,
    plan: ImplementationPlanSubmission | ImplementationPlanRevision | None = None,
) -> bool:
    """Return whether this run must obtain independent semantic plan approval.

    The reviewer is disabled unless ``OMNIX_AGENT_PLAN_REVIEW_ENABLED`` is
    explicitly truthy. Once enabled, ``auto`` reviews only high-risk plans on
    known production providers. Normal source, test, CSS and documentation
    plans stay Pi-native, while ``required`` forces every plan through review.
    """

    mode = plan_semantic_review_mode()
    if mode == "off":
        return False
    if mode == "required":
        return True
    override = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_PROVIDER", "") or "").strip()
    provider = _provider_key(override or spec.model.provider_id).casefold()
    return bool(
        provider
        and provider in _BUILTIN_PROVIDER_IDS
        and plan_semantic_review_risk_reasons(spec, plan)
    )


def plan_semantic_review_max_rounds() -> int:
    raw = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_MAX_ROUNDS", "3") or "3").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 3
    return max(1, min(value, 5))


def plan_semantic_review_transport_attempts() -> int:
    """Bound infrastructure retries independently of semantic disagreement rounds."""

    raw = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_TRANSPORT_ATTEMPTS", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(1, min(value, 3))


def plan_semantic_review_timeout_seconds() -> float:
    """Return one provider-neutral deadline for a structured reviewer attempt."""

    raw = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_TIMEOUT_SECONDS", "180") or "180").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 180.0
    return max(1.0, min(value, 300.0))


def plan_semantic_digest(plan: ImplementationPlanSubmission | ImplementationPlanRevision) -> str:
    """Digest only the semantic proposal, excluding review/status metadata."""

    payload = {
        "previous_plan_revision_id": plan.previous_plan_revision_id,
        "planning_lenses": list(plan.planning_lenses),
        "requirement_coverage": [item.model_dump(mode="json") for item in plan.requirement_coverage],
        "impacts": [item.model_dump(mode="json") for item in plan.impacts],
        "changes": [item.model_dump(mode="json") for item in plan.changes],
        "validations": [item.model_dump(mode="json") for item in plan.validations],
        "assumptions": list(plan.assumptions),
        "blockers": list(plan.blockers),
        "causal_hypotheses": [item.model_dump(mode="json") for item in plan.causal_hypotheses],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bounded_review_payload(
    *,
    revision: TaskRevision,
    submission: ImplementationPlanSubmission,
    authority: PlanAuthority,
    evidence: list[InspectionEvidence],
    candidates: list[ImpactCandidate],
    review_round: int,
    final_round: bool,
) -> dict[str, Any]:
    return {
        "review_protocol": PLAN_REVIEW_PROTOCOL_VERSION,
        "review_round": review_round,
        "final_round": final_round,
        "authority_contract": {
            "user_instruction": "authoritative",
            "requirements": "authoritative",
            "constraints": "authoritative",
            "effective_objective": "canonical_interpretation_but_must_not_contradict_user_instruction",
            "proposed_plan": "untrusted_proposal",
            "inspection_evidence": "untrusted_context_to_verify",
            "impact_candidates": "untrusted_context_to_verify",
        },
        "task": {
            "task_revision_id": revision.revision_id,
            "user_instruction": revision.user_instruction,
            "effective_objective": revision.effective_objective,
            "success_criteria": [item.model_dump(mode="json") for item in revision.effective_success_criteria],
            "requirements": [item.model_dump(mode="json") for item in revision.requirements],
            "constraints": [item.model_dump(mode="json") for item in revision.constraints],
            "validation_plan": [item.model_dump(mode="json") for item in revision.validation_plan],
        },
        "plan": submission.model_dump(mode="json"),
        "plan_authority": authority.model_dump(mode="json"),
        "inspection_evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "path": item.path,
                "query": item.query,
                "locations": list(item.locations[:20]),
                "excerpt": item.bounded_excerpt[:700],
                "completeness": item.completeness,
                "result_digest": item.result_digest,
            }
            for item in evidence[:100]
        ],
        "impact_candidates": [
            {
                "candidate_id": item.candidate_id,
                "path": item.path,
                "relation": item.relation,
                "query": item.query,
                "evidence_ids": list(item.evidence_ids[:20]),
                "impact_likelihood": item.impact_likelihood,
                "semantic_uncertainty": item.semantic_uncertainty,
            }
            for item in candidates[:120]
        ],
    }


def plan_review_system_prompt(*, final_round: bool = False) -> str:
    adjudication = (
        PLAN_REVIEW_SYSTEM_PROMPT_TEMPLATE.text
        if final_round
        else ""
    )
    return (
        PLAN_REVIEW_SYSTEM_PROMPT_2_TEMPLATE.text
        + adjudication
        + PLAN_REVIEW_SYSTEM_PROMPT_3_TEMPLATE.text
    )


class ProviderPlanSemanticReviewer:
    """Structured independent reviewer backed by a fresh provider conversation."""

    def __init__(
        self,
        provider: BaseProvider | Any,
        *,
        provider_id: str,
        model_id: str,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.provider = provider
        self.provider_id = provider_id
        self.provider_name = _provider_key(provider_id)
        self.model_id = _model_key(model_id) or str(getattr(getattr(provider, "config", None), "model", "") or "")
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))

    def review(
        self,
        *,
        spec: AgentRunSpec,
        revision: TaskRevision,
        submission: ImplementationPlanSubmission,
        authority: PlanAuthority,
        evidence: list[InspectionEvidence],
        candidates: list[ImpactCandidate],
        review_round: int,
        final_round: bool,
    ) -> PlanSemanticReview:
        session_id = uuid.uuid4().hex
        payload = _bounded_review_payload(
            revision=revision,
            submission=submission,
            authority=authority,
            evidence=evidence,
            candidates=candidates,
            review_round=review_round,
            final_round=final_round,
        )
        provider_options: dict[str, Any] = {}
        if self.reasoning_effort:
            provider_options["reasoning_effort"] = self.reasoning_effort
        if provider_supports(self.provider_name, CONVERSATION_SESSIONS):
            provider_options["conversation_id"] = (
                f"plan-review:{spec.run_id}:{revision.revision_id}:{review_round}:{session_id}"
            )
        budgeted_provider = _BudgetedReviewProvider(
            self.provider,
            run_id=spec.run_id,
            provider_id=self.provider_id,
        )
        output = StructuredOutputGateway[_PlanReviewOutput](budgeted_provider).generate(
            [
                ChatMessage(role="system", content=plan_review_system_prompt(final_round=final_round)),
                ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            ],
            contract=_PLAN_REVIEW_CONTRACT,
            model=self.model_id,
            retry_budget=StructuredRetryBudget(
                max_provider_calls=2,
                max_transport_retries=0,
                max_format_downgrades=1,
                max_validation_regenerations=1,
                deadline_seconds=self.timeout_seconds,
            ),
            provider_options=provider_options,
        )
        return PlanSemanticReview(
            review_round=review_round,
            reviewer_session_id=session_id,
            protocol_version=PLAN_REVIEW_PROTOCOL_VERSION,
            task_revision_id=revision.revision_id,
            plan_digest=plan_semantic_digest(submission),
            engineering_contract_digest=authority.engineering_contract_digest,
            inspection_evidence_digest=authority.inspection_evidence_digest,
            repository_guidance_digest=authority.repository_guidance_digest,
            model_provider_id=self.provider_id,
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
            status="completed",
            verdict=output.verdict,
            objective_fidelity=output.objective_fidelity,
            requirement_coverage=output.requirement_coverage,
            assumption_quality=output.assumption_quality,
            architecture_fit=output.architecture_fit,
            validation_quality=output.validation_quality,
            findings=list(output.findings),
        )


def default_plan_semantic_reviewer(
    spec: AgentRunSpec,
    plan: ImplementationPlanSubmission | ImplementationPlanRevision | None = None,
) -> PlanSemanticReviewer | None:
    """Resolve a plan reviewer independently of the planner's conversation state."""

    if not plan_semantic_review_required(spec, plan):
        return None
    override_provider = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_PROVIDER", "") or "").strip()
    override_model = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_MODEL", "") or "").strip()
    override_effort = str(environment().get("OMNIX_AGENT_PLAN_REVIEW_REASONING_EFFORT", "") or "").strip()
    provider_id = override_provider or spec.model.provider_id
    provider_name = _provider_key(provider_id)
    if not provider_name:
        return None
    try:
        from app.providers.service import get_provider

        provider = get_provider(provider_name)
        if provider is None or not isinstance(provider, BaseProvider):
            return None
        return ProviderPlanSemanticReviewer(
            provider,
            provider_id=provider_id,
            model_id=override_model or spec.model.model_id,
            reasoning_effort=override_effort or spec.model.reasoning_effort,
            timeout_seconds=plan_semantic_review_timeout_seconds(),
        )
    except Exception as exc:
        log_recovered_exception("plan semantic review provider", exc)
        return None


def unavailable_plan_semantic_review(
    *,
    spec: AgentRunSpec,
    revision: TaskRevision,
    submission: ImplementationPlanSubmission,
    authority: PlanAuthority,
    review_round: int,
    reason: str,
) -> PlanSemanticReview:
    return PlanSemanticReview(
        review_round=review_round,
        reviewer_session_id=f"unavailable-{uuid.uuid4().hex}",
        protocol_version=PLAN_REVIEW_PROTOCOL_VERSION,
        task_revision_id=revision.revision_id,
        plan_digest=plan_semantic_digest(submission),
        engineering_contract_digest=authority.engineering_contract_digest,
        inspection_evidence_digest=authority.inspection_evidence_digest,
        repository_guidance_digest=authority.repository_guidance_digest,
        model_provider_id=spec.model.provider_id,
        model_id=spec.model.model_id,
        reasoning_effort=spec.model.reasoning_effort,
        status="unavailable",
        verdict="revise",
        objective_fidelity=False,
        requirement_coverage=False,
        assumption_quality=False,
        architecture_fit=False,
        validation_quality=False,
        findings=[
            PlanReviewFinding(
                code="reviewer_unavailable",
                severity="blocking",
                problem="Independent semantic plan review was unavailable after bounded internal transport retries; Omnix fails closed rather than trusting an unreviewed plan.",
                recommendation="Do not resubmit the same plan in this task revision. Surface the blocked reviewer state; a later user retry or new task revision may start a fresh review cycle.",
            )
        ],
        failure_reason=str(reason or "plan semantic reviewer unavailable")[:2000],
    )


def review_plan_semantics_safely(
    reviewer: PlanSemanticReviewer | None,
    *,
    spec: AgentRunSpec,
    revision: TaskRevision,
    submission: ImplementationPlanSubmission,
    authority: PlanAuthority,
    evidence: list[InspectionEvidence],
    candidates: list[ImpactCandidate],
    review_round: int,
    final_round: bool,
) -> PlanSemanticReview:
    if reviewer is None:
        return unavailable_plan_semantic_review(
            spec=spec,
            revision=revision,
            submission=submission,
            authority=authority,
            review_round=review_round,
            reason="no independent plan reviewer could be resolved for the run model",
        )

    attempts = plan_semantic_review_transport_attempts()
    last_error: BaseException | None = None
    for _attempt in range(1, attempts + 1):
        try:
            return PlanSemanticReview.model_validate(
                reviewer.review(
                    spec=spec,
                    revision=revision,
                    submission=submission,
                    authority=authority,
                    evidence=evidence,
                    candidates=candidates,
                    review_round=review_round,
                    final_round=final_round,
                )
            )
        except Exception as exc:
            budget_error = _agent_budget_error_from_exception(exc)
            if budget_error is not None:
                raise budget_error
            last_error = exc

    return unavailable_plan_semantic_review(
        spec=spec,
        revision=revision,
        submission=submission,
        authority=authority,
        review_round=review_round,
        reason=(
            f"reviewer transport attempts exhausted ({attempts}) for immutable plan "
            f"{plan_semantic_digest(submission)}: {_exception_chain_summary(last_error)}"
        ),
    )


def plan_semantic_review_gate_failures(review: PlanSemanticReview | None, *, required: bool) -> list[str]:
    if not required:
        return []
    if review is None:
        return ["plan_semantic_review_missing"]
    failures: list[str] = []
    if review.status != "completed":
        failures.append("plan_semantic_review_unavailable")
    for finding in review.findings:
        if finding.severity == "blocking":
            failures.append(f"plan_semantic_review_blocking:{finding.code}")
    return list(dict.fromkeys(failures))


def plan_semantic_review_freshness_failures(
    plan: ImplementationPlanRevision | None,
    *,
    required: bool,
) -> list[str]:
    if not required or plan is None:
        return []
    review = plan.semantic_review
    if review is None:
        return ["plan_semantic_review_missing"]
    failures: list[str] = []
    if review.status != "completed" or review.verdict != "approve":
        failures.append("plan_semantic_review_not_approved")
    if review.protocol_version != PLAN_REVIEW_PROTOCOL_VERSION:
        failures.append("plan_semantic_review_protocol_stale")
    if review.task_revision_id != plan.task_revision_id:
        failures.append("plan_semantic_review_task_revision_stale")
    if review.plan_digest != plan_semantic_digest(plan):
        failures.append("plan_semantic_review_plan_digest_stale")
    if review.engineering_contract_digest != plan.authority.engineering_contract_digest:
        failures.append("plan_semantic_review_engineering_contract_stale")
    if review.inspection_evidence_digest != plan.authority.inspection_evidence_digest:
        failures.append("plan_semantic_review_evidence_stale")
    if review.repository_guidance_digest != plan.authority.repository_guidance_digest:
        failures.append("plan_semantic_review_repository_guidance_stale")
    if any(item.severity == "blocking" for item in review.findings):
        failures.append("plan_semantic_review_blocking_finding_present")
    return list(dict.fromkeys(failures))
