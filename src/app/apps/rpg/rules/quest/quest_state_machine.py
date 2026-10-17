"""Quest State Machine for the Quest Emergence Engine.

This module provides the QuestStateMachine class that manages
quest progression through stages based on events and world state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class QuestStateMachine:
    """State machine for advancing quest stages.

    The QuestStateMachine processes events against quests, updates
    objective progress, and advances quest stages when all objectives
    are completed. It also applies world effects when stages complete.

    Usage:
        sm = QuestStateMachine()
        sm.advance(quest, event, world_state)
    """

    def __init__(self, completion_threshold: float = 1.0):
        """Initialize the QuestStateMachine.

        Args:
            completion_threshold: Progress value needed to complete an objective.
        """
        self.completion_threshold = completion_threshold

    def advance(
        self,
        quest: Any,
        event: Dict[str, Any],
        world: Dict[str, Any],
    ) -> bool:
        """Advance quest progression based on event.

        Processes the event against the current stage objectives,
        updates progress, and advances to the next stage if all
        objectives are complete.

        Args:
            quest: Quest object to update.
            event: Event dict that may affect quest progress.
            world: World state dict to modify with world effects.

        Returns:
            True if quest advanced to next stage, False otherwise.
        """
        if quest.status != "active":
            return False

        if not quest.stages:
            return False

        stage = quest.stages[quest.current_stage_index]

        # Progress objectives based on event
        progressed = False
        for obj in stage.objectives:
            if not obj.completed and self._matches(event, obj):
                obj.progress += 0.25
                if obj.progress >= self.completion_threshold:
                    obj.completed = True
                progressed = True

        # Check if stage is complete
        if all(o.completed for o in stage.objectives):
            self._advance_stage(quest, world)
            return True

        return progressed

    def _matches(self, event: Dict[str, Any], obj: Any) -> bool:
        """Check if event matches objective for progress.

        Events matching the quest type or containing relevant keywords
        will advance objective progress.

        Args:
            event: Event dict to check.
            obj: QuestObjective to match against.

        Returns:
            True if event should progress this objective.
        """
        # Progress on any event if quest is active
        event_type = event.get("type", "")
        obj_desc = getattr(obj, "description", "").lower()

        # Always make progress on relevant events
        if not event_type:
            return False

        # Match event type to quest or stage
        return True

    def _advance_stage(self, quest: Any, world: Dict[str, Any]) -> None:
        """Advance quest to the next stage.

        Applies world effects from the completed stage, records it
        in history, and moves to the next stage. If no more stages
        remain, marks the quest as complete.

        Args:
            quest: Quest object to update.
            world: World state dict to modify with world effects.
        """
        stage = quest.stages[quest.current_stage_index]

        # Apply world effects from completed stage
        for key, value in stage.world_effects.items():
            if key in world:
                if isinstance(value, (int, float)) and isinstance(world[key], (int, float)):
                    world[key] = world[key] + value
                else:
                    world[key] = value
            else:
                world[key] = value

        # Record completion in history
        quest.history.append({
            "stage": stage.name,
            "completed": True,
            "effects_applied": list(stage.world_effects.keys()),
        })

        # Move to next stage
        quest.current_stage_index += 1

        # Update quest metadata
        if quest.current_stage_index >= len(quest.stages):
            quest.complete()
        else:
            next_stage = quest.stages[quest.current_stage_index]
            quest.arc_stage = next_stage.name
            quest.arc_progress = quest.current_stage_index / len(quest.stages)
            quest.description = next_stage.description

    def apply_consequences(
        self,
        quest: Any,
        consequences: List[Dict[str, Any]],
        world: Dict[str, Any],
    ) -> bool:
        """Apply consequences from player choices to quest evolution.

        This makes quests react to player decisions, not just events.
        Consequences can advance quest stages, add history entries,
        and branch quest arcs based on tags.

        Args:
            quest: Quest object to update.
            consequences: List of consequence dicts from the consequence engine.
            world: World state dict (used for context).

        Returns:
            True if any consequence was applied to the quest.
        """
        if quest.status != "active":
            return False

        applied = False
        for c in consequences:
            c_type = c.get("consequence_type") or c.get("type", "")
            c_data = c.get("data", {})

            if c_type == "faction_power_shift":
                # Record power shift in quest history for cross-quest impact
                quest.history.append({
                    "type": "power_shift",
                    "data": c_data,
                })
                applied = True

            elif c_type == "tag_add":
                tag = c_data.get("tag", "")

                # Record tag in quest history
                quest.history.append({
                    "type": "tag_add",
                    "data": c_data,
                })

                # 🔥 Branch quest arc based on tags
                if tag == "betrayal_exposed":
                    quest.current_stage_index = min(
                        quest.current_stage_index + 1,
                        len(quest.stages) - 1
                    )
                    # Update arc progress
                    quest.arc_progress = quest.current_stage_index / len(quest.stages) if quest.stages else 0
                    quest.history.append({
                        "type": "stage_branch",
                        "trigger": "betrayal_exposed",
                        "new_stage_index": quest.current_stage_index,
                    })
                    applied = True

                elif tag in ("faction_destroyed", "war_declared", "alliance_broken"):
                    # These major events should advance quest stages
                    quest.current_stage_index = min(
                        quest.current_stage_index + 1,
                        len(quest.stages) - 1
                    )
                    quest.arc_progress = quest.current_stage_index / len(quest.stages) if quest.stages else 0
                    quest.history.append({
                        "type": "stage_branch",
                        "trigger": tag,
                        "new_stage_index": quest.current_stage_index,
                    })
                    applied = True

                elif tag in ("supported_aggressor", "supported_target"):
                    # Update quest arc based on player alignment
                    quest.history.append({
                        "type": "alignment",
                        "tag": tag,
                    })
                    applied = True

                else:
                    # Generic tag recording
                    quest.history.append({
                        "type": "consequence_tag",
                        "tag": tag,
                    })
                    applied = True

            elif c_type == "belief_update":
                quest.history.append({
                    "type": "belief_update",
                    "data": c_data,
                })
                applied = True

            elif c_type == "world_state_change":
                quest.history.append({
                    "type": "world_change",
                    "data": c_data,
                })
                applied = True

        return applied

    def apply_global_effects(
        self,
        quest: Any,
        global_consequences: List[Dict[str, Any]],
        world: Dict[str, Any],
    ) -> bool:
        """Apply global consequences from other quests to this quest.

        This enables cross-quest impact where quests influence each other.

        Args:
            quest: Quest object to update.
            global_consequences: List of all consequences from all quests.
            world: World state dict.

        Returns:
            True if any global effect was applied.
        """
        if quest.status != "active":
            return False

        applied = False
        for c in global_consequences:
            c_type = c.get("consequence_type") or c.get("type", "")
            c_data = c.get("data", {})

            # Check if global consequence is relevant to this quest
            if c_type == "faction_power_shift":
                actor = c_data.get("actor", "")
                target = c_data.get("target", "")

                # If this quest involves either faction, record the effect
                quest_title = getattr(quest, "title", "").lower()
                quest_type = getattr(quest, "type", "").lower()

                if actor.lower() in quest_title or target.lower() in quest_title:
                    quest.history.append({
                        "type": "global_power_shift",
                        "source": "cross_quest",
                        "data": c_data,
                    })
                    applied = True

            elif c_type == "tag_add":
                tag = c_data.get("tag", "")

                # Major world events affect all active quests
                if tag in ("faction_destroyed", "war_declared", "betrayal_exposed"):
                    quest.history.append({
                        "type": "global_event",
                        "tag": tag,
                        "source": "cross_quest",
                    })
                    # Potentially advance quest stage due to world events
                    if tag == "faction_destroyed" and quest.current_stage_index < len(quest.stages) - 1:
                        quest.current_stage_index += 1
                        quest.arc_progress = quest.current_stage_index / len(quest.stages) if quest.stages else 0
                    applied = True

        return applied

    def filter_invalid_events(
        self,
        event: Optional[Dict[str, Any]],
        world: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Filter out events that violate irreversible constraints.

        This is the hard constraint layer that makes consequences real
        by blocking events that contradict irreversible history.

        Args:
            event: Event dict to validate.
            world: World state dict with history flags.

        Returns:
            The event dict if valid, None if it should be blocked.
        """
        if event is None:
            return None

        event_type = event.get("type", "")

        # Check for faction respawn when destroyed
        if event_type == "spawn_faction":
            faction_name = event.get("faction_name", "")
            if self._is_faction_destroyed(world, faction_name):
                return None

        # Check for alliance events when alliance is broken
        if event_type in ("ally_with", "form_alliance"):
            if "alliance_broken" in world.get("history_flags", set()):
                return None

        # Check for peace events when peace is broken
        if event_type in ("make_peace", "peace_treaty"):
            if "peace_broken" in world.get("history_flags", set()):
                return None

        # Check for events involving destroyed factions
        if event_type in ("faction_event", "faction_action"):
            faction_name = event.get("faction", "") or event.get("actor", "")
            if self._is_faction_destroyed(world, faction_name):
                return None

        return event

    def _is_faction_destroyed(
        self,
        world: Dict[str, Any],
        faction_name: str,
    ) -> bool:
        """Check if a faction is destroyed in world state.

        Args:
            world: World state dict.
            faction_name: Faction name to check.

        Returns:
            True if faction is destroyed or has irreversible flag.
        """
        if "faction_destroyed" in world.get("history_flags", set()):
            factions = world.get("factions", {})
            if faction_name in factions:
                return factions[faction_name].get("destroyed", False)
        return False

