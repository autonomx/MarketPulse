"""Resolution Engine — Tier 13: Emotional + Experiential Layer.

This module implements Tier 13's Resolution Generator that produces
emotionally meaningful and narratively satisfying resolutions to storylines.

Problem:
    Resolution is mechanical: "conflict resolved", "agreement reached"
    But not: emotionally meaningful, narratively satisfying
    
    Result: storylines end without emotional weight

Solution:
    ResolutionEngine generates satisfying resolutions using:
    1. Template-based resolution for common patterns
    2. LLM-assisted resolution for complex storylines (controlled usage)
    3. Emotional impact assessment for resolution quality
    
    The engine tracks resolution history and feeds into narrative memory.

Usage:
    engine = ResolutionEngine()
    resolution = engine.generate(storyline, characters, world_state)

Architecture:
    Resolution Types:
    - victory: One side achieved their goal
    - compromise: Both sides found middle ground  
    - tragedy: Something was lost in the process
    - redemption: A character overcame their flaws
    - betrayal: Trust was broken unexpectedly
    
    Prompt Template (for LLM):
    Storyline: {type}
    Participants: {A, B}
    History: {events...}
    Emotions: {anger, fear, trust levels}
    
    Generate satisfying resolution:
    - 1-2 sentences
    - Reflect consequences
    - Update relationships

Design Rules:
    - LLM used sparingly (only for high-importance storylines)
    - Template fallback always available
    - Resolutions update character emotions and relationships
    - Resolution quality affects future narrative gravity
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.apps.rpg.foundation.llm_gateway_adapter import adapt_base_provider
from app.apps.rpg.foundation.core.determinism import rng_for_current_turn, stable_sub_index
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.cognitive_resolution_engine.prompt', "1", 'You are a narrative resolution engine. Generate a satisfying resolution for this storyline.\n\nStoryline Type: {v0}\nParticipants: {v1}\nResolution Type: {v2}\nProgress: {v3:.0%}\n\nEvent History:\n{v4}\n\nCharacter States:\n{v5}\n\nGenerate a resolution that:\n1. Is exactly 1-2 sentences long\n2. Reflects the consequences of the storyline events\n3. Matches the resolution type: {v6}\n4. Feels emotionally meaningful, not mechanical\n5. Updates relationships based on the outcome\n\nResolution:')

logger = logging.getLogger(__name__)

# Resolution types
RESOLUTION_TYPES = [
    "victory",        # One side achieved their goal completely
    "compromise",     # Both sides found middle ground
    "tragedy",        # Something/someone was lost
    "redemption",     # A character overcame their nature/flaws
    "betrayal",       # Trust was broken unexpectedly
    "stalemate",      # No clear winner, conflict continues in new form
    "transcendence",  # Characters moved beyond the original conflict
    "sacrifice",      # Someone gave up something important for resolution
]

# Template patterns for resolutions without LLM
RESOLUTION_TEMPLATES = {
    "faction_conflict": {
        "victory": [
            "{winner} emerged triumphant over {loser}, their dominance reshaping the political landscape.",
            "After a decisive struggle, {winner} claimed victory, leaving {loser} to lick their wounds and plot revenge.",
        ],
        "compromise": [
            "{faction_a} and {faction_b} brokered a fragile truce, though old wounds simmer beneath the surface.",
            "Through painful negotiation, {faction_a} and {faction_b} found common ground, neither fully satisfied.",
        ],
        "tragedy": [
            "The conflict consumed everyone involved. {casualty} paid the ultimate price, a reminder of the cost of ambition.",
            "What began as political maneuvering ended in bloodshed. {casualty}'s death left all parties haunted.",
        ],
        "betrayal": [
            "{traitor} turned against {victim} at the crucial moment, rewriting the entire narrative of the conflict.",
            "The alliance was built on sand. {traitor}'s betrayal shattered the coalition and left {victim} devastated.",
        ],
    },
    "personal_conflict": {
        "redemption": [
            "{character} faced their darkest impulses and chose a different path, earning unexpected respect.",
            "Against all expectations, {character} overcame their nature. The transformation surprised even their enemies.",
        ],
        "victory": [
            "{winner} proved their point decisively, silencing {loser} in the process.",
            "Through {method}, {winner} achieved personal vindication against {loser}.",
        ],
        "compromise": [
            "After much tension, {character_a} and {character_b} found a way to coexist without fully reconciling.",
            "A grudging respect formed between {character_a} and {character_b}, born from hard-won understanding.",
        ],
    },
    "quest": {
        "victory": [
            "The quest reached its triumphant conclusion. {reward} was secured, changing the balance of power.",
            "Against all odds, the quest succeeded. {reward} now belongs to {winner}, with far-reaching consequences.",
        ],
        "tragedy": [
            "The quest succeeded, but at a terrible cost. {loss} can never be recovered.",
            "Victory tasted like ashes. The quest was completed, but {loss} made it feel like defeat.",
        ],
        "sacrifice": [
            "To complete the quest, {sacrificer} gave up {sacrifice}. The world changed, but so did they.",
            "{sacrificer}'s sacrifice ensured the quest's success. Their name will be remembered.",
        ],
    },
    "general": {
        "victory": ["{winner} achieved their goal, reshaping circumstances in their favor."],
        "compromise": ["A middle path was found, leaving all parties partially satisfied."],
        "tragedy": ["Events spiraled beyond anyone's control. The cost was higher than anyone anticipated."],
        "betrayal": ["Trust, once given, was weaponized. The betrayal changed everything."],
        "stalemate": ["Neither side could claim victory. The conflict transformed rather than ended."],
        "redemption": ["In a moment of clarity, {character} chose to be better. It changed the outcome."],
        "sacrifice": ["{sacrificer} gave up something precious. The resolution was bought with loss."],
        "transcendence": ["The characters rose above their differences. The conflict dissolved into something larger."],
    },
}


@dataclass
class ResolutionResult:
    """Result of a storyline resolution.
    
    Attributes:
        resolution_type: Type of resolution achieved.
        text: Human-readable resolution description.
        emotional_impact: Dict of emotion_name -> change_value.
        relationship_updates: Dict of (char_a, char_b) -> new_relationship_value.
        consequences: List of narrative consequences.
        satisfies_player: Whether resolution feels satisfying to player.
        importance: Resolution importance score (0.0-1.0).
    """
    
    resolution_type: str = "victory"
    text: str = ""
    emotional_impact: dict[str, float] = field(default_factory=dict)
    relationship_updates: dict[str, float] = field(default_factory=dict)
    consequences: list[str] = field(default_factory=list)
    satisfies_player: bool = False
    importance: float = 0.5
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize resolution result to dict."""
        return {
            "type": self.resolution_type,
            "text": self.text,
            "emotional_impact": dict(self.emotional_impact),
            "relationship_updates": dict(self.relationship_updates),
            "consequences": list(self.consequences),
            "satisfies_player": self.satisfies_player,
            "importance": self.importance,
        }


class ResolutionEngine:
    """Generates emotionally satisfying storyline resolutions.
    
    The engine uses template-based resolution for common patterns,
    with optional LLM assistance for high-importance storylines.
    It tracks resolution history and updates character states.
    """
    
    def __init__(self, llm_client: Optional[Any] = None, use_llm: bool = False):
        """Initialize the ResolutionEngine.
        
        Args:
            llm_client: Optional LLM client for advanced resolution generation.
            use_llm: Whether to use LLM for resolution (default: template-only).
        """
        self.llm_client = adapt_base_provider(llm_client)
        self.use_llm = use_llm
        
        # Tier 14 Fix: Resolution Entropy Injection
        # Track recent resolutions to prevent predictability over long runs
        self._recent_resolutions: list[str] = []
        
        self._stats: dict[str, Any] = {
            "resolutions_generated": 0,
            "llm_resolutions": 0,
            "template_resolutions": 0,
            "resolution_types_used": {},
        }
    
    def generate(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]] = None,
        world_state: Optional[dict[str, Any]] = None,
    ) -> ResolutionResult:
        """Generate a satisfying resolution for the given storyline.
        
        Args:
            storyline: Storyline dict with type, participants, events, etc.
            characters: Optional character data dict.
            world_state: Optional world state dict.
            
        Returns:
            ResolutionResult with text, emotional impact, and consequences.
        """
        self._stats["resolutions_generated"] += 1
        
        # Determine resolution type based on storyline context
        resolution_type = self._determine_resolution_type(
            storyline, characters, world_state
        )
        
        # Generate resolution text
        if self.use_llm and self.llm_client and storyline.get("importance", 0) > 0.7:
            resolution = self._generate_llm_resolution(
                storyline, characters, world_state, resolution_type
            )
            self._stats["llm_resolutions"] += 1
        else:
            resolution = self._generate_template_resolution(
                storyline, characters, world_state, resolution_type
            )
            self._stats["template_resolutions"] += 1
        
        # Track resolution type usage
        self._stats["resolution_types_used"][resolution_type] = (
            self._stats["resolution_types_used"].get(resolution_type, 0) + 1
        )
        
        # Calculate emotional impact on participants
        emotional_impact = self._calculate_emotional_impact(
            storyline, characters, resolution_type
        )
        
        # Calculate relationship updates
        relationship_updates = self._calculate_relationship_updates(
            storyline, characters, resolution_type
        )
        
        # Determine consequences
        consequences = self._determine_consequences(
            storyline, world_state, resolution_type
        )
        
        # Check player satisfaction
        satisfies_player = self._check_player_satisfaction(
            storyline, characters, resolution_type
        )
        
        return ResolutionResult(
            resolution_type=resolution_type,
            text=resolution,
            emotional_impact=emotional_impact,
            relationship_updates=relationship_updates,
            consequences=consequences,
            satisfies_player=satisfies_player,
            importance=storyline.get("importance", 0.5),
        )
    
    def _determine_resolution_type(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]] = None,
        world_state: Optional[dict[str, Any]] = None,
    ) -> str:
        """Determine the most satisfying resolution type.
        
        Resolution type is chosen based on:
        - Storyline characteristics (progress, importance, events)
        - Character emotional states
        - World state context
        - Tier 14 Fix: Resolution Entropy Injection to prevent predictability
        
        Args:
            storyline: Storyline dict.
            characters: Optional character data.
            world_state: Optional world state.
            
        Returns:
            Resolution type string.
        """
        progress = storyline.get("progress", 0.5)
        importance = storyline.get("importance", 0.5)
        events = storyline.get("events", [])
        participants = storyline.get("participants", [])
        
        # Default to most common resolution type
        candidate_types = ["victory", "compromise", "stalemate"]
        
        # Adjust based on progress
        if progress > 0.8:
            # High progress suggests resolution is near - favor victory/completion
            candidate_types = ["victory", "redemption", "sacrifice"]
        elif progress < 0.2:
            # Low progress - favor tragedy/stalemate/betrayal
            candidate_types = ["tragedy", "betrayal", "stalemate"]
        
        # Check for betrayal events in history
        has_betrayal = any(
            e.get("type") in ("betrayal", "betray") for e in events
        )
        if has_betrayal:
            candidate_types = ["betrayal", "tragedy", "victory"]
        
        # Check for sacrifice events
        has_sacrifice = any(
            e.get("type") in ("sacrifice", "selfless") for e in events
        )
        if has_sacrifice:
            candidate_types = ["sacrifice", "redemption", "compromise"]
        
        # Check emotional states for redemption potential
        if characters:
            for char_id in participants:
                char_data = characters.get(char_id, {})
                if isinstance(char_data, dict):
                    emotions = char_data.get("emotions", {})
                    if emotions.get("guilt", 0) > 0.5 or emotions.get("remorse", 0) > 0.5:
                        candidate_types.append("redemption")
                        break
        
        # Tier 14 Fix: Use _select_resolution_type with entropy injection
        return self._select_resolution_type(candidate_types)
    
    def _select_resolution_type(self, candidates: list[str]) -> str:
        """Select a resolution type with entropy to prevent predictability.
        
        Tier 14 Fix: Prevents resolution patterns from becoming predictable
        after ~50-100 resolutions by tracking recent selections and
        injecting entropy.
        
        Args:
            candidates: List of candidate resolution types.
            
        Returns:
            Selected resolution type.
        """
        if not candidates:
            return "victory"
        
        # Base selection: weighted random choice
        base = self._weighted_choice(candidates)
        
        # Avoid repetition: if base is in recent 3, pick something else
        if base in self._recent_resolutions[-3:]:
            # Pick from candidates not in recent history
            non_recent = [c for c in candidates if c not in self._recent_resolutions[-3:]]
            if non_recent:
                base = rng_for_current_turn(
                    "text:resolution_avoid_recent_alternative",
                    stable_sub_index(candidates),
                ).choice(non_recent)
            else:
                base = rng_for_current_turn(
                    "text:resolution_avoid_recent_fallback",
                    stable_sub_index(candidates),
                ).choice(candidates)
        
        # Inject entropy: 20% chance of completely random choice
        if rng_for_current_turn(
            "text:resolution_entropy_gate",
            stable_sub_index(candidates),
        ).random() < 0.2:
            base = rng_for_current_turn(
                "text:resolution_entropy_alternative",
                stable_sub_index(candidates),
            ).choice(candidates)
        
        # Track this resolution
        self._recent_resolutions.append(base)
        
        # Keep only last 20 for memory efficiency
        if len(self._recent_resolutions) > 20:
            self._recent_resolutions = self._recent_resolutions[-20:]
        
        return base
    
    @staticmethod
    def _weighted_choice(candidates: list[str]) -> str:
        """Simple weighted choice from candidates.
        
        Args:
            candidates: List of candidate resolution types.
            
        Returns:
            Randomly selected candidate.
        """
        if len(candidates) == 1:
            return candidates[0]
        return rng_for_current_turn(
            "text:resolution_weighted_choice",
            stable_sub_index(candidates),
        ).choice(candidates)
    
    def _generate_template_resolution(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]] = None,
        world_state: Optional[dict[str, Any]] = None,
        resolution_type: str = "victory",
    ) -> str:
        """Generate resolution text from templates.
        
        Args:
            storyline: Storyline dict.
            characters: Optional character data.
            world_state: Optional world state.
            resolution_type: Type of resolution.
            
        Returns:
            Resolution text string.
        """
        story_type = storyline.get("event_type", "general")
        participants = storyline.get("participants", ["Unknown"])
        events = storyline.get("events", [])
        
        # Get character names for template substitution
        char_names = {}
        if characters:
            for char_id in participants:
                char_data = characters.get(char_id, {})
                if isinstance(char_data, dict):
                    char_names[char_id] = char_data.get("name", char_id)
                else:
                    char_names[char_id] = str(char_id)
        else:
            char_names = {cid: cid for cid in participants}
        
        # Select appropriate template category
        template_category = story_type if story_type in RESOLUTION_TEMPLATES else "general"
        templates = RESOLUTION_TEMPLATES.get(template_category, RESOLUTION_TEMPLATES["general"])
        
        # Get templates for this resolution type
        type_templates = templates.get(resolution_type, RESOLUTION_TEMPLATES["general"].get(resolution_type, []))
        if not type_templates:
            type_templates = ["The storyline concluded with lasting consequences for all involved."]
        
        # Pick a template
        template = rng_for_current_turn(
            "text:resolution_template",
            stable_sub_index({"story_type": story_type, "type": resolution_type}),
        ).choice(type_templates)
        
        # Substitute placeholders
        text = template
        replacements = {
            "{winner}": char_names.get(participants[0], "Unknown") if participants else "Unknown",
            "{loser}": char_names.get(participants[-1], "Unknown") if participants else "Unknown",
            "{character}": char_names.get(participants[0], "Unknown") if participants else "Unknown",
        }
        
        if len(participants) >= 2:
            replacements["{faction_a}"] = char_names.get(participants[0], "Unknown")
            replacements["{faction_b}"] = char_names.get(participants[1], "Unknown")
            replacements["{character_a}"] = char_names.get(participants[0], "Unknown")
            replacements["{character_b}"] = char_names.get(participants[1], "Unknown")
            replacements["{victim}"] = char_names.get(participants[-1], "Unknown")
        else:
            replacements["{faction_a}"] = replacements.get("{winner}", "Unknown")
            replacements["{faction_b}"] = "opposing forces"
            replacements["{character_a}"] = replacements.get("{character}", "Unknown")
            replacements["{character_b}"] = "others"
            replacements["{victim}"] = "an innocent party"
        
        # Add event-specific substitutions based on storyline events
        for event in events:
            if event.get("type") in ("betrayal", "betray"):
                replacements["{traitor}"] = char_names.get(
                    event.get("betrayer", participants[0]), "a trusted ally"
                )
            elif event.get("type") in ("sacrifice",):
                replacements["{sacrificer}"] = char_names.get(
                    event.get("sacrificer", participants[0]), "someone"
                )
                replacements["{sacrifice}"] = event.get("sacrificed_item", "something precious")
        
        # Add fallback substitutions for missing placeholders
        fallbacks = {
            "{casualty}": "a key figure",
            "{reward}": "the prize",
            "{loss}": "something irreplaceable",
            "{method}": "skill and determination",
        }
        replacements.update(fallbacks)
        
        # Apply substitutions
        for key, value in replacements.items():
            text = text.replace(key, value)
        
        return text
    
    def _generate_llm_resolution(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]] = None,
        world_state: Optional[dict[str, Any]] = None,
        resolution_type: str = "victory",
    ) -> str:
        """Generate resolution using LLM for high-importance storylines.
        
        Args:
            storyline: Storyline dict.
            characters: Optional character data.
            world_state: Optional world state.
            resolution_type: Target resolution type.
            
        Returns:
            Resolution text string from LLM.
        """
        if not self.llm_client:
            return self._generate_template_resolution(
                storyline, characters, world_state, resolution_type
            )
        
        prompt = self._build_llm_prompt(
            storyline, characters, world_state, resolution_type
        )
        
        try:
            # Call LLM with strict constraints
            response = self.llm_client.generate(
                prompt=prompt,
                max_tokens=100,  # Strict 1-2 sentence limit
                temperature=0.5,  # Moderate creativity, not wild randomness
            )
            
            resolution = response.strip() if response else ""
            if resolution:
                return resolution
        except Exception as e:
            logger.warning(f"LLM resolution generation failed, falling back to template: {e}")
        
        # Fallback to template
        return self._generate_template_resolution(
            storyline, characters, world_state, resolution_type
        )
    
    def _build_llm_prompt(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]] = None,
        world_state: Optional[dict[str, Any]] = None,
        resolution_type: str = "victory",
    ) -> str:
        """Build the LLM prompt for resolution generation.
        
        Args:
            storyline: Storyline dict.
            characters: Optional character data.
            world_state: Optional world state.
            resolution_type: Target resolution type.
            
        Returns:
            Prompt string for LLM.
        """
        participants = storyline.get("participants", [])
        events = storyline.get("events", [])
        story_type = storyline.get("event_type", "unknown")
        progress = storyline.get("progress", 0.5)
        
        # Build character summaries
        char_summaries = []
        if characters:
            for char_id in participants:
                char_data = characters.get(char_id, {})
                if isinstance(char_data, dict):
                    name = char_data.get("name", char_id)
                    emotions = char_data.get("emotions", {})
                    char_summaries.append(f"- {name}: emotions={emotions}")
        
        # Build event history summary
        event_history = []
        for event in events[-5:]:  # Last 5 events max
            event_desc = f"- {event.get('type', 'unknown')}: {event.get('description', '')}"
            event_history.append(event_desc)
        
        prompt = _PROMPT_1.format(v0=(story_type), v1=(', '.join(str(p) for p in participants)), v2=(resolution_type), v3=(progress), v4=(chr(10).join(event_history)), v5=(chr(10).join(char_summaries)), v6=(resolution_type))
        return prompt
    
    def _calculate_emotional_impact(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]],
        resolution_type: str,
    ) -> dict[str, float]:
        """Calculate emotional impact of resolution on participants.
        
        Args:
            storyline: Storyline dict.
            characters: Character data.
            resolution_type: Resolution type.
            
        Returns:
            Dict of emotion_name -> change_value.
        """
        impact = {}
        importance = storyline.get("importance", 0.5)
        
        # Base emotional changes by resolution type
        emotion_changes = {
            "victory": {"joy": 0.3, "pride": 0.2, "anger": -0.1},
            "compromise": {"relief": 0.2, "frustration": 0.1, "satisfaction": 0.1},
            "tragedy": {"grief": 0.4, "fear": 0.2, "anger": 0.2},
            "betrayal": {"anger": 0.4, "distrust": 0.3, "fear": 0.2},
            "redemption": {"relief": 0.3, "hope": 0.2, "pride": 0.1},
            "stalemate": {"frustration": 0.2, "weariness": 0.1, "determination": 0.1},
            "transcendence": {"peace": 0.3, "wisdom": 0.2, "gratitude": 0.1},
            "sacrifice": {"grief": 0.3, "gratitude": 0.2, "honor": 0.2},
        }
        
        base_emotions = emotion_changes.get(resolution_type, {})
        for emotion, change in base_emotions.items():
            impact[emotion] = change * importance
        
        return impact
    
    def _calculate_relationship_updates(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]],
        resolution_type: str,
    ) -> dict[str, float]:
        """Calculate relationship value updates from resolution.
        
        Args:
            storyline: Storyline dict.
            characters: Character data.
            resolution_type: Resolution type.
            
        Returns:
            Dict of relationship_key -> change_value.
        """
        participants = storyline.get("participants", [])
        importance = storyline.get("importance", 0.5)
        updates = {}
        
        # Different resolution types affect relationships differently
        relationship_changes = {
            "victory": 0.2,       # Winners closer, losers more distant
            "compromise": 0.3,    # Both sides improve relationship
            "tragedy": -0.2,     # Shared loss can unite or divide
            "betrayal": -0.5,    # Major relationship damage
            "redemption": 0.3,   # Redeemed character gains trust
            "stalemate": 0.0,    # No significant change
            "transcendence": 0.4, # Relationships strengthen
            "sacrifice": 0.3,    # Sacrifice earns respect
        }
        
        change = relationship_changes.get(resolution_type, 0.0) * importance
        
        # Update relationships between all participant pairs
        for i, char_a in enumerate(participants):
            for char_b in participants[i+1:]:
                key = f"{char_a}:{char_b}"
                updates[key] = change
        
        return updates
    
    def _determine_consequences(
        self,
        storyline: dict[str, Any],
        world_state: Optional[dict[str, Any]],
        resolution_type: str,
    ) -> list[str]:
        """Determine narrative consequences of resolution.
        
        Args:
            storyline: Storyline dict.
            world_state: World state dict.
            resolution_type: Resolution type.
            
        Returns:
            List of consequence description strings.
        """
        consequences = []
        importance = storyline.get("importance", 0.5)
        story_type = storyline.get("event_type", "general")
        
        # Type-specific consequences
        consequence_pool = {
            "faction_conflict": [
                "Political alliances have shifted",
                "Power balance in the region has changed",
                "New faction rivalries have emerged",
            ],
            "personal_conflict": [
                "Personal relationships have been altered",
                "Reputation has shifted based on outcome",
                "Future interactions will be different",
            ],
            "quest": [
                "The quest's outcome will have lasting effects",
                "Rewards have been distributed",
                "Others will hear of this quest's result",
            ],
        }
        
        # Get relevant consequences
        pool = consequence_pool.get(story_type, consequence_pool.get("general", []))
        num_consequences = min(2, len(pool)) if importance > 0.5 else 1
        
        consequences = (
            rng_for_current_turn(
                "text:resolution_consequence_sample",
                stable_sub_index(pool),
            ).sample(pool, num_consequences)
            if pool
            else ["Events will unfold"]
        )
        
        # High importance adds extra consequence
        if importance > 0.7:
            consequences.append("The resolution will be remembered for a long time")
        
        return consequences
    
    def _check_player_satisfaction(
        self,
        storyline: dict[str, Any],
        characters: Optional[dict[str, Any]],
        resolution_type: str,
    ) -> bool:
        """Check if resolution feels satisfying to the player.
        
        Player satisfaction is affected by:
        - Resolution matches their expectations/effort
        - Resolution feels meaningful, not arbitrary
        - Resolution has lasting consequences
        
        Args:
            storyline: Storyline dict.
            characters: Character data.
            resolution_type: Resolution type.
            
        Returns:
            True if resolution should satisfy player.
        """
        player_involved = storyline.get("is_player_involved", False)
        progress = storyline.get("progress", 0.5)
        importance = storyline.get("importance", 0.5)
        participants = storyline.get("participants", [])
        
        if not player_involved:
            # Player less invested, easier to satisfy
            return True
        
        # Satisfying resolution types for player-involved storylines
        satisfying_types = {"victory", "compromise", "redemption", "transcendence"}
        
        # High progress + satisfying type = satisfying resolution
        if progress > 0.7 and resolution_type in satisfying_types:
            return True
        
        # Low progress but dramatic resolution (tragedy/betrayal) can still satisfy
        if progress < 0.3 and resolution_type in {"tragedy", "betrayal", "sacrifice"}:
            return importance > 0.5
        
        # Medium progress needs meaningful resolution type
        if 0.3 <= progress <= 0.7:
            return resolution_type in satisfying_types and importance > 0.4
        
        return False
    
    def get_stats(self) -> dict[str, Any]:
        """Get resolution engine statistics.
        
        Returns:
            Stats dict.
        """
        return dict(self._stats)
    
    def reset(self) -> None:
        """Reset engine statistics."""
        self._stats = {
            "resolutions_generated": 0,
            "llm_resolutions": 0,
            "template_resolutions": 0,
            "resolution_types_used": {},
        }
