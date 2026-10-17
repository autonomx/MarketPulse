"""Neutral contracts for declarative tool calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SIMULATION_TRUTH = "simulation_truth"


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    risk: ToolRiskLevel = ToolRiskLevel.LOW
    reason: str = ""
