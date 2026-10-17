"""Consequence Engine - The heart of the irreversible choice system.

This module provides the ConsequenceEngine class that translates player
choices into concrete consequences affecting the world state, NPC beliefs,
and narrative trajectory.

Core principle: Every choice has consequences that cannot be undone.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .choice_models import ConsequenceRecord, PlayerChoice


class ConsequenceEngine:
    """Translates player choices into concrete, irreversible consequences.

    The ConsequenceEngine analyzes the player's choice and generates
    consequences that will mutate the world state and NPC beliefs in
    permanent ways.

    Usage:
        engine = ConsequenceEngine()
        consequences = engine.apply(choice, quest, world_state, memory)
    """

    # Consequence mappings by quest type and option
    CONSEQUENCE_MAP: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        "conflict": {
            "support_actor": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": 0.3,
                },
                {
                    "type": "belief_update",
                    "actor_key": "actor",
                    "target_key": "player",
                    "delta": 0.5,
                },
                {
                    "type": "tag_add",
                    "tag": "supported_aggressor",
                },
            ],
            "support_target": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": -0.3,
                },
                {
                    "type": "belief_update",
                    "actor_key": "target",
                    "target_key": "player",
                    "delta": 0.5,
                },
                {
                    "type": "world_state_change",
                    "key": "tension_level",
                    "delta": -0.2,
                },
            ],
            "mediate": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": -0.1,
                },
                {
                    "type": "belief_update",
                    "actor_key": "actor",
                    "target_key": "player",
                    "delta": 0.2,
                },
                {
                    "type": "belief_update",
                    "actor_key": "target",
                    "target_key": "player",
                    "delta": 0.3,
                },
                {
                    "type": "world_state_change",
                    "key": "tension_level",
                    "delta": -0.3,
                },
            ],
        },
        "betrayal": {
            "forgive": [
                {
                    "type": "belief_update",
                    "actor_key": "betrayer",
                    "target_key": "player",
                    "delta": 0.3,
                },
                {
                    "type": "tag_add",
                    "tag": "forgave_betrayer",
                },
                {
                    "type": "world_state_change",
                    "key": "trust_network",
                    "delta": 0.1,
                },
            ],
            "punish": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "player",
                    "target_key": "betrayer",
                    "delta": 0.4,
                },
                {
                    "type": "belief_update",
                    "actor_key": "betrayer",
                    "target_key": "player",
                    "delta": -0.8,
                },
                {
                    "type": "tag_add",
                    "tag": "punished_betrayer",
                },
            ],
            "exploit": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "player",
                    "target_key": "betrayer",
                    "delta": 0.5,
                },
                {
                    "type": "belief_update",
                    "actor_key": "others",
                    "target_key": "player",
                    "delta": -0.3,
                },
                {
                    "type": "tag_add",
                    "tag": "exploited_situation",
                },
            ],
        },
        "war": {
            "full_assault": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": 0.5,
                },
                {
                    "type": "world_state_change",
                    "key": "tension_level",
                    "delta": 0.4,
                },
                {
                    "type": "tag_add",
                    "tag": "chose_war",
                },
            ],
            "siege": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": 0.2,
                },
                {
                    "type": "world_state_change",
                    "key": "tension_level",
                    "delta": 0.1,
                },
            ],
            "assassinate": [
                {
                    "type": "faction_power_shift",
                    "actor_key": "actor",
                    "target_key": "target",
                    "delta": 0.7,
                },
                {
                    "type": "tag_add",
                    "tag": "assassinated_leader",
                },
            ],
        },
    }

    def apply(
        self,
        choice: PlayerChoice,
        quest: Any,
        world_state: Dict[str, Any],
        memory: Optional[Dict[str, Any]] = None,
    ) -> List[ConsequenceRecord]:
        """Apply a player choice and generate consequences.

        Translates the player's selected option into concrete
        consequences that affect factions, beliefs, and world state.

        Args:
            choice: The PlayerChoice with selected option.
            quest: The quest this choice belongs to.
            world_state: Current world state dict.
            memory: Optional NPC memory/belief state.

        Returns:
            List of ConsequenceRecord objects generated from the choice.
        """
        if not choice or not choice.resolved or not choice.selected_option:
            return []

        option_id = choice.selected_option.get("id", "")
        quest_type = getattr(quest, "type", "")

        # Get consequences for this quest type and option
        type_consequences = self.CONSEQUENCE_MAP.get(quest_type, {})
        consequence_templates = type_consequences.get(option_id, [])

        # If no quest-type-specific consequences, use generic consequences
        if not consequence_templates:
            consequence_templates = self._get_generic_consequences(option_id)

        # Resolve consequence templates with actual world state
        consequences = []
        for template in consequence_templates:
            record = self._resolve_consequence(
                template, choice, world_state, memory
            )
            if record:
                consequences.append(record)

        return consequences

    def _get_generic_consequences(
        self, option_id: str
    ) -> List[Dict[str, Any]]:
        """Get generic consequences for options not in the map.

        Args:
            option_id: The selected option ID.

        Returns:
            List of consequence templates.
        """
        # Generic patterns based on option ID keywords
        if "support" in option_id:
            return [
                {
                    "type": "faction_power_shift",
                    "actor_key": "selected",
                    "target_key": "opponent",
                    "delta": 0.2,
                },
                {
                    "type": "belief_update",
                    "actor_key": "selected",
                    "target_key": "player",
                    "delta": 0.3,
                },
            ]
        elif "mediate" in option_id or "peace" in option_id:
            return [
                {
                    "type": "world_state_change",
                    "key": "tension_level",
                    "delta": -0.2,
                },
            ]
        elif "punish" in option_id or "attack" in option_id:
            return [
                {
                    "type": "faction_power_shift",
                    "actor_key": "player",
                    "target_key": "target",
                    "delta": 0.3,
                },
            ]
        elif "ignore" in option_id or "walk_away" in option_id:
            return [
                {
                    "type": "belief_update",
                    "actor_key": "affected_parties",
                    "target_key": "player",
                    "delta": -0.2,
                },
            ]

        # Default: minimal consequence
        return [
            {
                "type": "world_state_change",
                "key": "story_progress",
                "delta": 0.1,
            },
        ]

    def _resolve_consequence(
        self,
        template: Dict[str, Any],
        choice: PlayerChoice,
        world_state: Dict[str, Any],
        memory: Optional[Dict[str, Any]],
    ) -> Optional[ConsequenceRecord]:
        """Resolve a consequence template with actual values.

        Args:
            template: Consequence template dict.
            choice: The player's choice.
            world_state: Current world state.
            memory: Optional memory state.

        Returns:
            Resolved ConsequenceRecord, or None if unresolvable.
        """
        consequence_type = template.get("type", "")
        data = dict(template)  # Copy template as data

        if consequence_type == "faction_power_shift":
            # Resolve actor and target from world state
            return self._resolve_faction_shift(template, choice, world_state)
        elif consequence_type == "belief_update":
            return self._resolve_belief_update(template, choice, world_state, memory)
        elif consequence_type == "world_state_change":
            return self._resolve_world_state_change(template)
        elif consequence_type == "tag_add":
            return self._resolve_tag_add(template, choice)

        return ConsequenceRecord(
            choice_id=choice.id,
            consequence_type=consequence_type,
            data=data,
            applied=False,
        )

    def _resolve_faction_shift(
        self,
        template: Dict[str, Any],
        choice: PlayerChoice,
        world_state: Dict[str, Any],
    ) -> ConsequenceRecord:
        """Resolve faction power shift consequence.

        Args:
            template: Consequence template.
            choice: Player choice.
            world_state: World state.

        Returns:
            Resolved ConsequenceRecord.
        """
        actor_key = template.get("actor_key", "actor")
        target_key = template.get("target_key", "target")
        delta = template.get("delta", 0.1)

        factions = world_state.get("factions", {})
        faction_names = list(factions.keys())

        # Resolve actor name
        actor = self._resolve_faction_name(actor_key, faction_names, 0)
        target = self._resolve_faction_name(target_key, faction_names, 1)

        return ConsequenceRecord(
            choice_id=choice.id,
            consequence_type="faction_power_shift",
            data={
                "actor": actor,
                "target": target,
                "delta": delta,
            },
            applied=False,
        )

    def _resolve_belief_update(
        self,
        template: Dict[str, Any],
        choice: PlayerChoice,
        world_state: Dict[str, Any],
        memory: Optional[Dict[str, Any]],
    ) -> ConsequenceRecord:
        """Resolve belief update consequence.

        Args:
            template: Consequence template.
            choice: Player choice.
            world_state: World state for faction resolution.
            memory: Optional memory state.

        Returns:
            Resolved ConsequenceRecord.
        """
        actor_key = template.get("actor_key", "actor")
        target_key = template.get("target_key", "player")
        delta = template.get("delta", 0.1)

        # Resolve actor key to actual faction name from world state
        factions = world_state.get("factions", {})
        faction_names = list(factions.keys())
        actor = self._resolve_faction_name(actor_key, faction_names, 0)
        
        # Target is typically "player" but could be a faction
        if target_key == "player":
            target = "player"
        else:
            target = self._resolve_faction_name(target_key, faction_names, 1)

        return ConsequenceRecord(
            choice_id=choice.id,
            consequence_type="belief_update",
            data={
                "actor": actor,
                "target": target,
                "delta": delta,
                "key": f"{actor}->{target}",
            },
            applied=False,
        )

    def _resolve_world_state_change(
        self,
        template: Dict[str, Any],
    ) -> ConsequenceRecord:
        """Resolve world state change consequence.

        Args:
            template: Consequence template.

        Returns:
            Resolved ConsequenceRecord.
        """
        return ConsequenceRecord(
            choice_id=template.get("choice_id", ""),
            consequence_type="world_state_change",
            data={
                "key": template.get("key", ""),
                "delta": template.get("delta", 0.0),
            },
            applied=False,
        )

    def _resolve_tag_add(
        self,
        template: Dict[str, Any],
        choice: PlayerChoice,
    ) -> ConsequenceRecord:
        """Resolve tag addition consequence.

        Args:
            template: Consequence template.
            choice: Player choice.

        Returns:
            Resolved ConsequenceRecord.
        """
        return ConsequenceRecord(
            choice_id=choice.id,
            consequence_type="tag_add",
            data={
                "tag": template.get("tag", ""),
            },
            applied=False,
        )

    @staticmethod
    def _resolve_faction_name(
        key: str,
        faction_names: List[str],
        default_index: int = 0,
    ) -> str:
        """Resolve a faction key to an actual faction name.

        Args:
            key: Faction key (e.g., "actor", "target", "selected").
            faction_names: Available faction names from world state.
            default_index: Index to use if key doesn't match.

        Returns:
            Resolved faction name.
        """
        if key in faction_names:
            return key
        if key == "player":
            return "player"
        if 0 <= default_index < len(faction_names):
            return faction_names[default_index]
        return key  # Return key as-is as fallback

