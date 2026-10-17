"""The Hermes assist planner: its contract, tool catalog and sidecar planning."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.capabilities import default_capability_registry
from app.platform.chat.assist.models import AssistantRequest, ToolCall, ToolRiskLevel, AssistantResult
from app.prompts import prompt_template
from app.providers import ChatMessage
from app.providers.hermes_client import HermesSidecarClient, JsonObject


HermesRisk = Literal["low", "medium", "high", "simulation_truth"]
HermesState = Literal["accepted", "rejected", "fallback"]


@dataclass
class HermesToolSpec:
    name: str
    description: str
    risk: HermesRisk = "low"
    args_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class HermesPolicySpec:
    dry_run: bool = True
    allow_unknown_tools: bool = False
    review_required_for: list[HermesRisk] = field(default_factory=lambda: ["medium", "high", "simulation_truth"])


@dataclass
class HermesPlanRequest:
    workspace: str
    mode: str
    user_message: str
    session_id: str
    domain: str = "chat"
    dry_run: bool = True
    available_tools: list[HermesToolSpec] = field(default_factory=list)
    policy: HermesPolicySpec = field(default_factory=HermesPolicySpec)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class HermesPlanAction:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    risk: HermesRisk = "low"
    reason: str = ""


@dataclass
class HermesPlanResponse:
    state: HermesState
    response: str
    domain: str = "chat"
    actions: list[HermesPlanAction] = field(default_factory=list)
    requires_review: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def hermes_request_from_assistant(
    request: AssistantRequest,
    *,
    available_tools: list[HermesToolSpec] | None = None,
    context: dict[str, Any] | None = None,
) -> HermesPlanRequest:
    return HermesPlanRequest(
        workspace="omnix",
        mode="chat_agent",
        user_message=request.message,
        session_id=request.session_id,
        domain=request.domain,
        dry_run=request.dry_run,
        available_tools=list(available_tools or []),
        policy=HermesPolicySpec(dry_run=request.dry_run),
        context=dict(context or request.metadata or {}),
    )


def hermes_request_payload(request: HermesPlanRequest) -> dict[str, Any]:
    return asdict(request)


def normalize_hermes_response(payload: dict[str, Any], *, fallback_domain: str = "chat") -> HermesPlanResponse:
    if not isinstance(payload, dict):
        return HermesPlanResponse(state="rejected", response="Hermes returned an invalid response.", error="invalid_response")
    actions = []
    for item in payload.get("actions", []) or []:
        if not isinstance(item, dict):
            continue
        actions.append(
            HermesPlanAction(
                tool=str(item.get("tool") or item.get("name") or ""),
                args=dict(item.get("args") or {}),
                risk=_risk(str(item.get("risk") or "low")),
                reason=str(item.get("reason") or ""),
            )
        )
    return HermesPlanResponse(
        state=_state(str(payload.get("state") or "accepted")),
        response=str(payload.get("response") or payload.get("final_message") or "I prepared a plan."),
        domain=str(payload.get("domain") or fallback_domain),
        actions=actions,
        requires_review=bool(payload.get("requires_review")),
        trace=dict(payload.get("trace") or {}),
        error=str(payload["error"]) if payload.get("error") else None,
    )


def tool_calls_from_hermes(response: HermesPlanResponse) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for action in response.actions:
        if action.tool:
            calls.append(ToolCall(name=action.tool, args=dict(action.args), risk=ToolRiskLevel(action.risk), reason=action.reason))
    return calls


def _risk(value: str) -> HermesRisk:
    return value if value in {"low", "medium", "high", "simulation_truth"} else "low"  # type: ignore[return-value]


def _state(value: str) -> HermesState:
    return value if value in {"accepted", "rejected", "fallback"} else "accepted"  # type: ignore[return-value]


def hermes_contract_schema() -> dict[str, Any]:
    return {
        "request": {
            "workspace": "omnix",
            "mode": "chat_agent",
            "user_message": "string",
            "session_id": "string",
            "domain": "string",
            "dry_run": "boolean",
            "available_tools": "list",
            "policy": "object",
            "context": "object",
        },
        "response": {
            "state": "accepted|rejected|fallback",
            "response": "string",
            "domain": "string",
            "actions": "list",
            "requires_review": "boolean",
            "trace": "object",
            "error": "string|null",
        },
    }


def hermes_catalog_specs() -> list[HermesToolSpec]:
    """Project Hermes planner tools from the canonical capability registry."""
    return [
        HermesToolSpec(
            name=capability.id,
            description=capability.description,
            risk=capability.risk,
            args_schema=dict(capability.input_schema),
        )
        for capability in default_capability_registry().hermes_projection()
    ]


def hermes_catalog_payload() -> dict[str, Any]:
    return {"tools": [asdict(item) for item in hermes_catalog_specs()]}


PROPOSAL_ONLY_SYSTEM_PROMPT_TEMPLATE = prompt_template(
    'chat.assist.hermes_proposal_only_system_prompt', "1",
    (
        'You are a non-executing JSON proposal formatter. Your entire final answer MUST be one '
        'JSON object beginning with { and ending with }. Do not use markdown fences, preambles, '
        'explanations, or follow-up questions. Never execute tools. Use exactly these top-level '
        'fields: state, response, domain, actions, requires_review, trace, error. Each action '
        'must use exactly these fields: tool, args, risk, reason. Actions may contain only '
        'allowlisted tools from the request; if no tool can satisfy the request, return an empty '
        'actions list and explain that in response. Set requires_review to true.'
    ),
)

PLANNER_PROMPT_TEMPLATE = prompt_template(
    'chat.assist.hermes_planner_prompt', "1",
    'Create an Omnix execution plan. Do not execute tools.',
)


class HermesAssistantPlanner:
    """Asks the Hermes sidecar for a proposal; Omnix decides what runs."""

    def __init__(self, base_url: str = "http://127.0.0.1:8642", api_key: str | None = None, timeout: float = 45.0):
        self.client = HermesSidecarClient(base_url=base_url, api_key=api_key, timeout=timeout)

    def plan(self, request: AssistantRequest) -> AssistantResult:
        plan = self.client.structured(
            [ChatMessage(role="system", content=PROPOSAL_ONLY_SYSTEM_PROMPT_TEMPLATE.text),
             ChatMessage(role="user", content=_planner_prompt(request))],
            output_model=JsonObject, contract_id="hermes.assistant_plan", json_mode=True,
            timeout=self.client.timeout, error="Hermes did not return valid planner JSON",
        )
        normalized = normalize_hermes_response(plan.model_dump(), fallback_domain=request.domain)
        return AssistantResult(
            success=normalized.state != "rejected" and not normalized.error,
            response=normalized.response,
            domain=normalized.domain,
            tool_calls=tool_calls_from_hermes(normalized),
            requires_confirmation=normalized.requires_review,
            error=normalized.error,
        )


def _planner_prompt(request: AssistantRequest) -> str:
    contract_request = hermes_request_from_assistant(request, available_tools=hermes_catalog_specs())
    return json.dumps(
        {
            "task": PLANNER_PROMPT_TEMPLATE.text,
            "schema": hermes_contract_schema(),
            "request": hermes_request_payload(contract_request),
        },
        sort_keys=True,
    )
