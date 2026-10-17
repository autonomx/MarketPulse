"""Phase 8.3 — Sandbox/World Simulation Depth.

Provides deterministic world-impacting outcome projection and trend
evolution for locations, factions, threads, and rumors.

Modules:
    outcome_projection: Derive structured world-impacting outcomes
    location_dynamics: Update location trend state
    thread_evolution: Update thread trend state
    faction_dynamics: Update faction trend state
    rumor_feedback: Update rumor heat feedback
    world_consequence_builder: Build world consequences from outcomes
"""

from .faction_dynamics import update_faction_trends
from .location_dynamics import update_location_trends
from .outcome_projection import project_outcomes_from_state
from .rumor_feedback import update_rumor_feedback
from .thread_evolution import update_thread_trends
from .world_consequence_builder import build_world_consequences

__all__ = [
    "project_outcomes_from_state",
    "update_location_trends",
    "update_thread_trends",
    "update_faction_trends",
    "update_rumor_feedback",
    "build_world_consequences",
]