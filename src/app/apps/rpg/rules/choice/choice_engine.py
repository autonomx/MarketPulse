"""Choice Engine - Generates meaningful player choices.

This module provides the ChoiceEngine class that creates contextually
relevant decision options based on quest type, stage, and world state.

The core philosophy is that choices should be:
- Meaningful: Each option leads to different consequences
- Context-aware: Options reflect the current quest and world situation
- Type-specific: Different quest types generate different choice patterns
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from .choice_models import PlayerChoice


class ChoiceEngine:
    """Generates meaningful choices for players based on quest context.

    The ChoiceEngine analyzes the current quest type, stage, and world
    state to present players with meaningful decisions that will shape
    the narrative and world in irreversible ways.

    Usage:
        engine = ChoiceEngine()
        choices = engine.generate_choices(quest, world_state)
    """

    # Default choices for common quest stages
    STAGE_CHOICES: Dict[str, List[Dict[str, str]]] = {
        "setup": [
            {"id": "investigate", "text": "Investigate the situation further"},
            {"id": "ignore", "text": "Focus on other matters"},
            {"id": "gather_allies", "text": "Seek out potential allies"},
        ],
        "escalation": [
            {"id": "support_actor", "text": "Support the aggressor"},
            {"id": "support_target", "text": "Defend the victim"},
            {"id": "mediate", "text": "Attempt to broker peace"},
        ],
        "climax": [
            {"id": "direct_action", "text": "Take direct action now"},
            {"id": "strategic_retreat", "text": "Retreat and regroup"},
            {"id": "negotiate", "text": "Try negotiation one last time"},
        ],
        "resolution": [
            {"id": "enforce_justice", "text": "Enforce strict justice"},
            {"id": "show_mercy", "text": "Show mercy and forgive"},
            {"id": "walk_away", "text": "Walk away from the situation"},
        ],
        "confrontation": [
            {"id": "confront_directly", "text": "Confront them directly"},
            {"id": "gather_evidence", "text": "Gather more evidence first"},
            {"id": "secret_deal", "text": "Try to make a secret deal"},
        ],
    }

    # Quest-type-specific choice overrides
    QUEST_TYPE_CHOICES: Dict[str, Dict[str, List[Dict[str, str]]]] = {
        "conflict": {
            "escalation": [
                {"id": "support_actor", "text": "Support the aggressor"},
                {"id": "support_target", "text": "Defend the victim"},
                {"id": "mediate", "text": "Attempt to broker peace"},
            ],
        },
        "betrayal": {
            "confrontation": [
                {"id": "forgive", "text": "Forgive the betrayer"},
                {"id": "punish", "text": "Punish them"},
                {"id": "exploit", "text": "Use this to your advantage"},
            ],
        },
        "war": {
            "climax": [
                {"id": "full_assault", "text": "Launch a full assault"},
                {"id": "siege", "text": "Lay siege to weaken them"},
                {"id": "assassinate", "text": "Target the enemy leader"},
            ],
        },
        "alliance": {
            "escalation": [
                {"id": "strengthen_ties", "text": "Deepen the alliance"},
                {"id": "demand_tribute", "text": "Demand tribute or concessions"},
                {"id": "threaten_leave", "text": "Threaten to leave the alliance"},
            ],
        },
        "supply": {
            "escalation": [
                {"id": "hoard_resources", "text": "Hoard what you have"},
                {"id": "share_scarcity", "text": "Share the shortage fairly"},
                {"id": "seek_alternative", "text": "Search for alternative sources"},
            ],
        },
        "rebellion": {
            "climax": [
                {"id": "join_rebellion", "text": "Join the rebellion openly"},
                {"id": "suppress_rebellion", "text": "Help suppress the rebellion"},
                {"id": "secret_back", "text": "Secretly support the rebellion"},
            ],
        },
    }

    def generate_choices(
        self,
        quest: Any,
        world_state: Dict[str, Any],
        quest_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        beliefs: Optional[Dict[str, Any]] = None,
        global_flags: Optional[set] = None,
    ) -> Optional[PlayerChoice]:
        """Generate meaningful choices for the player.

        Creates a set of contextually relevant choices based on the
        quest's current stage, type, world state, past decisions,
        belief system, and global history flags.

        Args:
            quest: Quest object with type and current stage info.
            world_state: Current world state dict.
            quest_id: Optional quest ID override for the choice.
            history: Optional list of past quest history entries.
            beliefs: Optional belief system state dict.
            global_flags: Optional set of global history flags.

        Returns:
            PlayerChoice object with generated options, or None if no
            choices should be generated for this stage.
        """
        if not quest or not hasattr(quest, "stages"):
            return None

        # Get current stage
        stage = quest.current_stage
        if not stage:
            # Try to get stage from current_stage_index
            if hasattr(quest, "current_stage_index") and quest.stages:
                idx = quest.current_stage_index
                if 0 <= idx < len(quest.stages):
                    stage = quest.stages[idx]
                else:
                    return None
            else:
                return None

        stage_name = stage.name if hasattr(stage, "name") else "unknown"
        qtype = getattr(quest, "type", "")
        title = getattr(quest, "title", "Unknown Quest")
        qid = quest_id or getattr(quest, "id", "")

        # Check for quest-type-specific choices first
        choices = self._get_type_specific_choices(qtype, stage_name)

        # Fall back to stage-based choices
        if not choices:
            choices = self._get_stage_choices(stage_name)

        # If still no choices, check world state for special cases
        if not choices:
            choices = self._get_world_based_choices(world_state, stage_name)

        # 🔥 Apply context modifiers based on history, beliefs, and global flags
        if choices and (history or beliefs or global_flags):
            choices = self._contextualize_choices(
                choices, history, beliefs, global_flags, qtype, stage_name
            )

        if not choices:
            return None

        # 🔥 Build context-aware description
        description = self._build_context_description(
            title, stage_name, history, beliefs, global_flags
        )

        return PlayerChoice(
            quest_id=cast(Any, qid),
            stage=stage_name,
            description=description,
            options=choices,
        )

    def _get_type_specific_choices(
        self,
        qtype: str,
        stage_name: str,
    ) -> List[Dict[str, str]]:
        """Get choices specific to quest type and stage.

        Args:
            qtype: Quest type string (e.g., "conflict", "betrayal").
            stage_name: Current stage name.

        Returns:
            List of option dicts, or empty list if none found.
        """
        type_choices = self.QUEST_TYPE_CHOICES.get(qtype, {})
        return type_choices.get(stage_name, [])

    def _get_stage_choices(self, stage_name: str) -> List[Dict[str, str]]:
        """Get default choices for a stage.

        Args:
            stage_name: Current stage name.

        Returns:
            List of option dicts, or empty list if none found.
        """
        return self.STAGE_CHOICES.get(stage_name, [])

    def _get_world_based_choices(
        self,
        world_state: Dict[str, Any],
        stage_name: str,
    ) -> List[Dict[str, str]]:
        """Generate choices based on world state.

        Creates contextual choices based on factions, tensions, and
        other world state elements.

        Args:
            world_state: Current world state dict.
            stage_name: Current stage name.

        Returns:
            List of option dicts, or empty list if none generated.
        """
        choices = []

        # Check for faction-related context
        factions = world_state.get("factions", {})
        if factions:
            faction_names = list(factions.keys())
            if len(faction_names) >= 2:
                # Create choices based on faction dynamics
                actor = faction_names[0]
                target = faction_names[1]
                choices = [
                    {
                        "id": f"support_{actor}",
                        "text": f"Support {actor}",
                    },
                    {
                        "id": f"support_{target}",
                        "text": f"Support {target}",
                    },
                    {
                        "id": "remain_neutral",
                        "text": "Remain neutral",
                    },
                ]

        # Check for tension levels
        tension = world_state.get("tension_level", 0)
        if tension > 0.7:
            choices = [
                {"id": "de_escalate", "text": "Try to reduce tensions"},
                {"id": "escalate_further", "text": "Push the advantage"},
                {"id": "wait_and_see", "text": "Wait and see how things develop"},
            ]

        return choices

    def _contextualize_choices(
        self,
        choices: List[Dict[str, str]],
        history: Optional[List[Dict[str, Any]]],
        beliefs: Optional[Dict[str, Any]],
        global_flags: Optional[set],
        qtype: str,
        stage_name: str,
    ) -> List[Dict[str, str]]:
        """Modify choices based on historical context, beliefs, and global flags.

        This makes choices situational and meaningful by:
        - Adding context-specific options based on history
        - Modifying choice text to reflect past decisions
        - Adding consequences-aware options

        Args:
            choices: Original choice options.
            history: Quest history entries.
            beliefs: Belief system state.
            global_flags: World history flags.
            qtype: Quest type.
            stage_name: Current stage name.

        Returns:
            Modified choice list with contextual elements.
        """
        contextualized: list[Any] = []

        # Check for betrayal history - adds betrayal-specific options
        has_betrayal = False
        if history:
            for h in history:
                if h.get("type") in ("power_shift", "tag_add"):
                    tag = h.get("data", {}).get("tag", "")
                    if "betrayal" in tag.lower() or "betray" in tag.lower():
                        has_betrayal = True
                        break

        if has_betrayal and qtype == "betrayal":
            # Add extra confrontation option
            choices.append({
                "id": "expose_betrayal",
                "text": "Publicly expose the betrayal",
            })

        # Check for power shift history - modifies support options
        has_supported_faction = False
        if history:
            for h in history:
                if h.get("type") == "alignment":
                    has_supported_faction = True
                    break

        if has_supported_faction:
            # Modify choices to reflect past alignment
            for choice in choices:
                if "support" in choice.get("id", "").lower():
                    choice["text"] = choice["text"] + " (you have history here)"

        # Check global flags for major events
        if global_flags:
            if "faction_destroyed" in global_flags:
                # Add post-destruction options
                choices.append({
                    "id": "consolidate_power",
                    "text": "Consolidate power in the power vacuum",
                })
            if "war_declared" in global_flags:
                choices.append({
                    "id": "prepare_for_war",
                    "text": "Prepare your faction for war",
                })

        # Check beliefs for moral alignment options
        if beliefs:
            player_alignment = beliefs.get("player", {}).get("alignment", "")
            if player_alignment == "ruthless":
                # Add ruthless option for applicable stages
                if stage_name in ("climax", "confrontation"):
                    choices.append({
                        "id": "brutal_suppression",
                        "text": "Brutally suppress resistance",
                    })
            elif player_alignment == "diplomatic":
                if stage_name in ("escalation", "confrontation"):
                    choices.append({
                        "id": "seek_compromise",
                        "text": "Seek a diplomatic compromise",
                    })

        return choices

    def _build_context_description(
        self,
        title: str,
        stage_name: str,
        history: Optional[List[Dict[str, Any]]],
        beliefs: Optional[Dict[str, Any]],
        global_flags: Optional[set],
    ) -> str:
        """Build a context-aware choice description.

        Args:
            title: Quest title.
            stage_name: Current stage name.
            history: Quest history entries.
            beliefs: Belief system state.
            global_flags: World history flags.

        Returns:
            Contextualized description string.
        """
        base = f"{title} - {stage_name.capitalize()}: What will you do?"

        # Add context hints
        context_parts = []

        if history and len(history) > 0:
            recent = history[-1]
            r_type = recent.get("type", "")
            if r_type == "power_shift":
                context_parts.append("Power dynamics are shifting")

        if global_flags:
            if "war_declared" in global_flags:
                context_parts.append("War looms on the horizon")
            if "faction_destroyed" in global_flags:
                context_parts.append("A faction has fallen")

        if context_parts:
            return base + " (" + "; ".join(context_parts) + ")"

        return base

    @staticmethod
    def register_custom_choices(
        quest_type: str,
        stage: str,
        choices: List[Dict[str, str]],
        engine: Optional["ChoiceEngine"] = None,
    ) -> None:
        """Register custom choices for a quest type and stage.

        Args:
            quest_type: Quest type string (e.g., "rebellion").
            stage: Stage name string (e.g., "climax").
            choices: List of option dicts with "id" and "text" keys.
            engine: Optional ChoiceEngine instance to modify. If None,
                    the base class dictionary is modified (affects all instances).
        """
        if engine is not None:
            if quest_type not in engine.QUEST_TYPE_CHOICES:
                engine.QUEST_TYPE_CHOICES[quest_type] = {}
            engine.QUEST_TYPE_CHOICES[quest_type][stage] = choices
        else:
            if quest_type not in ChoiceEngine.QUEST_TYPE_CHOICES:
                ChoiceEngine.QUEST_TYPE_CHOICES[quest_type] = {}
            ChoiceEngine.QUEST_TYPE_CHOICES[quest_type][stage] = choices
