"""Hermes-compatible review gate for assistant tool requests."""
from __future__ import annotations

from collections.abc import Iterable

from .config_store import AssistantToolsConfigPayload, default_assistant_tools_config, load_assistant_tools_config
from .models import AssistantToolAction, AssistantToolReviewDecision, AssistantToolRequest, AssistantToolSpec, ApprovalPolicy
from .registry import default_assistant_tools
from .validation import action_requires_approval, is_valid_action_id, is_valid_tool_id


def review_assistant_tool_request(
    request: AssistantToolRequest,
    *,
    config: AssistantToolsConfigPayload | None = None,
    tools: Iterable[AssistantToolSpec] | None = None,
    approved: bool = False,
    policy_floor: ApprovalPolicy | None = None,
) -> AssistantToolReviewDecision:
    """Review a tool request before execution.

    This gate is pure when supplied a config payload and registry snapshot. Route
    handlers may omit config to read persisted assistant tool settings.
    """

    registry = list(tools or default_assistant_tools())
    config_payload = config or load_assistant_tools_config()
    tool_id = request.tool_id.strip()
    action_id = request.action_id.strip()
    base = {"tool_id": tool_id, "action_id": action_id, "session_id": request.session_id}

    if not is_valid_tool_id(tool_id):
        return AssistantToolReviewDecision(**base, reason="invalid_tool_id", result_summary="Blocked: invalid tool id.")
    if not is_valid_action_id(action_id):
        return AssistantToolReviewDecision(**base, reason="invalid_action_id", result_summary="Blocked: invalid action id.")
    if not action_id.startswith(f"{tool_id}."):
        return AssistantToolReviewDecision(**base, reason="action_tool_mismatch", result_summary="Blocked: action does not belong to tool.")

    tool = next((candidate for candidate in registry if candidate.id == tool_id), None)
    if tool is None:
        return AssistantToolReviewDecision(**base, reason="unknown_tool", result_summary="Blocked: unknown tool.")
    action = next((candidate for candidate in tool.actions if candidate.id == action_id), None)
    if action is None:
        return AssistantToolReviewDecision(**base, reason="unknown_action", result_summary="Blocked: unknown action.")

    state_changed = action.category in {"write", "delete", "execute"}
    base.update({"risk_level": action.risk_level, "state_changed": state_changed})
    tool_config = _tool_config(config_payload, tool_id)
    action_config = _action_config(config_payload, tool_id, action_id)
    policy = (action_config.get("approval_policy") if action_config else None) or action.approval_policy
    if policy_floor is not None:
        strength = {"allow_automatic": 0, "ask_sensitive": 1, "always_ask": 2, "disabled": 3}
        policy = max((policy, policy_floor), key=strength.__getitem__)

    if not action.enabled:
        return AssistantToolReviewDecision(**base, reason="action_disabled", result_summary="Blocked: action is disabled by registry.")
    if not tool_config.get("enabled", False):
        return AssistantToolReviewDecision(**base, reason="tool_disabled", result_summary="Blocked: tool is disabled.")
    if action_config and not action_config.get("enabled", True):
        return AssistantToolReviewDecision(**base, reason="action_disabled", result_summary="Blocked: action is disabled.")
    if policy == "disabled":
        return AssistantToolReviewDecision(**base, reason="approval_policy_disabled", result_summary="Blocked: approval policy disables action.")
    if action.requires_connection and tool_config.get("connection_status") != "connected":
        return AssistantToolReviewDecision(**base, reason="missing_connection", result_summary="Blocked: tool connection is not available.")

    approval_required = action_requires_approval(action, policy)
    executable = not approval_required or approved
    return AssistantToolReviewDecision(
        **base,
        allowed=True,
        executable=executable,
        approval_required=approval_required,
        reason="approval_required" if approval_required and not approved else None,
        result_summary=_summary(action, approval_required, executable),
    )


def _summary(action: AssistantToolAction, approval_required: bool, executable: bool) -> str:
    if approval_required and not executable:
        return f"Review required before {action.label}."
    if approval_required:
        return f"Approved and ready to run {action.label}."
    return f"Ready to run {action.label}."


def _tool_config(payload: AssistantToolsConfigPayload, tool_id: str) -> dict[str, object]:
    fallback = default_assistant_tools_config()
    record = next((tool for tool in payload.tools if tool.tool_id == tool_id), None)
    if record is None:
        record = next(tool for tool in fallback.tools if tool.tool_id == tool_id)
    return record.model_dump()


def _action_config(payload: AssistantToolsConfigPayload, tool_id: str, action_id: str) -> dict[str, object] | None:
    tool_config = next((tool for tool in payload.tools if tool.tool_id == tool_id), None)
    if tool_config is None:
        tool_config = next((tool for tool in default_assistant_tools_config().tools if tool.tool_id == tool_id), None)
    if tool_config is None:
        return None
    record = next((action for action in tool_config.actions if action.action_id == action_id), None)
    return record.model_dump() if record is not None else None
