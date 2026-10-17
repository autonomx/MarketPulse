"""PHASE 5.5 — External Effect Isolation.

This module provides policy-based gating for side effects so replay/simulation
can refuse external actions deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EffectPolicy:
    """Policy controlling which side effects are allowed."""

    allow_logs: bool = True
    allow_metrics: bool = True
    allow_network: bool = False
    allow_disk_write: bool = False
    allow_live_llm: bool = False
    allow_tool_calls: bool = False
    # PHASE 5.8 — host/process/runtime leakage controls
    allow_env_read: bool = False
    allow_filesystem_read: bool = False
    allow_wall_clock: bool = False
    allow_process_spawn: bool = False


@dataclass
class EffectRecord:
    """Recorded effect attempt."""

    effect_type: str
    payload: Dict[str, Any] = field(default_factory=dict)


class EffectManager:
    """Central effect gate for replay/simulation safety."""

    def __init__(self, policy: Optional[EffectPolicy] = None):
        self.policy = policy or EffectPolicy()
        self.records: List[EffectRecord] = []

    def set_policy(self, policy: EffectPolicy) -> None:
        self.policy = policy

    def is_allowed(self, effect_type: str) -> bool:
        """Check if an effect type is allowed without raising an exception.

        This allows systems to branch cleanly instead of using try/except.

        Args:
            effect_type: The effect type to check (e.g., "log", "network", "live_llm").

        Returns:
            True if the effect type is allowed by the current policy.
        """
        return {
            "log": self.policy.allow_logs,
            "metric": self.policy.allow_metrics,
            "network": self.policy.allow_network,
            "disk_write": self.policy.allow_disk_write,
            "live_llm": self.policy.allow_live_llm,
            "tool_call": self.policy.allow_tool_calls,
            "env_read": self.policy.allow_env_read,
            "filesystem_read": self.policy.allow_filesystem_read,
            "wall_clock": self.policy.allow_wall_clock,
            "process_spawn": self.policy.allow_process_spawn,
        }.get(effect_type, False)

    def check(self, effect_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        payload = payload or {}
        self.records.append(EffectRecord(effect_type=effect_type, payload=payload))

        if not self.is_allowed(effect_type):
            raise RuntimeError(
                f"Effect '{effect_type}' is blocked by current policy."
            )

    def serialize_state(self) -> Dict[str, Any]:
        return {
            "policy": {
                "allow_logs": self.policy.allow_logs,
                "allow_metrics": self.policy.allow_metrics,
                "allow_network": self.policy.allow_network,
                "allow_disk_write": self.policy.allow_disk_write,
                "allow_live_llm": self.policy.allow_live_llm,
                "allow_tool_calls": self.policy.allow_tool_calls,
                "allow_env_read": self.policy.allow_env_read,
                "allow_filesystem_read": self.policy.allow_filesystem_read,
                "allow_wall_clock": self.policy.allow_wall_clock,
                "allow_process_spawn": self.policy.allow_process_spawn,
            },
            "records": [
                {"effect_type": r.effect_type, "payload": r.payload}
                for r in self.records
            ],
        }

    def deserialize_state(self, state: Dict[str, Any]) -> None:
        p = state.get("policy", {})
        self.policy = EffectPolicy(
            allow_logs=p.get("allow_logs", True),
            allow_metrics=p.get("allow_metrics", True),
            allow_network=p.get("allow_network", False),
            allow_disk_write=p.get("allow_disk_write", False),
            allow_live_llm=p.get("allow_live_llm", False),
            allow_tool_calls=p.get("allow_tool_calls", False),
            allow_env_read=p.get("allow_env_read", False),
            allow_filesystem_read=p.get("allow_filesystem_read", False),
            allow_wall_clock=p.get("allow_wall_clock", False),
            allow_process_spawn=p.get("allow_process_spawn", False),
        )
        self.records = [
            EffectRecord(effect_type=r["effect_type"], payload=r.get("payload", {}))
            for r in state.get("records", [])
        ]
