"""Quest Engine for the Quest Emergence Engine.

This module provides the QuestEngine class that orchestrates
all quest system components: detection, arc building, state
management, tracking, narrative direction, and the irreversible
choice/consequence system.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.apps.rpg.rules.choice.belief_updater import BeliefUpdater

# Choice and Consequence System
from app.apps.rpg.rules.choice.choice_engine import ChoiceEngine
from app.apps.rpg.rules.choice.consequence_engine import ConsequenceEngine
from app.apps.rpg.rules.choice.timeline_recorder import TimelineRecorder
from app.apps.rpg.rules.choice.world_mutator import WorldMutator

from .quest_arc_engine import QuestArcBuilder
from .quest_detector import QuestDetector
from .quest_director import QuestDirector
from .quest_state_machine import QuestStateMachine
from .quest_templates import get_arc_type_for_quest
from .quest_tracker import QuestTracker


class QuestEngine:
    """Main quest engine orchestrating all components.

    The QuestEngine ties together quest detection, arc building,
    state management, tracking, and narrative direction into a
    unified system for processing events and evolving quest lines.

    Usage:
        engine = QuestEngine()
        result = engine.process_event(event, world_state)
    """

    def __init__(self, max_active_quests: int = 10):
        """Initialize the QuestEngine.

        Args:
            max_active_quests: Maximum number of concurrent active quests.
        """
        self.detector = QuestDetector()
        self.tracker = QuestTracker(max_active=max_active_quests)
        self.arc_builder = QuestArcBuilder()
        self.state_machine = QuestStateMachine()
        self.director = QuestDirector()
        self.world_effects_callbacks: Dict[str, Any] = {}

        # Choice and Consequence System (from rpg-design.txt)
        self.choice_engine = ChoiceEngine()
        self.consequence_engine = ConsequenceEngine()
        self.world_mutator = WorldMutator()
        self.belief_updater = BeliefUpdater()
        self.timeline = TimelineRecorder()

        # 🔥 Global Consequence Bus - enables cross-quest impact
        self.global_consequences: List[Dict[str, Any]] = []

    def process_event(
        self,
        event: Dict[str, Any],
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process an event through the quest system.

        Detects if the event triggers a new quest, builds the quest
        arc if needed, and advances all active quests based on the event.
        Also applies global consequences for cross-quest impact and
        filters events that violate irreversible constraints.

        Args:
            event: Event dict with type and optional importance.
            world_state: Current world state dict.

        Returns:
            Dict with:
            - "new_quest": New quest if created, None otherwise
            - "active_quests": Current list of active quests
            - "world_effects": List of world effects applied this tick
            - "blocked": True if event was blocked by irreversible constraints
        """
        new_quest = None
        world_effects_applied: List[str] = []

        # 🔥 Hard Constraint Layer: filter events that violate irreversible history
        filtered_event = self.state_machine.filter_invalid_events(event, world_state)
        if filtered_event is None:
            return {
                "new_quest": None,
                "active_quests": self.tracker.get_active_quests(),
                "world_effects": [],
                "blocked": True,
                "reason": "Event blocked by irreversible constraints",
            }

        # Check if event should create a new quest (using filtered event)
        detected_quest = self.detector.detect(filtered_event, world_state)

        if detected_quest:
            # Determine arc type and build quest arc
            arc_type = get_arc_type_for_quest(detected_quest.type)
            quest = self.arc_builder.build_arc(filtered_event, arc_type)
            quest.type = detected_quest.type  # Keep original quest type
            quest.title = detected_quest.title
            quest.description = detected_quest.description

            self.tracker.add(quest)
            new_quest = quest

        # 👉 Cross-Quest Impact: Apply global consequences to all active quests
        for quest in self.tracker.get_active_quests():
            if self.global_consequences:
                self.state_machine.apply_global_effects(
                    quest,
                    self.global_consequences,
                    world_state,
                )

        # Advance all active quests based on event
        for quest in self.tracker.get_active_quests():
            old_stage_index = quest.current_stage_index

            self.state_machine.advance(quest, filtered_event, world_state)

            # Track world effects from stage transitions
            if quest.current_stage_index > old_stage_index:
                completed_stage = quest.stages[old_stage_index]
                for effect_key in completed_stage.world_effects:
                    world_effects_applied.append(effect_key)

                # Register world effect callbacks
                self._apply_world_effects(completed_stage.world_effects, world_state)

        return {
            "new_quest": new_quest,
            "active_quests": self.tracker.get_active_quests(),
            "world_effects": world_effects_applied,
            "blocked": False,
        }

    def register_world_effect_callback(
        self,
        effect_key: str,
        callback: Any,
    ) -> None:
        """Register a callback for world effect changes.

        Args:
            effect_key: World state key to trigger callback on.
            callback: Function(world_state, value) to call.
        """
        self.world_effects_callbacks[effect_key] = callback

    def _apply_world_effects(
        self,
        effects: Dict[str, Any],
        world_state: Dict[str, Any],
    ) -> None:
        """Apply world effects and call registered callbacks.

        Args:
            effects: World effects dict to apply.
            world_state: Current world state dict.
        """
        for key, value in effects.items():
            if key in self.world_effects_callbacks:
                self.world_effects_callbacks[key](world_state, value)

    def get_quest_status(self, quest_id: str) -> Optional[Dict[str, Any]]:
        """Get formatted status for a specific quest.

        Args:
            quest_id: Quest ID to query.

        Returns:
            Quest status dict or None if quest not found.
        """
        quest = self.tracker.get_quest(quest_id)
        if quest:
            return self.director.generate_summary(quest)
        return None

    def get_quest_description(self, quest_id: str) -> Optional[str]:
        """Get human-readable description for a quest.

        Args:
            quest_id: Quest ID to describe.

        Returns:
            Quest description string or None if not found.
        """
        quest = self.tracker.get_quest(quest_id)
        if quest:
            return self.director.generate_description(quest)
        return None

    def complete_quest(self, quest_id: str) -> Optional[Any]:
        """Manually complete a quest.

        Args:
            quest_id: Quest ID to complete.

        Returns:
            Completed quest or None if not found.
        """
        return self.tracker.complete(quest_id)

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics.

        Returns:
            Dict with tracker stats and system info.
        """
        stats = self.tracker.get_stats()
        stats["registered_world_effects"] = list(self.world_effects_callbacks.keys())
        return stats

    def reset(self) -> None:
        """Reset all quest state.

        Clears all active, completed, and failed quests.
        """
        self.tracker.active_quests.clear()
        self.tracker.completed_quests.clear()
        self.tracker.failed_quests.clear()

    # ==================== Choice and Consequence Methods ====================

    def generate_choices(
        self,
        quest_id: str,
        world_state: Dict[str, Any],
        use_context: bool = True,
    ) -> Optional[Any]:
        """Generate choices for a quest at its current stage.

        When use_context is True, injects past decisions, belief system,
        and world state into choice generation for situational and
        meaningful choices.

        Args:
            quest_id: Quest ID to generate choices for.
            world_state: Current world state dict.
            use_context: If True, inject history, beliefs, and global flags.

        Returns:
            PlayerChoice object with generated options, or None.
        """
        quest = self.tracker.get_quest(quest_id)
        if not quest:
            return None

        # 🔥 Context-Aware Choice Generation
        if use_context:
            return self.choice_engine.generate_choices(
                quest,
                world_state,
                quest_id,
                history=quest.history,
                beliefs=world_state.get("beliefs"),
                global_flags=world_state.get("history_flags"),
            )

        return self.choice_engine.generate_choices(quest, world_state, quest_id)

    def resolve_choice(
        self,
        choice: Any,
        quest_id: str,
        world_state: Dict[str, Any],
        memory: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve a player choice and apply irreversible consequences.

        This is the core method of the Irreversible Consequence Engine.
        It generates consequences from the choice, applies them to
        the world state and memory, records everything permanently,
        and feeds consequences back into the quest system so quests
        react to player decisions.

        Args:
            choice: PlayerChoice with selected option.
            quest_id: Quest ID this choice belongs to.
            world_state: World state dict to mutate.
            memory: Optional NPC memory/belief state.

        Returns:
            Dict with:
            - "effects": List of applied effect descriptions
            - "narrative_events": List of narrative events for the surface engine
            - "consequences": Raw consequence records

        Raises:
            ValueError: If choice is not resolved or quest not found.
        """
        quest = self.tracker.get_quest(quest_id)
        if not quest:
            raise ValueError(f"Quest '{quest_id}' not found")
        if not choice.resolved or not choice.selected_option:
            raise ValueError("Choice must be resolved with a selected option")

        # Generate consequences from the choice
        consequences = self.consequence_engine.apply(choice, quest, world_state, memory)

        if not consequences:
            return {"effects": [], "narrative_events": [], "consequences": []}

        # Apply consequences to world state and memory
        applied_effects: List[Dict[str, Any]] = []
        narrative_events: List[Dict[str, Any]] = []

        for c in consequences:
            if c.consequence_type == "faction_power_shift":
                effect = self.world_mutator.shift_faction_power(
                    world_state,
                    c.data.get("actor", ""),
                    c.data.get("target", ""),
                    c.data.get("delta", 0.0),
                )
                applied_effects.append(effect)
                c.applied = True

                # 🔥 Emit narrative event for surface engine
                narrative_events.append({
                    "type": "narrative",
                    "event": "power_shift",
                    "actor": c.data.get("actor", ""),
                    "target": c.data.get("target", ""),
                    "delta": c.data.get("delta", 0.0),
                })

            elif c.consequence_type == "belief_update":
                # Ensure memory dict exists before applying belief update
                if memory is not None:
                    effect = self.belief_updater.apply(
                        memory,
                        c.data.get("actor", ""),
                        c.data.get("target", ""),
                        c.data.get("delta", 0.0),
                    )
                    applied_effects.append(effect)
                    c.applied = True

                    # Emit narrative event
                    narrative_events.append({
                        "type": "narrative",
                        "event": "belief_update",
                        "actor": c.data.get("actor", ""),
                        "target": c.data.get("target", ""),
                        "delta": c.data.get("delta", 0.0),
                    })

            elif c.consequence_type == "world_state_change":
                effect = self.world_mutator.change_world_state(
                    world_state,
                    c.data.get("key", ""),
                    c.data.get("delta", 0.0),
                )
                applied_effects.append(effect)
                c.applied = True

                narrative_events.append({
                    "type": "narrative",
                    "event": "world_change",
                    "key": c.data.get("key", ""),
                    "delta": c.data.get("delta", 0.0),
                })

            elif c.consequence_type == "tag_add":
                effect = self.world_mutator.add_world_tag(
                    world_state,
                    c.data.get("tag", ""),
                )
                applied_effects.append(effect)
                c.applied = True

                narrative_events.append({
                    "type": "narrative",
                    "event": "tag_added",
                    "tag": c.data.get("tag", ""),
                })

        # 🔥 FEED CONSEQUENCES BACK INTO QUEST SYSTEM
        # Convert ConsequenceRecord objects to dicts for the state machine
        consequence_dicts = [{"consequence_type": c.consequence_type, "data": c.data} for c in consequences]
        self.state_machine.apply_consequences(quest, consequence_dicts, world_state)

        # 🔥 Add to global consequence bus (cross-quest impact)
        self.global_consequences.extend(consequence_dicts)

        # Record in timeline (permanent, cannot be undone)
        self.timeline.record(world_state, choice, consequences)

        return {
            "effects": applied_effects,
            "narrative_events": narrative_events,
            "consequences": consequence_dicts,
        }

    def check_irreversible(self, world_state: Dict[str, Any], tag: str) -> bool:
        """Check if an irreversible flag exists in world history.

        Args:
            world_state: World state dict.
            tag: Tag to check (e.g., "faction_destroyed").

        Returns:
            True if the tag exists in history.
        """
        return self.world_mutator.check_irreversible(world_state, tag)

    def get_world_summary(self, world_state: Dict[str, Any]) -> Dict[str, Any]:
        """Get a summary of world state including choice history.

        Args:
            world_state: World state dict.

        Returns:
            Summary dict with world and timeline information.
        """
        return {
            "world": self.world_mutator.get_world_summary(world_state),
            "timeline": self.timeline.get_summary(),
        }
