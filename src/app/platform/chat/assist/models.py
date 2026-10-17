"""Assist mode requests, results, tool results, confirmations and the action log."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.capabilities.tool_call import ToolCall, ToolRiskLevel


@dataclass
class ToolResult:
    name: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    executed: bool = False


@dataclass
class AssistantRequest:
    message: str
    session_id: str = "default"
    domain: str = "chat"
    dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantResult:
    success: bool
    response: str
    domain: str = "chat"
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_id: str | None = None
    trace_id: str | None = None
    error: str | None = None


@dataclass
class PolicyDecision:
    allowed: bool
    risk: ToolRiskLevel
    requires_confirmation: bool = False
    reason: str = ""


@dataclass
class ConfirmationRequest:
    confirmation_id: str
    tool_call: ToolCall
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    status: Literal["pending", "approved", "rejected"] = "pending"


@dataclass
class ActionLogEntry:
    trace_id: str
    action: str
    domain: str
    dry_run: bool
    success: bool
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
