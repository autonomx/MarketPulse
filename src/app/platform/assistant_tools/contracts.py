"""Assistant tool services Chat's live agent and assist mode use (WP-8.2)."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol

from app.runtime.ports import Port

from app.platform.assistant_tools.kasa_plan import (
    KASA_READ_TOOLS,
    first_pending_kasa_write,
    is_kasa_tool_name,
    kasa_request_from_tool_call,
)
from app.platform.assistant_tools.live_agent_proposals import live_agent_planner_context, live_agent_tool_proposals
from app.platform.assistant_tools.models import AssistantToolRequest, AssistantToolResult

__all__ = [
    "AGENT_RUN_WORKSPACES",
    "AgentRunWorkspaces",
    "KASA_READ_TOOLS",
    "AssistantToolRequest",
    "AssistantToolResult",
    "first_pending_kasa_write",
    "is_kasa_tool_name",
    "kasa_request_from_tool_call",
    "live_agent_planner_context",
    "live_agent_tool_proposals",
]


class AgentRunWorkspaces(Protocol):
    """An agent run's issued workspace and its preview launcher (sandboxed runs
    preview in the sandbox), or None when the run has no issued workspace."""

    def preview(self, run_id: str) -> tuple[Any, Any] | None: ...


# The agent runtime contributes this; without it run-scoped previews are unavailable.
AGENT_RUN_WORKSPACES: Port[AgentRunWorkspaces] = Port(
    "assistant_tools.agent_run_workspaces", AgentRunWorkspaces, "at_most_one",
)

# Services other modules import through this contract, loaded on first use (ADR-0016).
_LAZY_EXPORTS = {
    "review_assistant_tool_request": "gate",
    "github_repository_from_remote": "repo_adapter",
    "AssistantCapabilityDashboard": "capability_dashboard",
    "build_assistant_capability_dashboard": "capability_dashboard",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"app.platform.assistant_tools.{module}"), name)
