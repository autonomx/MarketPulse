"""The agent runtime's view of capability requests and results (WP-8.2).

The one place ``app.platform.agent_runtime`` depends on ``app.platform.assistant_tools``
request models; the broker, chat lanes, TaskGraph and workflows import them
from here.
"""
from __future__ import annotations

from typing import Any, Callable

from app.platform.assistant_tools.contracts import AssistantToolRequest, AssistantToolResult
from app.capabilities.executor import CapabilityGrant, execute_capability

# A capability node's executor: (session id, request) -> tool result.
CapabilityExecutor = Callable[[str, AssistantToolRequest], Any]


def default_capability_executor(session_id: str, request: AssistantToolRequest) -> Any:
    # Capability nodes are never pre-approved: the tool policy decides, and a
    # call that needs approval fails the node (WP-4.5).
    return execute_capability(CapabilityGrant("task_graph", session_id), request, user_request=session_id)


__all__ = ["AssistantToolRequest", "AssistantToolResult", "CapabilityExecutor", "default_capability_executor"]
