"""Assistant-tools runtime behind ``app.capabilities.executor`` (WP-4.5).

Reviews each call against the current tool policy, dispatches to an adapter
from a fail-closed registry and records the execution ledger. This module is
the only one that calls adapters; everything else goes through
``app.capabilities.executor.execute_capability``.
"""
from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

from app.capabilities.executor import CAPABILITY_RUNTIME, CapabilityGrant
from app.observability.tracing import annotate, span
from app.runtime.ports import ContributionSpec
from app.security import audit

from .browser_adapter import run_browser_tool_request
from .calendar_adapter import run_calendar_tool_request
from .contacts_adapter import run_contacts_tool_request
from .gate import review_assistant_tool_request
from .gmail_adapter import run_gmail_tool_request
from .hermes_payloads import HermesAssistantToolExecutePayload
from .home_adapter import run_home_tool_request
from .kasa_adapter import run_kasa_tool_request
from .ledger import (
    AssistantToolLedgerEntry,
    append_assistant_tool_ledger_entry,
    assistant_tool_execution_for_proposal,
    summarize_tool_input,
)
from .mcp_adapter import run_mcp_tool_request
from .models import AssistantToolRequest, AssistantToolResult, ToolRiskLevel
from .repo_adapter import run_repository_tool_request
from .result_context import tool_result_to_chat_context

CapabilityAdapter = Callable[[AssistantToolRequest], AssistantToolResult]

ADAPTERS: MappingProxyType[str, CapabilityAdapter] = MappingProxyType({
    "browser": run_browser_tool_request,
    "calendar": run_calendar_tool_request,
    "contacts": run_contacts_tool_request,
    "github": run_repository_tool_request,
    "gmail": run_gmail_tool_request,
    "home": run_home_tool_request,
    "kasa": run_kasa_tool_request,
    "mcp": run_mcp_tool_request,
})


def run_capability_adapter(
    request: AssistantToolRequest, risk_level: ToolRiskLevel, *, source: str = "tool_proposal",
) -> AssistantToolResult:
    """Dispatch an already reviewed request. An unknown tool is an error."""
    with span("capability.execute", tool_id=request.tool_id, action_id=request.action_id, source=source) as current:
        result = _dispatch(request, risk_level)
        annotate(current, {"state_changed": result.state_changed, "error": result.error})
    audit.record(
        "capability.execute",
        target_type="capability",
        target_id=request.action_id,
        outcome="failure" if result.error else "success",
        details={"source": source, "risk_level": risk_level, "state_changed": result.state_changed,
                 "error": result.error, "session_id": request.session_id},
    )
    return result


def _dispatch(request: AssistantToolRequest, risk_level: ToolRiskLevel) -> AssistantToolResult:
    adapter = ADAPTERS.get(request.tool_id) or _declared_adapter(request.tool_id)
    if adapter is None:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level=risk_level,
            state_changed=False,
            result_summary="Blocked: no adapter is registered for this tool.",
            error="unknown_adapter",
        )
    result = adapter(request)
    result.risk_level = risk_level
    return result


def _declared_adapter(tool_id: str) -> CapabilityAdapter | None:
    """A tool an enabled feature declared (ADR-0016); built-in adapters win."""
    from app.capabilities.registry import declared_tools

    tool = next((tool for tool in declared_tools() if tool.tool_id == tool_id), None)
    return tool.run if tool is not None else None


def execute_with_grant(
    grant: CapabilityGrant,
    request: AssistantToolRequest,
    *,
    user_request: str = "",
) -> HermesAssistantToolExecutePayload:
    approved = grant.approved
    decision = review_assistant_tool_request(request, approved=approved, policy_floor=grant.policy_floor)
    existing = assistant_tool_execution_for_proposal(request.proposal_id or "") if approved else None
    if existing is not None:
        result = AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level=decision.risk_level,
            state_changed=False,
            result_summary=f"This approved proposal was already executed as {existing.execution_id}.",
            output={"already_executed": True, "execution_id": existing.execution_id},
        )
        return HermesAssistantToolExecutePayload(
            user_request=user_request,
            selected_tool_id=request.tool_id,
            selected_action_id=request.action_id,
            approval_decision=decision,
            execution_result=result,
            result_context=tool_result_to_chat_context(result, existing),
            state_changed=False,
        )
    if decision.executable:
        result = run_capability_adapter(request, decision.risk_level, source=grant.source)
    else:
        result = AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level=decision.risk_level,
            state_changed=False,
            result_summary=decision.result_summary,
            error=decision.reason or "not_executable",
        )
    entry = append_assistant_tool_ledger_entry(
        AssistantToolLedgerEntry(
            session_id=request.session_id,
            proposal_id=request.proposal_id,
            tool_id=request.tool_id,
            action_id=request.action_id,
            approval_source="user" if approved else "policy",
            input_summary=summarize_tool_input(request.input),
            result_summary=result.result_summary,
            state_changed=result.state_changed,
            error=result.error,
        )
    )
    return HermesAssistantToolExecutePayload(
        user_request=user_request,
        selected_tool_id=request.tool_id,
        selected_action_id=request.action_id,
        approval_decision=decision,
        execution_result=result,
        result_context=tool_result_to_chat_context(result, entry),
        state_changed=result.state_changed,
    )


CAPABILITY_RUNTIME_CONTRIBUTION = ContributionSpec(CAPABILITY_RUNTIME, lambda _context: execute_with_grant)

__all__ = ["ADAPTERS", "CAPABILITY_RUNTIME_CONTRIBUTION", "execute_with_grant", "run_capability_adapter"]
