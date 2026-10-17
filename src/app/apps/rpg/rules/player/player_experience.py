"""Player Experience Module — Tier 14: Player Experience & Perception Layer.

This module implements Tier 14's core systems that transform the underlying
simulation into a compelling player experience:

1. Narrative Surfacing Engine - Decides what the player sees, compresses
   complexity into clarity
2. Attention Director - Highlights important events, hides noise
3. Emotional Feedback Loop - Shows consequences emotionally, not just logically
4. Memory Echo System - Callbacks to past arcs, creates "story continuity feeling"
5. Player Identity Model - World adapts to player's style and values

Design Principles:
    - Simulation depth should be perceived, not just exist
    - What the player sees is more important than what exists
    - Emotional resonance trumps mechanical accuracy
    - Player identity shapes world response

Usage:
    engine = PlayerExperienceEngine()
    surfaced = engine.surface_event(event, context)

Architecture:
    PlayerExperienceEngine:
    - NarrativeSurfacer: Compresses complex events into player-facing narrative
    - AttentionDirector: Filters and prioritizes what player notices
    - EmotionalFeedbackLoop: Translates mechanical changes into emotional impact
    - MemoryEchoSystem: Generates callbacks to past events
    - PlayerIdentityModel: Tracks and applies player preferences
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.apps.rpg.foundation.core.determinism import rng_for_current_turn, stable_sub_index

logger = logging.getLogger(__name__)

# Constants for surfacing thresholds
VISIBILITY_THRESHOLD = 0.3
HIGHLIGHT_THRESHOLD = 0.7
EMOTIONAL_SALIENCE_BASE = 0.2

# Player style categories
PLAYER_STYLES = [
    "aggressive",
    "diplomatic",
    "stealthy",
    "charismatic",
    "strategic",
    "altruistic",
    "pragmatic",
    "chaotic",
]

# Player value categories
PLAYER_VALUES = [
    "loyalty",
    "power",
    "freedom",
    "knowledge",
    "justice",
    "mercy",
    "honor",
    "survival",
]

# Memory echo triggers
MEMORY_ECHO_TRIGGERS = {
    "character_reunion": "meeting character after absence",
    "location_return": "returning to significant location",
    "theme_recurrence": "encountering similar situation",
    "consequence_manifest": "past action having delayed impact",
    "emotional_parallel": "current emotion mirrors past event",
}


@dataclass
class SurfacedEvent:
    """A processed event ready for player presentation.

    Attributes:
        headline: Short, attention-grabbing summary.
        detail: Expanded description with context.
        emotional_tone: Primary emotional quality.
        visibility: How visible/prominent this event is (0.0-1.0).
        should_highlight: Whether to draw special attention.
        memory_echo: Optional callback to a past event.
        player_relevance: How relevant this is to the player (0.0-1.0).
        raw_event: Original event data for reference.
    """

    headline: str = ""
    detail: str = ""
    emotional_tone: str = "neutral"
    visibility: float = 0.5
    should_highlight: bool = False
    memory_echo: Optional[str] = None
    player_relevance: float = 0.5
    raw_event: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "headline": self.headline,
            "detail": self.detail,
            "emotional_tone": self.emotional_tone,
            "visibility": self.visibility,
            "should_highlight": self.should_highlight,
            "memory_echo": self.memory_echo,
            "player_relevance": self.player_relevance,
        }


@dataclass
class PlayerProfile:
    """Tracks player identity, preferences, and behavioral patterns.

    Attributes:
        play_style: Dominant player approach (aggressive, diplomatic, etc.).
        values: What the player prioritizes (loyalty, power, etc.).
        emotional_preferences: Preferred emotional tones in narrative.
        attention_patterns: What types of events player engages with most.
        relationship_history: Key relationships the player has formed.
        narrative_preferences: Preferred story types and pacing.
        interaction_count: Total interactions for behavior analysis.
        style_confidence: How confident we are in style classification.
    """

    play_style: str = "balanced"
    values: List[str] = field(default_factory=lambda: ["freedom", "loyalty"])
    emotional_preferences: Dict[str, float] = field(default_factory=dict)
    attention_patterns: Dict[str, float] = field(default_factory=dict)
    relationship_history: Dict[str, float] = field(default_factory=dict)
    narrative_preferences: Dict[str, float] = field(default_factory=lambda: {
        "action": 0.5,
        "dialogue": 0.5,
        "exploration": 0.5,
        "intrigue": 0.5,
    })
    interaction_count: int = 0
    style_confidence: float = 0.0

    def update_style(self, action_type: str, weight: float = 1.0) -> None:
        """Update play style based on player action."""
        self.interaction_count += 1

        current = self.attention_patterns.get(action_type, 0)
        self.attention_patterns[action_type] = current + weight

        if self.interaction_count >= 5:
            self._recalculate_style()

    def update_value_alignment(self, value: str, alignment: float) -> None:
        """Update alignment with a player value."""
        current = self.emotional_preferences.get(value, 0.0)
        self.emotional_preferences[value] = min(1.0, current + alignment * 0.1)

    def record_relationship(self, character_id: str, quality: float) -> None:
        """Record quality of relationship with a character."""
        self.relationship_history[character_id] = quality

    def _recalculate_style(self) -> None:
        """Recalculate dominant play style from action patterns."""
        if not self.attention_patterns:
            return

        total = sum(self.attention_patterns.values())
        if total == 0:
            return

        # Normalize
        normalized = {
            k: v / total for k, v in self.attention_patterns.items()
        }

        # Map to known styles
        style_mapping = {
            "attack": "aggressive",
            "fight": "aggressive",
            "aggressive": "aggressive",
            "negotiate": "diplomatic",
            "persuade": "diplomatic",
            "diplomatic": "diplomatic",
            "sneak": "stealthy",
            "hide": "stealthy",
            "stealthy": "stealthy",
            "charm": "charismatic",
            "inspire": "charismatic",
            "charismatic": "charismatic",
            "plan": "strategic",
            "analyze": "strategic",
            "strategic": "strategic",
            "help": "altruistic",
            "protect": "altruistic",
            "altruistic": "altruistic",
            "adapt": "pragmatic",
            "compromise": "pragmatic",
            "pragmatic": "pragmatic",
            "disrupt": "chaotic",
            "random": "chaotic",
            "chaotic": "chaotic",
        }

        style_scores: Dict[str, float] = defaultdict(float)
        for action, score in normalized.items():
            mapped = style_mapping.get(action, action)
            if mapped in PLAYER_STYLES:
                style_scores[mapped] += score

        if style_scores:
            dominant = max(style_scores.items(), key=lambda x: x[1])
            self.play_style = dominant[0]
            self.style_confidence = min(1.0, dominant[1] * 2)

    def matches_value(self, value: str, threshold: float = 0.5) -> bool:
        """Check if player identifies with a value."""
        return value in self.values or self.emotional_preferences.get(value, 0) > threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "play_style": self.play_style,
            "values": list(self.values),
            "style_confidence": self.style_confidence,
            "top_relationships": dict(
                sorted(self.relationship_history.items(), key=lambda x: -x[1])[:5]
            ),
            "interaction_count": self.interaction_count,
        }


@dataclass
class MemoryEcho:
    """A callback to a past event that enhances narrative continuity.

    Attributes:
        echo_type: Type of memory connection.
        past_event_description: Brief description of the past event.
        relevance_score: How relevant this echo is to current situation.
        emotional_weight: Emotional impact of the memory.
    """

    echo_type: str = "general"
    past_event_description: str = ""
    relevance_score: float = 0.0
    emotional_weight: float = 0.0

    def format(self) -> str:
        """Format the echo for presentation."""
        templates = {
            "character_reunion": "This reminds you of {desc}",
            "location_return": "Being here again brings back memories of {desc}",
            "theme_recurrence": "This echoes a familiar pattern: {desc}",
            "consequence_manifest": "The ripple of past choices surfaces: {desc}",
            "emotional_parallel": "The feeling is hauntingly familiar: {desc}",
            "general": "This calls to mind: {desc}",
        }
        template = templates.get(self.echo_type, templates["general"])
        return template.format(desc=self.past_event_description)


class NarrativeSurfacer:
    """Compresses complex simulation events into player-facing narrative.

    The surfacer decides WHAT the player sees from the underlying simulation,
    translating mechanical depth into narrative clarity.

    Key Methods:
    - surface_simple: Basic event surfacing
    - surface_contextual: Event with relationship/world context
    - surface_emotional: Event with emotional framing
    """

    def __init__(self):
        """Initialize the NarrativeSurfacer."""
        self._events_processed = 0
        self._headline_templates: Dict[str, List[str]] = {
            "faction_conflict": [
                "Tensions rise between {faction_a} and {faction_b}",
                "{faction_a} and {faction_b} clash over {issue}",
                "A power struggle erupts: {faction_a} vs {faction_b}",
            ],
            "alliance_formed": [
                "An unlikely alliance forms between {faction_a} and {faction_b}",
                "{faction_a} and {faction_b} unite against a common concern",
                "New pact: {faction_a} and {faction_b} join forces",
            ],
            "betrayal": [
                "Trust is shattered: {betrayer} turns against {victim}",
                "A shocking betrayal rocks {group}",
                "{betrayer}'s treachery changes everything",
            ],
            "quest_complete": [
                "The quest for {objective} reaches its conclusion",
                "After much struggle, {objective} is achieved",
                "A journey ends: {objective} accomplished",
            ],
            "character_growth": [
                "{character} has changed in ways no one expected",
                "A pivotal moment for {character}",
                "{character} faces a reckoning",
            ],
            "death": [
                "A life is lost: {character}'s story ends",
                "Grief spreads as {character} falls",
                "The world feels dimmer without {character}",
            ],
            "discovery": [
                "A hidden truth comes to light",
                "Secrets unravel: {discovery} revealed",
                "What was concealed can no longer stay hidden",
            ],
            "general": [
                "Events unfold in {location}",
                "Something stirs in {location}",
                "The situation in {location} develops",
            ],
        }

    def surface(
        self,
        event: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SurfacedEvent:
        """Surface an event for player presentation.

        Args:
            event: The raw event data.
            context: Optional contextual information (relationships, world state).

        Returns:
            SurfacedEvent ready for presentation.
        """
        self._events_processed += 1

        event_type = event.get("type", "general")
        importance = event.get("importance", 0.5)

        # Generate headline
        headline = self._generate_headline(event, event_type)

        # Generate detail
        detail = self._generate_detail(event, event_type, context)

        # Determine emotional tone
        emotional_tone = self._determine_emotional_tone(event, context)

        # Calculate visibility
        visibility = self._calculate_visibility(importance, event_type)

        # Check for memory echoes
        memory_echo = self._check_memory_echo(event, context)

        # Calculate player relevance
        player_relevance = importance  # Default to importance

        should_highlight = visibility >= HIGHLIGHT_THRESHOLD

        return SurfacedEvent(
            headline=headline,
            detail=detail,
            emotional_tone=emotional_tone,
            visibility=visibility,
            should_highlight=should_highlight,
            memory_echo=memory_echo,
            player_relevance=player_relevance,
            raw_event=event,
        )

    def _generate_headline(
        self,
        event: Dict[str, Any],
        event_type: str,
    ) -> str:
        """Generate attention-grabbing headline."""
        templates = self._headline_templates.get(event_type, self._headline_templates["general"])
        template = rng_for_current_turn(
            "text:player_experience_headline",
            stable_sub_index(event),
        ).choice(templates)

        # Substitute placeholders
        replacements = {
            "{faction_a}": event.get("faction_a", "Faction A"),
            "{faction_b}": event.get("faction_b", "Faction B"),
            "{character}": event.get("character", "Someone"),
            "{location}": event.get("location", "the distance"),
            "{issue}": event.get("issue", "matters of importance"),
            "{objective}": event.get("objective", "their goal"),
            "{betrayer}": event.get("betrayer", "a trusted ally"),
            "{victim}": event.get("victim", "an unsuspecting party"),
            "{group}": event.get("group", "the community"),
            "{discovery}": event.get("discovery", "something hidden"),
        }

        for key, value in replacements.items():
            template = template.replace(key, value)

        return template

    def _generate_detail(
        self,
        event: Dict[str, Any],
        event_type: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate expanded event description."""
        descriptions = {
            "faction_conflict": (
                "The ongoing tensions between {faction_a} and {faction_b} "
                "have reached a critical point. Observers note the potential "
                "for wider repercussions across the region."
            ),
            "alliance_formed": (
                "In an unexpected turn, {faction_a} and {faction_b} have "
                "forged a partnership. Both sides stand to gain, though "
                "old alliances may strain under the new arrangement."
            ),
            "betrayal": (
                "What seemed like loyalty proved to be deception. "
                "{betrayer}'s actions against {victim} have sent shockwaves "
                "through established power structures."
            ),
            "quest_complete": (
                "The long journey for {objective} has concluded. "
                "The outcome will resonate for some time, affecting "
                "all who were invested in its success."
            ),
            "character_growth": (
                "{character} has undergone a significant transformation. "
                "Those who know them well notice the change, and it "
                "may reshape future interactions."
            ),
            "death": (
                "{character} has fallen. The loss reverberates through "
                "their circle of influence, and the void they leave "
                "will not easily be filled."
            ),
            "discovery": (
                "New information has come to light. {discovery} changes "
                "the understanding of those who have learned it, and "
                "may alter courses of action going forward."
            ),
        }

        detail = descriptions.get(event_type, descriptions.get("general",
            "Events continue to unfold. Observers watch with interest."
        ))

        for key, value in {
            "{faction_a}": event.get("faction_a", "Faction A"),
            "{faction_b}": event.get("faction_b", "Faction B"),
            "{character}": event.get("character", "Someone"),
            "{objective}": event.get("objective", "their goal"),
            "{betrayer}": event.get("betrayer", "a trusted ally"),
            "{victim}": event.get("victim", "an unsuspecting party"),
            "{discovery}": event.get("discovery", "new information"),
        }.items():
            detail = detail.replace(key, value)

        # Add context if available
        if context:
            relationships = context.get("relationships", {})
            if relationships:
                detail += " Relationship dynamics may influence the outcome."

        return detail

    def _determine_emotional_tone(
        self,
        event: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Determine the emotional tone of the event."""
        event_type = event.get("type", "general").lower()
        emotions = event.get("emotions", {})

        # Event type mapping
        type_tones = {
            "death": "grief",
            "betrayal": "shock",
            "attack": "tension",
            "conflict": "tension",
            "victory": "triumph",
            "quest_complete": "satisfaction",
            "alliance_formed": "hope",
            "discovery": "wonder",
            "character_growth": "inspiration",
            "romance": "warmth",
            "gift": "warmth",
            "help": "warmth",
        }

        # Check for emotion overrides
        dominant_emotion = "neutral"
        if emotions:
            dominant_emotion = max(emotions.items(), key=lambda x: x[1])[0]
            if emotions[dominant_emotion] > 0.5:
                return dominant_emotion

        return type_tones.get(event_type, "neutral")

    def _calculate_visibility(
        self,
        importance: float,
        event_type: str,
    ) -> float:
        """Calculate how visible this event should be."""
        base = importance

        # Bonus for dramatic event types
        visibility_modifiers = {
            "death": 0.3,
            "betrayal": 0.25,
            "quest_complete": 0.2,
            "faction_conflict": 0.15,
            "discovery": 0.1,
        }

        return min(1.0, base + visibility_modifiers.get(event_type, 0.0))

    def _check_memory_echo(
        self,
        event: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Check if this event should trigger a memory echo."""
        if context is None:
            return None

        memory_bank = context.get("memory_bank", {})
        if not memory_bank:
            return None

        # Check for returning characters
        character = event.get("character", "")
        if character and character in memory_bank.get("significant_characters", []):
            return f"seeing {character} again after everything that's happened"

        # Check for thematic parallels
        event_type = event.get("type", "")
        past_events = memory_bank.get("past_events", [])
        for past in past_events[-10:]:
            if past.get("type") == event_type:
                return f"a situation reminiscent of {past.get('description', 'the past')}"

        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get surfacer statistics."""
        return {"events_processed": self._events_processed}


class AttentionDirector:
    """Filters and prioritizes what the player notices.

    Prevents information overload while ensuring important events
    are properly highlighted.

    Key Methods:
    - filter: Reduce event stream to what player should notice
    - prioritize: Rank events by attention value
    - schedule: Determine timing of event presentation
    """

    def __init__(self, max_events_per_tick: int = 3):
        """Initialize the AttentionDirector.

        Args:
            max_events_per_tick: Maximum events to surface per tick.
        """
        self.max_events_per_tick = max_events_per_tick
        self._attention_budget = 1.0
        self._recent_events: List[Tuple[int, float]] = []
        self._fatigue_factor = 0.0

    def filter_events(
        self,
        events: List[Dict[str, Any]],
        current_tick: int = 0,
        player_profile: Optional[PlayerProfile] = None,
    ) -> List[Dict[str, Any]]:
        """Filter events to what the player should notice.

        Args:
            events: List of raw events.
            current_tick: Current game tick.
            player_profile: Optional player identity model.

        Returns:
            Filtered and sorted list of events worth noticing.
        """
        if not events:
            return []

        # Score events
        scored_events = []
        for event in events:
            score = self._score_event(event, current_tick, player_profile)
            scored_events.append((score, event))

        # Sort by score descending
        scored_events.sort(key=lambda x: -x[0])

        # Take top N
        selected = scored_events[:self.max_events_per_tick]

        # Track attention budget
        total_score = sum(s for s, _ in selected)
        self._recent_events.append((current_tick, total_score))
        if len(self._recent_events) > 10:
            self._recent_events = self._recent_events[-10:]

        return [event for _, event in selected]

    def prioritize(
        self,
        events: List[Dict[str, Any]],
        player_profile: Optional[PlayerProfile] = None,
    ) -> List[Tuple[float, Dict[str, Any]]]:
        """Rank events by priority for presentation.

        Args:
            events: List of events to rank.
            player_profile: Optional player profile.

        Returns:
            List of (priority_score, event) tuples, sorted by priority.
        """
        scored = []
        for event in events:
            score = self._score_event(event, player_profile=player_profile)
            scored.append((score, event))

        scored.sort(key=lambda x: -x[0])
        return scored

    def get_attention_budget(self) -> float:
        """Get remaining attention budget (0.0-1.0)."""
        if not self._recent_events:
            return 1.0
        avg_load = sum(s for _, s in self._recent_events) / len(self._recent_events)
        return max(0.0, 1.0 - avg_load * 0.1)

    def _score_event(
        self,
        event: Dict[str, Any],
        current_tick: int = 0,
        player_profile: Optional[PlayerProfile] = None,
    ) -> float:
        """Score an event for attention priority."""
        base_score = event.get("importance", 0.5)

        # Player involvement bonus
        if event.get("player_involved", False):
            base_score *= 1.5

        # Player relevance bonus
        if player_profile:
            event_type = event.get("type", "")
            style_relevant = event_type in player_profile.attention_patterns
            if style_relevant:
                base_score *= 1.2

            # Relationship relevance
            for char_id in event.get("characters", []):
                if char_id in player_profile.relationship_history:
                    base_score *= 1.3

        # Fatigue factor (recent high-attention events reduce budget)
        fatigue = self._fatigue_factor
        base_score *= (1.0 - fatigue * 0.3)

        # Update fatigue
        if base_score > HIGHLIGHT_THRESHOLD:
            self._fatigue_factor = min(1.0, self._fatigue_factor + 0.1)
        else:
            self._fatigue_factor = max(0.0, self._fatigue_factor - 0.02)

        return min(1.0, base_score)


class EmotionalFeedbackLoop:
    """Translates mechanical game changes into emotional player feedback.

    Shows consequences emotionally, not just logically.

    Key Methods:
    - translate: Convert mechanical changes to emotional narrative
    - amplify: Boost emotional signal for important events
    - dampen: Reduce noise for minor events
    """

    def __init__(self):
        """Initialize the EmotionalFeedbackLoop."""
        self._feedback_history: List[Dict[str, Any]] = []
        self._emotional_patterns: Dict[str, int] = Counter()

    def translate(
        self,
        mechanical_change: Dict[str, Any],
        player_profile: Optional[PlayerProfile] = None,
    ) -> Dict[str, Any]:
        """Translate a mechanical change into emotional feedback.

        Args:
            mechanical_change: Raw game state change.
            player_profile: Optional player profile.

        Returns:
            Emotional feedback dict with narrative framing.
        """
        change_type = mechanical_change.get("type", "unknown")
        magnitude = mechanical_change.get("magnitude", 0.5)

        # Emotional translation table
        emotional_mappings = {
            "reputation_decrease": {
                "emotion": "isolation",
                "narrative": "You feel the weight of others' disapproval",
            },
            "reputation_increase": {
                "emotion": "validation",
                "narrative": "Your deeds are noticed and appreciated",
            },
            "relationship_damage": {
                "emotion": "regret",
                "narrative": "The bond between you strains under recent events",
            },
            "relationship_growth": {
                "emotion": "connection",
                "narrative": "Something shifts between you — a strengthening of trust",
            },
            "power_loss": {
                "emotion": "vulnerability",
                "narrative": "You feel your influence slipping away",
            },
            "power_gain": {
                "emotion": "empowerment",
                "narrative": "New possibilities open before you",
            },
            "resource_loss": {
                "emotion": "anxiety",
                "narrative": "Resources grow scarce; uncertainty creeps in",
            },
            "resource_gain": {
                "emotion": "security",
                "narrative": "For now, you can breathe easier",
            },
            "betrayal": {
                "emotion": "shock",
                "narrative": "The betrayal cuts deep — trust is harder now",
            },
            "loyalty_test": {
                "emotion": "conflict",
                "narrative": "You're torn between competing loyalties",
            },
        }

        mapping = emotional_mappings.get(change_type, {
            "emotion": "uncertainty",
            "narrative": "Something shifts, though the full meaning remains unclear",
        })

        emotional_feedback = {
            "emotion": mapping["emotion"],
            "narrative": mapping["narrative"],
            "intensity": magnitude,
            "personal_relevance": 0.5,
        }

        # Adjust for player profile
        if player_profile:
            values = player_profile.values
            if change_type in values:
                emotional_feedback["intensity"] *= 1.5
                emotional_feedback["personal_relevance"] = 0.8

        # Track pattern
        self._emotional_patterns[mapping["emotion"]] += 1
        self._feedback_history.append({
            "type": change_type,
            "emotion": mapping["emotion"],
            "tick": mechanical_change.get("tick", 0),
        })

        return emotional_feedback

class MemoryEchoSystem:
    """Generates callbacks to past events for narrative continuity.

    Creates the "story continuity feeling" by connecting current events
    to significant past events.

    Key Methods:
    - record_event: Store event for future callbacks
    - find_echo: Find relevant past event for current situation
    - generate_echo: Format the callback for presentation
    """

    def __init__(self, max_memories: int = 50):
        """Initialize the MemoryEchoSystem.

        Args:
            max_memories: Maximum memories to retain.
        """
        self.max_memories = max_memories
        self._memories: List[Dict[str, Any]] = []
        self._significant_characters: set = set()
        self._significant_locations: set = set()
        self._echoes_generated = 0

    def record_event(
        self,
        event: Dict[str, Any],
        significance: float = 0.5,
    ) -> None:
        """Record an event for potential future callbacks.

        Args:
            event: Event to record.
            significance: Event significance score (0.0-1.0).
        """
        if significance < VISIBILITY_THRESHOLD:
            return

        memory = {
            "event": event,
            "significance": significance,
            "characters": set(event.get("characters", [])),
            "locations": set(event.get("locations", [])),
            "themes": set(event.get("themes", [])),
            "emotions": event.get("emotions", {}),
            "tick": event.get("tick", 0),
        }

        self._memories.append(memory)
        self._significant_characters.update(memory["characters"])
        self._significant_locations.update(memory["locations"])

        # Prune if necessary
        if len(self._memories) > self.max_memories:
            self._memories.sort(key=lambda m: -m["significance"])
            self._memories = self._memories[:self.max_memories]

    def find_echo(
        self,
        current_context: Dict[str, Any],
    ) -> Optional[MemoryEcho]:
        """Find a relevant memory echo for the current context.

        Args:
            current_context: Current situation context.

        Returns:
            MemoryEcho if a relevant memory is found, else None.
        """
        if not self._memories:
            return None

        # Score memories for relevance
        scored = []
        current_characters = set(current_context.get("characters", []))
        current_locations = set(current_context.get("locations", []))
        current_themes = set(current_context.get("themes", []))
        current_emotions = current_context.get("emotions", {})

        for memory in self._memories:
            score = 0.0
            echo_type = "general"

            # Character overlap
            characters_overlap = current_characters & memory["characters"]
            if characters_overlap:
                score += len(characters_overlap) * 0.3
                echo_type = "character_reunion"

            # Location overlap
            locations_overlap = current_locations & memory["locations"]
            if locations_overlap:
                score += len(locations_overlap) * 0.2
                if echo_type == "general":
                    echo_type = "location_return"

            # Theme overlap
            themes_overlap = current_themes & memory["themes"]
            if themes_overlap:
                score += len(themes_overlap) * 0.25
                if echo_type == "general":
                    echo_type = "theme_recurrence"

            # Consequence manifestation (time-based)
            time_gap = current_context.get("tick", 0) - memory["tick"]
            if 10 < time_gap < 100:
                score += 0.1
                if echo_type == "general":
                    echo_type = "consequence_manifest"

            # Emotional parallel
            if current_emotions and memory["emotions"]:
                common_emotions = set(current_emotions.keys()) & set(memory["emotions"].keys())
                if common_emotions:
                    score += 0.15
                    if echo_type == "general":
                        echo_type = "emotional_parallel"

            # Base significance
            score += memory["significance"] * 0.2

            if score > VISIBILITY_THRESHOLD:
                scored.append((score, echo_type, memory))

        if not scored:
            return None

        scored.sort(key=lambda x: -x[0])
        best_score, echo_type, memory = scored[0]

        event = memory["event"]
        description = event.get("description", event.get("type", "a past event"))

        self._echoes_generated += 1

        return MemoryEcho(
            echo_type=echo_type,
            past_event_description=description,
            relevance_score=best_score,
            emotional_weight=memory["significance"],
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get echo system statistics."""
        return {
            "memories_stored": len(self._memories),
            "significant_characters": len(self._significant_characters),
            "significant_locations": len(self._significant_locations),
            "echoes_generated": self._echoes_generated,
        }



# Re-exported here for the historical public API.
from app.apps.rpg.rules.player.player_experience_engine import PlayerExperienceEngine  # noqa: E402,F401
