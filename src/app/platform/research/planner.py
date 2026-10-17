"""Dedicated declarative planning for Deep Research."""
from __future__ import annotations

from app.config.env import environment

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.providers.structured import (
    ProviderEmptyResponse,
    StructuredContract,
    StructuredOutputError,
    StructuredOutputExhausted,
    StructuredOutputGateway,
    StructuredRetryBudget,
    StructuredSemanticError,
)

ResearchOperationType = Literal["web_search", "web_extract", "evaluate_evidence", "stop"]


class ResearchPlanningBudget(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=12)
    max_queries: int = Field(default=5, ge=1, le=10)
    max_sources: int = Field(default=12, ge=1, le=100)
    max_extracts: int = Field(default=8, ge=0, le=20)


class ResearchPlanningRequest(BaseModel):
    question: str = Field(min_length=1)
    budget: ResearchPlanningBudget = Field(default_factory=ResearchPlanningBudget)
    source_preferences: list[str] = Field(default_factory=list, max_length=8)
    evidence_summary: list[str] = Field(default_factory=list, max_length=20)


class ResearchOperation(BaseModel):
    operation: ResearchOperationType
    query: str | None = None
    source_record_id: str | None = None
    evaluation_question: str | None = None
    reason: str = ""


class ResearchPlan(BaseModel):
    title: str = Field(default="Deep Research Plan", min_length=1, max_length=160)
    objective: str
    steps: list[str] = Field(default_factory=list, max_length=8)
    operations: list[ResearchOperation] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list, max_length=8)
    stop_reason: str | None = None


class ResearchPlannerDecision(BaseModel):
    plan: ResearchPlan
    backend: Literal["hermes", "provider", "local_fallback", "provider_fallback", "local"]
    warnings: list[str] = Field(default_factory=list)


class LocalResearchPlanner:
    def plan_research(self, request: ResearchPlanningRequest) -> ResearchPlan:
        operations = [
            ResearchOperation(
                operation="web_search",
                query=query,
                reason="Find directly relevant current sources.",
            )
            for query in _local_query_variants(request.question, request.budget.max_queries)
        ]
        operations.extend(
            [
                ResearchOperation(
                    operation="evaluate_evidence",
                    evaluation_question=(
                        "Which claims are supported by multiple sources, and where do sources conflict?"
                    ),
                    reason="Compare support, uncertainty, and conflicts before synthesis.",
                ),
                ResearchOperation(
                    operation="stop",
                    reason="Stop when the evidence is sufficient or the hard budget is exhausted.",
                ),
            ]
        )
        return enforce_research_plan_budget(
            ResearchPlan(
                title=_local_plan_title(request.question),
                objective=request.question,
                steps=_local_plan_steps(request.question),
                operations=operations,
                completion_conditions=[
                    "At least one relevant source was evaluated.",
                    "Uncertainty and source conflicts are explicit.",
                    "The answer remains within the configured research budget.",
                ],
            ),
            request.budget,
        )


class ResearchPlanner:
    """Choose Hermes only through its dedicated research contract."""

    def __init__(
        self,
        *,
        hermes_factory: Callable[[], Any] | None = None,
        local_planner: LocalResearchPlanner | None = None,
        prefer_hermes: bool | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        use_provider: bool = False,
    ) -> None:
        self.hermes_factory = hermes_factory
        self.local_planner = local_planner or LocalResearchPlanner()
        self.prefer_hermes = _hermes_planner_enabled() if prefer_hermes is None else prefer_hermes
        self.provider_id = provider_id
        self.model_id = model_id
        self.use_provider = use_provider

    def plan(self, request: ResearchPlanningRequest) -> ResearchPlannerDecision:
        if not self.prefer_hermes:
            warnings: list[str] = []
            if self.use_provider:
                try:
                    return ResearchPlannerDecision(
                        plan=enforce_research_plan_budget(self._provider_plan(request), request.budget),
                        backend="provider",
                    )
                except Exception as exc:
                    warnings.append(f"provider_planner_unavailable:{type(exc).__name__}")
            return ResearchPlannerDecision(
                plan=self.local_planner.plan_research(request),
                backend="provider_fallback" if warnings else "local",
                warnings=warnings,
            )
        warnings: list[str] = []
        try:
            hermes = self.hermes_factory() if self.hermes_factory else HermesResearchPlanner(_default_hermes_client())
            plan = hermes.plan_research(request)
            return ResearchPlannerDecision(
                plan=enforce_research_plan_budget(plan, request.budget),
                backend="hermes",
            )
        except Exception as exc:
            warnings.append(f"hermes_planner_unavailable:{type(exc).__name__}")
        if self.use_provider:
            try:
                return ResearchPlannerDecision(
                    plan=enforce_research_plan_budget(self._provider_plan(request), request.budget),
                    backend="provider",
                    warnings=warnings,
                )
            except Exception as exc:
                warnings.append(f"provider_planner_unavailable:{type(exc).__name__}")
        return ResearchPlannerDecision(
            plan=self.local_planner.plan_research(request),
            backend="provider_fallback" if self.use_provider else "local_fallback",
            warnings=warnings,
        )

    def _provider_plan(self, request: ResearchPlanningRequest) -> ResearchPlan:
        from app.providers import service as provider_service
        from app.providers import ChatMessage

        provider = provider_service.get_provider(_provider_key(self.provider_id))
        if provider is None:
            raise RuntimeError("research_planner_provider_unavailable")
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are the planning phase of a deep research assistant. Return only one valid JSON object "
                    "matching the supplied research schema. Do not search, browse, cite sources, or answer the "
                    "question. Create a concise title and 3-6 concrete research steps that explain what must "
                    "be investigated before writing the final answer. Keep operations declarative and limited "
                    "to the supplied allowlist. Include at least one web_search operation with a concrete "
                    "query; the plan will be executed only after the user approves it."
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(research_planning_payload(request), sort_keys=True),
            ),
        ]
        try:
            return StructuredOutputGateway(provider).generate(
                messages,
                contract=_PROVIDER_PLAN_CONTRACT,
                model=_model_key(self.model_id),
                retry_budget=_PROVIDER_PLAN_BUDGET,
                provider_options={"request_timeout_seconds": 30},
            )
        except StructuredOutputExhausted as exc:
            if isinstance(exc.last_error, ProviderEmptyResponse):
                raise RuntimeError("research_planner_provider_returned_no_text") from exc
            if exc.last_error is not None:
                raise exc.last_error from None
            raise RuntimeError("research_planner_provider_returned_invalid_plan") from exc
        except StructuredOutputError as exc:
            raise RuntimeError("research_planner_provider_returned_invalid_plan") from exc


def enforce_research_plan_budget(
    plan: ResearchPlan,
    budget: ResearchPlanningBudget,
) -> ResearchPlan:
    operations: list[ResearchOperation] = []
    query_count = 0
    extract_count = 0
    for operation in plan.operations:
        if len(operations) >= budget.max_steps:
            break
        if operation.operation == "web_search":
            if not operation.query or query_count >= budget.max_queries:
                continue
            query_count += 1
        if operation.operation == "web_extract":
            if not operation.source_record_id or extract_count >= budget.max_extracts:
                continue
            extract_count += 1
        operations.append(operation)
    if not operations or operations[-1].operation != "stop":
        if len(operations) >= budget.max_steps:
            operations[-1] = ResearchOperation(
                operation="stop",
                reason="Hard research step budget reached.",
            )
        else:
            operations.append(
                ResearchOperation(operation="stop", reason="Planner operations completed.")
            )
    return plan.model_copy(update={"operations": operations})


def research_plan_schema() -> dict[str, Any]:
    return ResearchPlan.model_json_schema()


def research_planning_payload(request: ResearchPlanningRequest) -> dict[str, Any]:
    return {
        "task": "Create a bounded declarative research plan. Do not execute operations.",
        "allowed_operations": ["web_search", "web_extract", "evaluate_evidence", "stop"],
        "schema": research_plan_schema(),
        "request": request.model_dump(mode="json"),
    }


def _local_plan_title(question: str) -> str:
    subject = _plan_subject(question)
    return f"{subject} Deep Research" if subject else "Deep Research Plan"


def _local_plan_steps(question: str) -> list[str]:
    subject = _plan_subject(question) or "the question"
    return [
        f"Collect recent, authoritative sources relevant to {subject}.",
        "Gather the key data and context needed to answer it.",
        "Analyze the strongest evidence, including uncertainty and opposing signals.",
        "Cross-check sources and identify conflicts or information gaps.",
        "Synthesize a cited answer with clear conclusions and limitations.",
    ]


def _plan_subject(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if not clean:
        return ""
    without_lead = re.sub(
        r"^(?:analy[sz]e|research|investigate|review|summari[sz]e|compare|look into)\s+",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    subject = re.split(
        r"\b(?:and how|and whether|how should|is it|over the next|for the next|what should)\b|[?.!]",
        without_lead,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,:;")
    if not subject or subject.casefold() in {"this", "that", "it", "the topic"}:
        return ""
    return subject[:1].upper() + subject[1:]


def _model_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    parts = text.split(":", 2)
    if len(parts) == 3 and parts[0] == "llm":
        return parts[2] or None
    return text or None


def _provider_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("llm:") else text or None


def _require_search_operation(plan: ResearchPlan) -> None:
    if not any(operation.operation == "web_search" and bool(operation.query) for operation in plan.operations):
        raise StructuredSemanticError("research_planner_provider_returned_no_search_operations")


_PROVIDER_PLAN_CONTRACT = StructuredContract(
    contract_id="research.provider_plan",
    version=1,
    output_model=ResearchPlan,
    semantic_validator=_require_search_operation,
    temperature=0.0,
    max_tokens=1_400,
    regenerate_on_semantic_failure=False,
)
# A second call only if the provider rejects its preferred JSON format; an
# invalid plan falls back to the local planner instead of regenerating.
_PROVIDER_PLAN_BUDGET = StructuredRetryBudget(
    max_provider_calls=2,
    max_transport_retries=0,
    max_format_downgrades=1,
    max_validation_regenerations=0,
    deadline_seconds=35.0,
)


class HermesResearchPlanner:
    """A research plan from the Hermes sidecar, validated as a ``ResearchPlan``."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def plan_research(self, request: Any) -> ResearchPlan:
        from app.providers import ChatMessage

        validated_request = ResearchPlanningRequest.model_validate(request)
        return self.client.structured(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "Return only valid JSON matching the supplied research schema. Do not execute "
                        "operations and do not propose operations outside the allowlist."
                    ),
                ),
                ChatMessage(role="user", content=json.dumps(research_planning_payload(validated_request), sort_keys=True)),
            ],
            output_model=ResearchPlan,
            contract_id="hermes.research_plan",
            json_mode=False,
            timeout=self.client.timeout,
            error="Hermes did not return a valid research plan",
        )


def _default_hermes_client() -> Any:
    from app.providers.hermes_client import HermesSidecarClient
    from app.providers.hermes_status import hermes_runtime_config

    config = hermes_runtime_config()
    return HermesSidecarClient(
        base_url=config.base_url,
        api_key=environment().get("HERMES_API_KEY") or None,
        timeout=config.timeout_seconds,
    )


def _hermes_planner_enabled() -> bool:
    enabled = environment().get("HERMES_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    research = environment().get("OMNIX_DEEP_RESEARCH_HERMES_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    return enabled and research


def _local_query_variants(question: str, max_queries: int) -> list[str]:
    clean = " ".join(str(question or "").split()).strip()
    if not clean:
        return []
    variants = [clean]
    lower = clean.casefold()
    year = str(datetime.now().year)
    if any(term in lower for term in ("gpu", "rtx", "llm", "local model", "coding", "coder")):
        variants.extend(
            [
                f"{clean} local model benchmark {year}",
                f"{clean} coding model comparison Qwen Coder DeepSeek Coder {year}",
            ]
        )
    else:
        keywords = " ".join(_significant_terms(clean)[:8])
        if keywords and keywords != clean:
            variants.append(f"{keywords} reliable sources {year}")
        variants.append(f"{clean} latest source comparison {year}")
    return list(dict.fromkeys(variants))[:max(1, max_queries)]


def _significant_terms(value: str) -> list[str]:
    ignored = {
        "about",
        "after",
        "best",
        "could",
        "does",
        "good",
        "have",
        "latest",
        "should",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
    return [
        word
        for word in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9.+-]*", value)
        if word.casefold() not in ignored and len(word) > 2
    ]
