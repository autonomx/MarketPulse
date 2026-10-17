"""Quest Emergence Engine - Multi-stage narrative quest system.

This module implements the Quest Emergence Engine v2 from the RPG design specification:
a multi-stage narrative state machine where quests evolve reactively and impact the world.

Architecture:
    quest_models.py - Core data models (Quest, QuestStage, QuestObjective)
    quest_templates.py - Pre-defined quest arc templates (conflict, betrayal, etc.)
    quest_arc_engine.py - QuestArcBuilder for constructing quest arcs from templates
    quest_state_machine.py - QuestStateMachine for advancing quest stages
    quest_director.py - QuestDirector for narrative descriptions
    quest_detector.py - QuestDetector for discovering quests from events
    quest_tracker.py - QuestTracker for managing active/completed quests
    quest_engine.py - Main QuestEngine orchestrating all components

Key Features:
    - Act-based quests (setup → escalation → climax → resolution)
    - Reactive progression based on events and player choices
    - World-impacting effects (faction shifts, network changes)
    - Procedural story generation with memory and evolution
"""

from .quest_arc_engine import QuestArcBuilder
from .quest_detector import QuestDetector
from .quest_director import QuestDirector
from .quest_engine import QuestEngine
from .quest_models import Quest, QuestObjective, QuestStage
from .quest_state_machine import QuestStateMachine
from .quest_templates import QUEST_ARCS
from .quest_tracker import QuestTracker

__all__ = [
    "Quest",
    "QuestStage",
    "QuestObjective",
    "QUEST_ARCS",
    "QuestArcBuilder",
    "QuestStateMachine",
    "QuestDirector",
    "QuestDetector",
    "QuestTracker",
    "QuestEngine",
]