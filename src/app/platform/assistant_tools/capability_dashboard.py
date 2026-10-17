"""Unified assistant capability dashboard projection."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .config_store import AssistantToolsConfigPayload, default_assistant_tools_config, load_assistant_tools_config
from .ledger import AssistantToolLedgerPayload, load_assistant_tool_ledger
from .registry import assistant_tool_registry_payload


class AssistantCapabilityStatus(BaseModel):
    tool_id: str
    name: str
    enabled: bool = False
    connection_status: str = "not_configured"
    action_count: int = 0
    enabled_action_count: int = 0
    recent_execution_count: int = 0
    recent_error_count: int = 0


class AssistantCapabilityDashboard(BaseModel):
    tools: list[AssistantCapabilityStatus] = Field(default_factory=list)
    total_tools: int = 0
    enabled_tools: int = 0
    recent_execution_count: int = 0
    recent_error_count: int = 0


def build_assistant_capability_dashboard(
    *,
    config: AssistantToolsConfigPayload | None = None,
    ledger: AssistantToolLedgerPayload | None = None,
) -> AssistantCapabilityDashboard:
    registry = assistant_tool_registry_payload()
    config_payload = config or load_assistant_tools_config()
    ledger_payload = ledger or load_assistant_tool_ledger(limit=100)
    fallback = default_assistant_tools_config()
    statuses: list[AssistantCapabilityStatus] = []
    for tool in registry.tools:
        tool_config = next((record for record in config_payload.tools if record.tool_id == tool.id), None)
        if tool_config is None:
            tool_config = next(record for record in fallback.tools if record.tool_id == tool.id)
        entries = [entry for entry in ledger_payload.entries if entry.tool_id == tool.id]
        statuses.append(
            AssistantCapabilityStatus(
                tool_id=tool.id,
                name=tool.name,
                enabled=tool_config.enabled,
                connection_status=tool_config.connection_status,
                action_count=len(tool.actions),
                enabled_action_count=sum(1 for action in tool_config.actions if action.enabled),
                recent_execution_count=len(entries),
                recent_error_count=sum(1 for entry in entries if entry.error),
            )
        )
    return AssistantCapabilityDashboard(
        tools=statuses,
        total_tools=len(statuses),
        enabled_tools=sum(1 for status in statuses if status.enabled),
        recent_execution_count=sum(status.recent_execution_count for status in statuses),
        recent_error_count=sum(status.recent_error_count for status in statuses),
    )
