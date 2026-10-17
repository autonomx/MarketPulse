"""Backend projection of the canonical Omnix capability registry."""
from __future__ import annotations

from app.capabilities.registry import declared_tools

from collections import defaultdict

from app.capabilities import Capability, default_capability_registry

from .models import (
    ApprovalPolicy,
    AssistantToolAction,
    AssistantToolRegistryPayload,
    AssistantToolSpec,
    ToolActionCategory,
    ToolRiskLevel,
)
from .validation import is_valid_action_id, is_valid_tool_id


def default_approval_policy_for_action(
    *,
    category: ToolActionCategory,
    risk_level: ToolRiskLevel,
    requires_confirmation: bool = False,
    is_destructive: bool = False,
) -> ApprovalPolicy:
    if is_destructive or category == "delete":
        return "always_ask"
    if requires_confirmation or risk_level == "high":
        return "always_ask"
    if category in {"write", "execute"} or risk_level == "medium":
        return "ask_sensitive"
    return "allow_automatic"


def tool_action(
    *,
    tool_id: str,
    action_id: str,
    label: str,
    description: str,
    category: ToolActionCategory,
    risk_level: ToolRiskLevel = "low",
    enabled: bool = True,
    requires_connection: bool = True,
    requires_confirmation: bool = False,
    is_destructive: bool = False,
    approval_policy: ApprovalPolicy | None = None,
) -> AssistantToolAction:
    if not is_valid_tool_id(tool_id):
        raise ValueError(f"invalid tool id: {tool_id}")
    if not is_valid_action_id(action_id):
        raise ValueError(f"invalid action id: {action_id}")
    if not action_id.startswith(f"{tool_id}."):
        raise ValueError(f"action {action_id} does not belong to tool {tool_id}")
    return AssistantToolAction(
        id=action_id,
        tool_id=tool_id,
        label=label,
        description=description,
        category=category,
        risk_level=risk_level,
        enabled=enabled,
        approval_policy=approval_policy
        or default_approval_policy_for_action(
            category=category,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            is_destructive=is_destructive,
        ),
        requires_connection=requires_connection,
        requires_confirmation=requires_confirmation,
        is_destructive=is_destructive,
    )


def _action_category(capability: Capability) -> ToolActionCategory:
    if capability.effect == "read":
        return "read"
    if capability.effect == "delete":
        return "delete"
    if capability.effect == "execute":
        return "execute"
    return "write"


def default_assistant_tools() -> list[AssistantToolSpec]:
    """Project browser-facing assistant tools from the canonical registry."""

    grouped: dict[str, list[Capability]] = defaultdict(list)
    for capability in default_capability_registry().assistant_projection():
        grouped[capability.namespace].append(capability)

    tools: list[AssistantToolSpec] = []
    for tool_id, capabilities in grouped.items():
        first = capabilities[0]
        declared = next((tool for tool in declared_tools() if tool.tool_id == tool_id), None)
        actions = [
            tool_action(
                tool_id=tool_id,
                action_id=capability.id,
                label=capability.name,
                description=capability.description,
                category=_action_category(capability),
                risk_level=capability.risk,
                enabled=capability.enabled,
                requires_connection=capability.requires_connection,
                requires_confirmation=capability.requires_confirmation,
                is_destructive=capability.destructive,
                approval_policy=capability.approval_policy,
            )
            for capability in capabilities
        ]
        tools.append(
            AssistantToolSpec(
                id=tool_id,
                name={
                    "gmail": "Gmail",
                    "calendar": "Google Calendar",
                    "contacts": "Google Contacts",
                    "github": "GitHub",
                    "kasa": "TP-Link Kasa",
                }.get(tool_id, declared.display_name if declared else tool_id.replace("_", " ").title()),
                description={
                    "gmail": "Read, draft, send, and delete Gmail messages with approval controls.",
                    "calendar": "Read availability and manage calendar events.",
                    "contacts": "Resolve contacts for email and calendar workflows.",
                    "github": "Read repositories, manage pull requests, inspect CI, and perform governed repo actions.",
                    "kasa": "Discover, inspect, and control approved Kasa smart plugs on the local network.",
                }.get(tool_id, declared.description if declared else f"Governed {tool_id} capabilities."),
                category=first.category,
                provider=first.provider,
                actions=actions,
            )
        )
    return tools


def assistant_tool_registry_payload(
    tools: list[AssistantToolSpec] | None = None,
) -> AssistantToolRegistryPayload:
    registry_tools = tools or default_assistant_tools()
    return AssistantToolRegistryPayload(
        tools=registry_tools,
        actions=[action for tool in registry_tools for action in tool.actions],
    )


def get_registered_tool(
    tool_id: str,
    tools: list[AssistantToolSpec] | None = None,
) -> AssistantToolSpec | None:
    return next((tool for tool in (tools or default_assistant_tools()) if tool.id == tool_id), None)


def get_registered_action(
    action_id: str,
    tools: list[AssistantToolSpec] | None = None,
) -> AssistantToolAction | None:
    return next(
        (action for tool in (tools or default_assistant_tools()) for action in tool.actions if action.id == action_id),
        None,
    )
