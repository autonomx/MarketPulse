"""World Mutator - Applies irreversible state changes to the world.

This module provides the WorldMutator class that applies consequences
from player choices to the world state, ensuring changes are permanent
and cannot be rolled back.

Key principle: World mutations are ONE-WAY operations. Once a faction
loses power, gains an enemy, or is destroyed, there is no automatic
reversal mechanism.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .choice_models import ConsequenceRecord


class WorldMutator:
    """Applies irreversible state changes to the world.

    The WorldMutator takes consequence records and applies them to
    the world state dictionary. Changes are permanent and accumulate
    over time.

    Usage:
        mutator = WorldMutator()
        mutator.apply_consequences(consequences, world_state)
    """

    # Tags that represent irreversible world state changes
    IRREVERSIBLE_TAGS = {
        "faction_destroyed",
        "alliance_broken",
        "leader_killed",
        "territory_lost",
        "betrayal_recorded",
        "war_declared",
        "peace_broken",
    }

    def apply_consequences(
        self,
        consequences: List[ConsequenceRecord],
        world_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Apply a list of consequences to the world state.

        Args:
            consequences: List of ConsequenceRecord objects.
            world_state: World state dict to modify.

        Returns:
            List of applied effect descriptions.
        """
        applied = []
        for consequence in consequences:
            effect = self._apply_single(consequence, world_state)
            if effect:
                applied.append(effect)
                consequence.applied = True
        return applied

    def _apply_single(
        self,
        consequence: ConsequenceRecord,
        world_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Apply a single consequence to world state.

        Args:
            consequence: Consequence record to apply.
            world_state: World state dict.

        Returns:
            Effect description dict, or None if not applicable.
        """
        ctype = consequence.consequence_type
        data = consequence.data

        if ctype == "faction_power_shift":
            return self.shift_faction_power(
                world_state,
                data.get("actor", ""),
                data.get("target", ""),
                data.get("delta", 0.0),
            )
        elif ctype == "world_state_change":
            return self.change_world_state(
                world_state,
                data.get("key", ""),
                data.get("delta", 0.0),
            )
        elif ctype == "tag_add":
            return self.add_world_tag(
                world_state,
                data.get("tag", ""),
            )

        return None

    def shift_faction_power(
        self,
        world_state: Dict[str, Any],
        actor: str,
        target: str,
        delta: float,
    ) -> Dict[str, Any]:
        """Shift power between two factions.

        Increases actor's power by delta and decreases target's
        power by delta. If a faction's power drops to 0 or below,
        it is marked as destroyed.

        Args:
            world_state: World state dict.
            actor: Faction gaining power.
            target: Faction losing power.
            delta: Amount of power to shift.

        Returns:
            Effect description dict.
        """
        world_state.setdefault("factions", {})

        # Initialize factions if needed
        world_state["factions"].setdefault(actor, {"power": 1.0})
        world_state["factions"].setdefault(target, {"power": 1.0})

        # Apply power shift
        world_state["factions"][actor]["power"] = round(
            world_state["factions"][actor].get("power", 1.0) + delta, 4
        )
        world_state["factions"][target]["power"] = round(
            world_state["factions"][target].get("power", 1.0) - delta, 4
        )

        # Check for faction destruction
        if world_state["factions"][target]["power"] <= 0:
            self.mark_irreversible(world_state, "faction_destroyed")
            world_state["factions"][target]["destroyed"] = True

        return {
            "type": "faction_power_shift",
            "actor": actor,
            "target": target,
            "delta": delta,
            "actor_power": world_state["factions"][actor]["power"],
            "target_power": world_state["factions"][target]["power"],
        }

    def change_world_state(
        self,
        world_state: Dict[str, Any],
        key: str,
        delta: float,
    ) -> Dict[str, Any]:
        """Change a world state value by delta.

        Args:
            world_state: World state dict.
            key: World state key to modify.
            delta: Amount to change by.

        Returns:
            Effect description dict.
        """
        old_value = world_state.get(key, 0.0)
        new_value = round(old_value + delta, 4)
        world_state[key] = new_value

        return {
            "type": "world_state_change",
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
            "delta": delta,
        }

    def add_world_tag(
        self,
        world_state: Dict[str, Any],
        tag: str,
    ) -> Dict[str, Any]:
        """Add an irreversible tag to the world state.

        Args:
            world_state: World state dict.
            tag: Tag to add.

        Returns:
            Effect description dict.
        """
        world_state.setdefault("history_flags", set())
        world_state["history_flags"].add(tag)

        is_irreversible = tag in self.IRREVERSIBLE_TAGS

        return {
            "type": "tag_add",
            "tag": tag,
            "is_irreversible": is_irreversible,
        }

    def mark_irreversible(
        self,
        world_state: Dict[str, Any],
        tag: str,
    ) -> None:
        """Mark the world state with an irreversible flag.

        This is the core guarantee of the consequence engine:
        certain events can never be undone.

        Args:
            world_state: World state dict.
            tag: Irreversible flag.
        """
        world_state.setdefault("history_flags", set())
        world_state["history_flags"].add(tag)

    def check_irreversible(
        self,
        world_state: Dict[str, Any],
        tag: str,
    ) -> bool:
        """Check if a tag exists in the world's history flags.

        This can be used to prevent actions that would contradict
        irreversible history.

        Args:
            world_state: World state dict.
            tag: Tag to check.

        Returns:
            True if the tag exists, False otherwise.
        """
        flags = world_state.get("history_flags", set())
        return tag in flags

    def prevent_respawn(
        self,
        world_state: Dict[str, Any],
        faction_name: str,
        check_tag: str = "faction_destroyed",
    ) -> bool:
        """Check if a faction respawn should be prevented.

        If the world has the faction_destroyed flag and the faction
        was previously destroyed, prevent respawn.

        Args:
            world_state: World state dict.
            faction_name: Faction name to check.
            check_tag: Tag to check for destruction history.

        Returns:
            True if respawn should be prevented.
        """
        if self.check_irreversible(world_state, check_tag):
            return True

        factions = world_state.get("factions", {})
        if faction_name in factions:
            return factions[faction_name].get("destroyed", False)

        return False

    def get_world_summary(
        self,
        world_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get a summary of the current world state.

        Args:
            world_state: World state dict.

        Returns:
            Summary dict with key world state information.
        """
        factions = world_state.get("factions", {})
        return {
            "factions": {
                name: {
                    "power": f.get("power", 0.0),
                    "destroyed": f.get("destroyed", False),
                }
                for name, f in factions.items()
            },
            "history_flags": list(world_state.get("history_flags", set())),
            "timeline_entries": len(world_state.get("timeline", [])),
        }