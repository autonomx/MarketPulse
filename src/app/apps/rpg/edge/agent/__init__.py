"""RPG Agent System — TIER 10: Autonomous NPC Agent System.

This module implements autonomous NPC agents that can:
- Perceive the world and events
- Reason about goals
- Plan multi-step actions
- Act in the world
- Adapt based on outcomes

Architecture:
    Agent Brain (decision-making)
         ↓
    Planner (multi-step plans)
         ↓
    Action Executor (world mutation via events)
         ↓
    Agent Scheduler (who acts each tick)
         ↓
    Feedback Loop (success/failure → belief updates)

Usage:
    agents = AgentSystem()
    events = agents.update(characters, world_state)
"""

from __future__ import annotations

from .action_executor import ActionExecutor
from .agent_brain import AgentBrain
from .agent_scheduler import AgentScheduler
from .agent_system import AgentSystem
from .planner import Plan, Planner

__all__ = [
    "AgentBrain",
    "Plan",
    "Planner",
    "ActionExecutor",
    "AgentScheduler",
    "AgentSystem",
]