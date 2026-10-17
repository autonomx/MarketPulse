"""The single entry point for executing a capability (WP-4.5).

Every caller (agent broker, task graph, workflow, chat, live agent, tool
proposals) describes its authority as a ``CapabilityGrant`` and calls
``execute_capability``. The assistant-tools feature contributes the runtime
(``CAPABILITY_RUNTIME``, an at-most-one port) that reviews the request
against the current tool policy, dispatches to an adapter from a fail-closed
registry and records the ledger entry. Without that feature, execution fails
closed.

A grant is approved only when ``approved_by`` names the principal whose
recorded decision approved this exact call. Callers never pass a bare
``approved=True``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

from app.runtime.ports import Port, optional

GrantSource = Literal["agent_run", "task_graph", "workflow", "chat", "live_agent", "tool_proposal"]
ApprovalFloor = Literal["allow_automatic", "ask_sensitive", "always_ask", "disabled"]

class CapabilityRuntimeUnavailable(RuntimeError):
    """No capability runtime is installed (the assistant-tools feature is off)."""


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Authority for one capability call.

    ``subject_id`` identifies what the call belongs to (run, session or
    workflow). ``approved_by`` is the principal user id that approved this
    call, or ``None`` when only the tool policy may allow it.
    ``policy_floor`` can only tighten the configured approval policy.
    """

    source: GrantSource
    subject_id: str
    approved_by: str | None = None
    policy_floor: ApprovalFloor | None = None

    def __post_init__(self) -> None:
        if self.source not in get_args(GrantSource):
            raise ValueError(f"unknown grant source {self.source!r}")
        if not str(self.subject_id).strip():
            raise ValueError("grant subject is required")
        if self.approved_by is not None and not str(self.approved_by).strip():
            raise ValueError("approved_by must name a principal")
        if self.policy_floor is not None and self.policy_floor not in get_args(ApprovalFloor):
            raise ValueError(f"unknown approval floor {self.policy_floor!r}")

    @property
    def approved(self) -> bool:
        return self.approved_by is not None


# Approver recorded for decisions made before approvals named their principal.
LEGACY_APPROVER = "unrecorded:before-principal-binding"


class CapabilityExecutor(Protocol):
    def __call__(self, grant: CapabilityGrant, request: Any, *, user_request: str = "") -> Any: ...


# Contributing the runtime makes tools executable; it grants nothing. The
# runtime itself enforces the grant, approvals and the tool policy.
CAPABILITY_RUNTIME: Port[CapabilityExecutor] = Port("capabilities.runtime", CapabilityExecutor, "at_most_one")


def execute_capability(grant: CapabilityGrant, request: Any, *, user_request: str = "") -> Any:
    """Execute ``request`` under ``grant`` through the composed runtime."""
    if not isinstance(grant, CapabilityGrant):
        raise TypeError("execute_capability requires a CapabilityGrant")
    runtime = optional(CAPABILITY_RUNTIME)
    if runtime is None:
        raise CapabilityRuntimeUnavailable("capability runtime is not installed (assistant-tools feature disabled)")
    return runtime(grant, request, user_request=user_request)


# The tools a live agent plans with (PA-1.3). Assistant tools implements it;
# without it the live agent plans with no tools. Planning grants nothing:
# execution still goes through ``execute_capability``.
class LiveAgentTools(Protocol):
    @property
    def read_tool_names(self) -> frozenset[str]:
        """Tool names the live agent may run without confirmation (device reads)."""

    def is_device_tool(self, name: str) -> bool:
        """Whether a planned tool call addresses a device."""

    def read_request(self, call: Any, *, session_id: str) -> Any | None:
        """The capability request a planned read runs as, or ``None``."""

    def planner_context(self) -> dict[str, str]:
        """Context the planner needs to plan tool calls (date, timezone, rules)."""

    def tool_proposals(self, *, user_request: str, session_id: str, source_message_id: str,
                       mode_result: dict[str, Any]) -> list[dict[str, Any]]:
        """Governed tool proposals for a planner result."""

    def first_pending_write(self, mode_result: dict[str, Any], *, session_id: str) -> Any | None:
        """The first planned write that waits for the user's confirmation, or ``None``."""

    def parse_request(self, raw: dict[str, Any]) -> Any:
        """A stored pending request; raises ``ValueError`` when it is not one."""


LIVE_AGENT_TOOLS: Port[LiveAgentTools] = Port("capabilities.live_agent_tools", LiveAgentTools, "at_most_one")


class _NoTools:
    read_tool_names: frozenset[str] = frozenset()

    def is_device_tool(self, name: str) -> bool:
        return False

    def read_request(self, call: Any, *, session_id: str) -> Any | None:
        return None

    def planner_context(self) -> dict[str, str]:
        return {}

    def tool_proposals(self, *, user_request: str, session_id: str, source_message_id: str,
                       mode_result: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def first_pending_write(self, mode_result: dict[str, Any], *, session_id: str) -> Any | None:
        return None

    def parse_request(self, raw: dict[str, Any]) -> Any:
        raise ValueError("assistant tools are disabled")


def live_agent_tools() -> LiveAgentTools:
    return optional(LIVE_AGENT_TOOLS) or _NoTools()


__all__ = [
    "CAPABILITY_RUNTIME",
    "LIVE_AGENT_TOOLS",
    "LiveAgentTools",
    "live_agent_tools",
    "LEGACY_APPROVER",
    "CapabilityExecutor",
    "CapabilityGrant",
    "CapabilityRuntimeUnavailable",
    "GrantSource",
    "execute_capability",
]
