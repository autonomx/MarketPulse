"""Canonical capability metadata and adapter registration contracts."""

from .registry import (
    Capability,
    CapabilityEffect,
    CapabilityExecutionZone,
    CapabilityRegistry,
    CapabilityRisk,
    browser_capability_ids,
    default_capability_registry,
)
from .tool_call import ToolCall, ToolRiskLevel

__all__ = [
    "Capability",
    "CapabilityEffect",
    "CapabilityExecutionZone",
    "CapabilityRegistry",
    "CapabilityRisk",
    "ToolCall",
    "ToolRiskLevel",
    "browser_capability_ids",
    "default_capability_registry",
]
