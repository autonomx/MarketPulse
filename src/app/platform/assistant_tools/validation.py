"""Pure assistant tool request validation helpers."""
from __future__ import annotations

import re
from collections.abc import Iterable

from .models import AssistantToolAction, AssistantToolRequest, AssistantToolSpec, AssistantToolValidationResult, ApprovalPolicy

_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}\.[a-z][a-z0-9_]{1,63}$")
_MCP_ACTION_ID_RE = re.compile(
    r"^mcp\.[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$"
)


def is_valid_tool_id(tool_id: str) -> bool:
    """Return True when a tool id is safe and canonical."""

    return bool(_TOOL_ID_RE.fullmatch((tool_id or "").strip()))


def is_valid_action_id(action_id: str) -> bool:
    """Return True when an action id is safe and canonical.

    Ordinary assistant actions retain the historical ``tool.action`` shape.
    Governed MCP actions get one additional, identifier-constrained server
    segment: ``mcp.server.action``.  MCP tool names themselves may contain
    punctuation; the operator policy maps those names to a canonical underscore
    capability id instead of widening this security-sensitive identifier grammar.
    """

    value = (action_id or "").strip()
    return bool(_ACTION_ID_RE.fullmatch(value) or _MCP_ACTION_ID_RE.fullmatch(value))


def action_requires_approval(action: AssistantToolAction, approval_policy: ApprovalPolicy | None = None) -> bool:
    """Return whether the action must be approved before execution."""

    policy = approval_policy or action.approval_policy
    if policy == "disabled":
        return False
    if policy == "always_ask":
        return True
    if action.is_destructive or action.category == "delete":
        return True
    if policy == "ask_sensitive":
        return action.category != "read" or action.risk_level != "low" or action.requires_confirmation or action.is_destructive
    return False


def validate_assistant_tool_request(
    request: AssistantToolRequest,
    tools: Iterable[AssistantToolSpec],
    *, approved: bool = False,
) -> AssistantToolValidationResult:
    """Validate a request against a registry snapshot before execution.

    The function is intentionally pure: it reads only the supplied request and
    tool list. Runtime adapters must call this gate before attempting side
    effects.
    """

    tool_id = request.tool_id.strip()
    action_id = request.action_id.strip()
    if not is_valid_tool_id(tool_id):
        return AssistantToolValidationResult(valid=False, reason="invalid_tool_id", tool_id=tool_id, action_id=action_id)
    if not is_valid_action_id(action_id):
        return AssistantToolValidationResult(valid=False, reason="invalid_action_id", tool_id=tool_id, action_id=action_id)
    if not action_id.startswith(f"{tool_id}."):
        return AssistantToolValidationResult(valid=False, reason="action_tool_mismatch", tool_id=tool_id, action_id=action_id)

    tool = next((candidate for candidate in tools if candidate.id == tool_id), None)
    if tool is None:
        return AssistantToolValidationResult(valid=False, reason="unknown_tool", tool_id=tool_id, action_id=action_id)
    if not tool.enabled:
        return AssistantToolValidationResult(valid=False, reason="tool_disabled", tool_id=tool_id, action_id=action_id)

    action = next((candidate for candidate in tool.actions if candidate.id == action_id), None)
    if action is None:
        return AssistantToolValidationResult(valid=False, reason="unknown_action", tool_id=tool_id, action_id=action_id)
    if not action.enabled:
        return AssistantToolValidationResult(
            valid=False,
            reason="action_disabled",
            tool_id=tool_id,
            action_id=action_id,
            risk_level=action.risk_level,
            state_changed=action.category in {"write", "delete", "execute"},
        )

    policy = action.approval_policy
    if policy == "disabled":
        return AssistantToolValidationResult(
            valid=False,
            reason="approval_policy_disabled",
            tool_id=tool_id,
            action_id=action_id,
            risk_level=action.risk_level,
            state_changed=action.category in {"write", "delete", "execute"},
        )

    approval_required = action_requires_approval(action, policy)
    return AssistantToolValidationResult(
        valid=True,
        executable=not approval_required or approved,
        approval_required=approval_required,
        reason="approval_required" if approval_required and not approved else None,
        tool_id=tool_id,
        action_id=action_id,
        risk_level=action.risk_level,
        state_changed=action.category in {"write", "delete", "execute"},
    )
