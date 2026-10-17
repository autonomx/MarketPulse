"""The live agent: route a live turn to direct chat or the agent, and plan its proposal."""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.executor import CapabilityGrant, execute_capability, live_agent_tools
from app.platform.chat.assist.hermes import HermesAssistantPlanner
from app.platform.chat.assist.models import AssistantRequest, ToolResult
from app.platform.chat.assist.modes import apply_mode_result, ModeChatResponse, detect_mode_domain
from app.config.env import environment
from app.providers.hermes_status import hermes_runtime_config


LiveAgentRequestedMode = Literal["off", "auto", "agent"]
LiveAgentRoute = Literal["direct_chat", "agent_plan"]

_EXPLICIT_AGENT = re.compile(
    r"^(?:/agent\b|agent[,:]\s|use (?:the )?agent\b|have (?:the )?agent\b)",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(?:turn\s+(?:on|off)|set|create|delete|remove|add|send|email|message|schedule|"
    r"book|reserve|move|rename|upload|download|run|execute|install|update|save|post|"
    r"publish|call|remind|cancel|start|stop|open|close)\b",
    re.IGNORECASE,
)
_ACTION_TARGET = re.compile(
    r"\b(?:light|brightness|thermostat|calendar|event|meeting|email|message|file|folder|"
    r"document|app|application|service|job|task|reminder|reservation|booking|device|"
    r"plug|outlet|kasa|room|system|repository|branch|pull request|issue|download|upload)\b",
    re.IGNORECASE,
)
_KASA_READ = re.compile(
    r"\b(?:kasa|smart\s+plug|plug|outlet)\b.*\b(?:status|state|on|off|discover|find|list)\b|"
    r"^(?:is|are|what|find|discover|list).+\b(?:kasa|smart\s+plug|plug|outlet)\b",
    re.IGNORECASE,
)
_INFORMATIONAL = re.compile(
    r"^(?:what|why|when|where|who|how|tell me|explain|describe|summarize|compare|"
    r"do you think|what do you think|is|are|was|were)\b",
    re.IGNORECASE,
)
_POLITE_ACTION = re.compile(
    r"^(?:can|could|would|will)\s+you\s+.+",
    re.IGNORECASE,
)
_CASUAL = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|good morning|good afternoon|good evening|"
    r"how are you|what's up|whats up)[.!?\s]*$",
    re.IGNORECASE,
)


class LiveAgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    auto_route_enabled: bool = False
    require_hermes: bool = True
    hermes_enabled: bool = False
    planner_timeout_seconds: float = Field(default=6.0, ge=1.0, le=30.0)


class LiveAgentRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: LiveAgentRoute
    requested_mode: LiveAgentRequestedMode
    automatic: bool = False
    confidence: float = Field(ge=0, le=1)
    reason: str
    proposal_only: bool = True
    review_required: bool = True
    executes: bool = False
    hermes_required: bool = True


def live_agent_runtime_config() -> LiveAgentRuntimeConfig:
    return LiveAgentRuntimeConfig(
        enabled=_flag("OMNIX_LIVE_AGENT_ENABLED"),
        auto_route_enabled=_flag("OMNIX_LIVE_AGENT_AUTO_ROUTE_ENABLED"),
        require_hermes=_flag("OMNIX_LIVE_AGENT_REQUIRE_HERMES", default=True),
        hermes_enabled=_flag("HERMES_ENABLED"),
        planner_timeout_seconds=_float("OMNIX_LIVE_AGENT_TIMEOUT_SECONDS", 6.0, 1.0, 30.0),
    )


def classify_live_agent_intent(text: str) -> tuple[bool, float, str]:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return False, 0.0, "empty"
    if _CASUAL.match(normalized):
        return False, 0.98, "casual_conversation"
    if _EXPLICIT_AGENT.search(normalized):
        return True, 0.99, "explicit_agent_request"
    if _KASA_READ.search(normalized):
        return True, 0.94, "kasa_device_request"
    action = bool(_ACTION.search(normalized))
    target = bool(_ACTION_TARGET.search(normalized))
    if _POLITE_ACTION.match(normalized) and action and target:
        return True, 0.91, "polite_action_request"
    if _INFORMATIONAL.match(normalized) and not (action and target):
        return False, 0.93, "informational_request"
    if action and target:
        return True, 0.86, "action_with_target"
    if action:
        return False, 0.55, "ambiguous_action"
    return False, 0.88, "conversation"


def resolve_live_agent_route(
    *,
    content: str,
    requested_mode: LiveAgentRequestedMode = "off",
    agent_mode: bool = False,
    user_turn_id: str | None = None,
    speech_segment_id: str | None = None,
    config: LiveAgentRuntimeConfig | None = None,
) -> LiveAgentRouteDecision:
    runtime = config or live_agent_runtime_config()
    explicit = agent_mode or requested_mode == "agent"
    is_live_voice = _is_live_voice_turn(user_turn_id, speech_segment_id)

    if explicit:
        return LiveAgentRouteDecision(
            route="agent_plan",
            requested_mode="agent",
            automatic=False,
            confidence=1.0,
            reason="explicit_agent_mode",
            hermes_required=False,
        )
    if requested_mode == "off" and not is_live_voice:
        return _direct("off", "not_live_voice", 1.0, runtime.require_hermes)
    if not runtime.enabled:
        return _direct(requested_mode, "live_agent_disabled", 1.0, runtime.require_hermes)
    if not runtime.auto_route_enabled:
        return _direct(requested_mode, "auto_route_disabled", 1.0, runtime.require_hermes)
    if not is_live_voice and requested_mode != "auto":
        return _direct(requested_mode, "auto_route_not_requested", 1.0, runtime.require_hermes)

    task, confidence, reason = classify_live_agent_intent(content)
    if not task:
        return _direct("auto", reason, confidence, runtime.require_hermes)
    if runtime.require_hermes and not runtime.hermes_enabled:
        return _direct("auto", "hermes_disabled", 1.0, runtime.require_hermes)
    return LiveAgentRouteDecision(
        route="agent_plan",
        requested_mode="auto",
        automatic=True,
        confidence=confidence,
        reason=reason,
        hermes_required=runtime.require_hermes,
    )


def _direct(
    requested_mode: LiveAgentRequestedMode,
    reason: str,
    confidence: float,
    hermes_required: bool,
) -> LiveAgentRouteDecision:
    return LiveAgentRouteDecision(
        route="direct_chat",
        requested_mode=requested_mode,
        automatic=requested_mode == "auto",
        confidence=confidence,
        reason=reason,
        proposal_only=True,
        review_required=False,
        executes=False,
        hermes_required=hermes_required,
    )


def _is_live_voice_turn(user_turn_id: str | None, speech_segment_id: str | None) -> bool:
    return str(user_turn_id or "").startswith("voice-user-turn:") or str(
        speech_segment_id or ""
    ).startswith("voice-segment:")


def _flag(name: str, default: bool = False) -> bool:
    value = environment().get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(environment().get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


class LiveAgentUnavailable(RuntimeError):
    pass


def plan_live_agent_proposal(
    *,
    content: str,
    session_id: str,
    context: dict[str, Any] | None = None,
    timeout_seconds: float = 6.0,
) -> ModeChatResponse:
    config = hermes_runtime_config()
    if not config.enabled:
        raise LiveAgentUnavailable("Hermes is disabled")
    request = AssistantRequest(
        message=content,
        session_id=session_id,
        domain=detect_mode_domain(content),
        dry_run=True,
        metadata={
            "source": "live_agent",
            "proposal_only": True,
            "review_required": True,
            "executes": False,
            **(context or {}),
        },
    )
    try:
        result = HermesAssistantPlanner(
            base_url=config.base_url,
            api_key=environment().get("HERMES_API_KEY") or None,
            timeout=min(config.timeout_seconds, timeout_seconds),
        ).plan(request)
    except Exception as exc:
        raise LiveAgentUnavailable(str(exc) or "Hermes planner is unavailable") from exc
    tools = live_agent_tools()
    has_kasa_call = any(tools.is_device_tool(call.name) for call in result.tool_calls)
    if not has_kasa_call:
        result = apply_mode_result(result, dry_run=True)
    _apply_kasa_reads(result, content=content, session_id=session_id)
    for row in result.tool_results:
        if row.name not in tools.read_tool_names:
            row.executed = False
    kasa_read_only = bool(result.tool_calls) and all(
        call.name in tools.read_tool_names for call in result.tool_calls
    )
    result.requires_confirmation = not kasa_read_only
    return ModeChatResponse(
        ok=result.success,
        mode="agent",
        backend="hermes",
        result=asdict(result),
    )


def _apply_kasa_reads(result, *, content: str, session_id: str) -> None:
    rows = list(result.tool_results)
    tools = live_agent_tools()
    for call in result.tool_calls:
        if call.name not in tools.read_tool_names:
            continue
        request = tools.read_request(call, session_id=session_id)
        if request is None:
            continue
        payload = execute_capability(CapabilityGrant("live_agent", session_id or "live-agent"), request, user_request=content)
        execution = payload.execution_result
        rows.append(
            ToolResult(
                name=call.name,
                ok=execution.error is None,
                output=execution.output,
                error=execution.error,
                executed=execution.error is None,
            )
        )
        if execution.result_summary:
            result.response = execution.result_summary
    if rows:
        result.tool_results = rows
        result.success = all(row.ok for row in rows)
