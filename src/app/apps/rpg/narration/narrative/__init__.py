"""Narrative module — Story arcs, narrative events, and narrative generation.

This module implements the narrative layer from the RPG design spec:
- NarrativeEvent: Structured event representation with importance scoring
- NarrativeDirector: Converts world events to narrative events
- SceneManager: Tracks active scenes and scene context
- NarrativeGenerator: Turns events into narrative prose
- StoryArc: Story arc management
- TIER 5 Experience Orchestration:
  - AIDirector: Controls narrative tension and event shaping
  - DialogueEngine: Belief-driven dialogue generation
  - PacingController: Controls narrative length and density

Architecture:
    World Events → NarrativeDirector → NarrativeEvents → Focus Selection
                                              ↓
                                        SceneManager → Scene Context
                                              ↓
                                    NarrativeGenerator → Prose
                                              ↓
                                   AIDirector → Tension Shaping
                                   DialogueEngine → Character Dialogue
                                   PacingController → Output Control
"""

from .ai_director import AIDirector
from .dialogue_engine import DialogueEngine
from .narrative_director import NarrativeDirector
from app.apps.rpg.foundation.narrative_event import NarrativeEvent
from .narrative_generator import NarrativeGenerator
from .pacing_controller import NarrativeBeat, PacingController
from .scene_manager import Scene, SceneManager
from .story_arc import StoryArc, StoryArcManager

# Tier 14 Fix: Narrative Surface Engine for player-facing output
from .surface_engine import NarrativeSurfaceEngine

__all__ = [
    "NarrativeEvent",
    "NarrativeDirector",
    "Scene",
    "SceneManager",
    "NarrativeGenerator",
    "StoryArc",
    "StoryArcManager",
    "AIDirector",
    "DialogueEngine",
    "PacingController",
    "NarrativeBeat",
    # Tier 14
    "NarrativeSurfaceEngine",
]

# Tier 18: Narrative Director (Meta-AI Story Director)
from .arc_manager import ArcManager
from .event_injector import EventInjector
from .narrative_director_t18 import NarrativeDirector as T18NarrativeDirector
from .story_state import StoryState
from .tension_engine import TensionEngine

__all__.extend([
    "StoryState", "TensionEngine", "ArcManager", "EventInjector", "T18NarrativeDirector",
])
