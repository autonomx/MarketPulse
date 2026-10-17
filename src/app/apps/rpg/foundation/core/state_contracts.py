"""PHASE 5.5 — Deterministic State Boundary Contracts.

This module defines explicit protocols for stateful subsystems so that:
- replay can reconstruct subsystem state deterministically
- snapshots can capture full engine state boundaries
- simulation sandboxes can isolate mutations cleanly
"""

from __future__ import annotations

from typing import Any, Dict, Protocol


class SerializableState(Protocol):
    """Protocol for subsystems with explicit serializable state."""

    def serialize_state(self) -> Dict[str, Any]:
        """Return deterministic subsystem state."""
        ...

    def deserialize_state(self, state: Dict[str, Any]) -> None:
        """Restore subsystem state from serialized representation."""
        ...


class ReplaySafe(Protocol):
    """Protocol for subsystems that support replay/live mode switching."""

    def set_mode(self, mode: str) -> None:
        """Set subsystem mode to 'live', 'replay', or 'simulation'."""
        ...


class EffectAware(Protocol):
    """Protocol for subsystems that can receive an effect manager."""

    def set_effect_manager(self, effect_manager: Any) -> None:
        """Inject effect policy manager into subsystem."""
        ...


class LLMRecorderAware(Protocol):
    """Protocol for subsystems that can receive an LLM recorder."""

    def set_llm_recorder(self, recorder: Any) -> None:
        """Inject LLM recorder for deterministic replay."""
        ...


class ToolRuntimeRecorderAware(Protocol):
    """Protocol for subsystems that can receive a tool/runtime recorder."""

    def set_tool_runtime_recorder(self, recorder: Any) -> None:
        """Inject tool/runtime recorder for deterministic replay."""
        ...


class HostRuntimeRecorderAware(Protocol):
    """Protocol for subsystems that can receive a host/runtime recorder."""

    def set_host_runtime_recorder(self, recorder: Any) -> None:
        """Inject host/runtime recorder for deterministic replay."""
        ...
