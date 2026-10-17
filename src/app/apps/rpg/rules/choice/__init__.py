"""Irreversible Consequence Engine - Player Choice System.

This module implements the Player Choice → Irreversible Consequence Engine
from the RPG design specification. It transforms quests from narrative flavor
into world-shaping decisions that permanently affect the game world.

Architecture:
    choice_models.py      - Data models (PlayerChoice, ConsequenceRecord, TimelineEntry)
    choice_engine.py      - Generates contextually meaningful player choices
    consequence_engine.py - Translates choices into concrete consequences
    world_mutator.py      - Applies irreversible state changes to world
    belief_updater.py     - Updates NPC beliefs and relationships
    timeline_recorder.py  - Permanent record of choices and consequences

Core Design Principle:
    Player choices → World mutation → Belief shifts → Future changes → No rollback

The system ensures that:
    - Dead factions stay dead
    - Broken alliances stay broken
    - Betrayals permanently affect trust
    - Every choice is recorded in the permanent timeline
"""

from .belief_updater import BeliefUpdater
from .choice_engine import ChoiceEngine
from .choice_models import ConsequenceRecord, PlayerChoice, TimelineEntry
from .consequence_engine import ConsequenceEngine
from .timeline_recorder import TimelineRecorder
from .world_mutator import WorldMutator

__all__ = [
    "PlayerChoice",
    "ConsequenceRecord",
    "TimelineEntry",
    "ChoiceEngine",
    "ConsequenceEngine",
    "WorldMutator",
    "BeliefUpdater",
    "TimelineRecorder",
]