"""Canonical assistant tool request/result schemas.

These models define the shared backend contract used before any assistant tool can
be reviewed or executed. They intentionally do not import runtime adapters so the
validation layer stays pure and easy to test.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ToolActionCategory = Literal["read", "write", "delete", "execute"]
ToolRiskLevel = Literal["low", "medium", "high"]
ApprovalPolicy = Literal["allow_automatic", "ask_sensitive", "always_ask", "disabled"]
ConnectionStatus = Literal["not_configured", "connected", "error"]


class AssistantToolAction(BaseModel):
    """Backend-owned action definition for an assistant tool."""

    id: str
    tool_id: str
    label: str
    description: str
    category: ToolActionCategory
    risk_level: ToolRiskLevel = "low"
    enabled: bool = True
    approval_policy: ApprovalPolicy = "allow_automatic"
    requires_connection: bool = True
    requires_confirmation: bool = False
    is_destructive: bool = False


class AssistantToolConfig(BaseModel):
    """Persisted configuration overlay for one tool/action."""

    enabled: bool = False
    connection_status: ConnectionStatus = "not_configured"
    approval_policy: ApprovalPolicy | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class AssistantToolSpec(BaseModel):
    """Backend-owned assistant tool metadata and actions."""

    id: str
    name: str
    description: str
    category: str
    provider: str | None = None
    enabled: bool = False
    connection_status: ConnectionStatus = "not_configured"
    actions: list[AssistantToolAction] = Field(default_factory=list)


class AssistantToolRegistryPayload(BaseModel):
    """Serializable backend mirror of assistant tools and actions."""

    tools: list[AssistantToolSpec]
    actions: list[AssistantToolAction]


class AssistantToolRequest(BaseModel):
    """Canonical request envelope required before any tool action can run."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str
    action_id: str
    session_id: str | None = None
    proposal_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class AssistantToolReviewDecision(BaseModel):
    """Approval-gate decision for an assistant tool request."""

    tool_id: str
    action_id: str
    session_id: str | None = None
    allowed: bool = False
    executable: bool = False
    approval_required: bool = False
    risk_level: ToolRiskLevel = "low"
    state_changed: bool = False
    reason: str | None = None
    result_summary: str = ""


class AssistantToolResult(BaseModel):
    """Canonical result envelope returned by assistant tool execution."""

    tool_id: str
    action_id: str
    session_id: str | None = None
    risk_level: ToolRiskLevel = "low"
    state_changed: bool = False
    result_summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AssistantToolValidationResult(BaseModel):
    """Pure validation decision for a request against a registry/config snapshot."""

    valid: bool
    executable: bool = False
    approval_required: bool = False
    risk_level: ToolRiskLevel = "low"
    state_changed: bool = False
    reason: str | None = None
    tool_id: str | None = None
    action_id: str | None = None
