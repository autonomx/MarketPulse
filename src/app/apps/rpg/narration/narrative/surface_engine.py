"""Narrative Surface Engine — Tier 14: Player Experience & Perception Layer.

This module implements the player-facing narrative layer that surfaces
internal simulation depth into compelling player-perceived narrative.

Problem:
    The simulation has incredible internal depth (emotions, memory, factions),
    but none of it is visible to the player.
    
    Result: Player sees bland, repetitive events despite complex underlying systems.

Solution:
    NarrativeSurfaceEngine translates internal game state into rich,
    player-facing narrative output with:
    - Headline generation (attention-grabbing summaries)
    - Emotional context framing
    - Memory echo callbacks to past events
    - Relationship-aware descriptions

Usage:
    engine = NarrativeSurfaceEngine()
    narration = engine.narrate(event, world_state, player)

Architecture:
    Input: Internal event dict + world state + player profile
    Output: {
        "headline": "Tension Rises Between Alice and Bob",
        "description": "Their alliance shows signs of fracture.",
        "emotional_context": "Alice seems distrustful.",
        "memory_echo": "This mirrors the conflict from years ago.",
    }

Design Rules:
    - Every surfaced event should feel narratively distinct
    - Emotional context should reflect actual simulation state
    - Memory echoes should connect to real past events
    - Headlines should be attention-grabbing, not mechanical
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Dict, List, Optional

from app.apps.rpg.foundation.core.determinism import rng_for_current_turn, stable_sub_index

logger = logging.getLogger(__name__)

# Headline templates by event type and emotional tone
HEADLINE_TEMPLATES: Dict[str, Dict[str, List[str]]] = {
    "faction_conflict": {
        "tension": [
            "Tension Erupts Between {faction_a} and {faction_b}",
            "{faction_a} and {faction_b} Clash Over {issue}",
            "A Power Struggle Shakes the Alliance",
        ],
        "crisis": [
            "{faction_a} vs {faction_b}: War Drums Sound",
            "Crisis Point: {faction_a} and {faction_b} on the Brink",
            "The Fracture Between {faction_a} and {faction_b} Deepens",
        ],
        "neutral": [
            "Events Unfold Between {faction_a} and {faction_b}",
            "The Situation Between Factions Develops",
            "New Tensions Emerge in the Political Landscape",
        ],
    },
    "alliance_formed": {
        "hope": [
            "An Unlikely Alliance: {faction_a} and {faction_b} Join Forces",
            "New Pact Forged Between {faction_a} and {faction_b}",
            "Unity Against Adversity: {faction_a} Joins {faction_b}",
        ],
        "neutral": [
            "A New Alliance Takes Shape",
            "{faction_a} and {faction_b} Find Common Ground",
            "The Balance of Power Shifts",
        ],
    },
    "betrayal": {
        "shock": [
            "A Shocking Betrayal: {betrayer} Turns Against {victim}",
            "Trust Shattered: {betrayer}'s Treachery Revealed",
            "The Knife in the Dark: {betrayer} Betrays {victim}",
        ],
        "anger": [
            "{betrayer}'s Betrayal Rocks the {group}",
            "The Ultimate Treachery: {betrayer} Strikes at {victim}",
            "Loyalty Broken: {betrayer} Double-Crosses {victim}",
        ],
    },
    "death": {
        "grief": [
            "A Life Cut Short: {character} Falls",
            "Mourning Spreads as {character} Perishes",
            "The World Is Dimmer Without {character}",
        ],
        "shock": [
            "{character}'s Sudden Death Stuns Everyone",
            "Tragedy Strikes: {character} Is No More",
            "A Fatal Blow: {character}'s Story Ends",
        ],
    },
    "character_growth": {
        "inspiration": [
            "{character}'s Transformation Inspires All Who Witness It",
            "Against All Odds, {character} Rises",
            "{character} Faces a Reckoning and Emerges Changed",
        ],
        "neutral": [
            "{character}'s Journey Takes a New Turn",
            "A Pivotal Moment for {character}",
            "{character} Is Not Who They Used to Be",
        ],
    },
    "quest_complete": {
        "satisfaction": [
            "The Quest for {objective} Reaches Its Triumphant End",
            "Against All Odds, {objective} Is Secured",
            "Victory at Last: The Quest Succeeds",
        ],
        "neutral": [
            "The Quest Concludes With Lasting Consequences",
            "A Journey Ends, a New Era Begins",
            "The Outcome of {objective} Reshapes Everything",
        ],
    },
    "discovery": {
        "wonder": [
            "A Hidden Truth Revealed: {discovery}",
            "Secrets Unearthed: What Was Hidden Can Stay Hidden No More",
            "The Veil Lifts: {discovery} Changes Everything",
        ],
        "neutral": [
            "New Information Comes to Light",
            "A Discovery That Could Alter the Course of Events",
            "What Was Concealed Is Now Known",
        ],
    },
    "general": {
        "neutral": [
            "Events Unfold in {location}",
            "Something Stirs in {location}",
            "The Situation in {location} Develops",
        ],
    },
}

# Description templates
DESCRIPTION_TEMPLATES: Dict[str, List[str]] = {
    "faction_conflict": [
        "The ongoing tensions between {faction_a} and {faction_b} have reached a critical point. "
        "The {issue} dispute that has simmered for months is now boiling over, with neither side willing to back down.",
        "What began as political maneuvering has escalated into open conflict. "
        "Both {faction_a} and {faction_b} are mobilizing their resources, and neutral parties are being forced to choose sides.",
    ],
    "alliance_formed": [
        "In an unexpected turn, {faction_a} and {faction_b} have forged a partnership. "
        "Though their reasons differ, both sides recognize the strength that unity provides against their common challenges.",
        "The alliance between {faction_a} and {faction_b} has shifted the political landscape. "
        "Old rivalries are set aside, at least for now, as both factions see advantage in cooperation.",
    ],
    "betrayal": [
        "What seemed like loyalty proved to be calculated deception. "
        "{betrayer}'s actions against {victim} have sent shockwaves through established alliances, leaving everyone questioning who to trust.",
        "The betrayal by {betrayer} against {victim} was both sudden and devastating. "
        "The fallout has already begun to reshape relationships across the entire region.",
    ],
    "death": [
        "{character}'s death leaves a void that will not easily be filled. "
        "Friends and allies mourn, while enemies reassess the new balance of power.",
        "The loss of {character} reverberates through all levels of society. "
        "What comes next depends on who steps forward to take their place.",
    ],
    "character_growth": [
        "{character} has undergone a transformation that surprised even those who know them best. "
        "The changes are subtle but profound, hinting at a different path forward.",
        "What began as a small shift in perspective has blossomed into genuine change for {character}. "
        "Those who interact with them notice a new quality that was absent before.",
    ],
    "quest_complete": [
        "The journey for {objective} has concluded, but its impact will be felt long after the final moment. "
        "Those who invested in its success find their circumstances changed, for better or worse.",
        "Against formidable odds, the quest for {objective} has reached its conclusion. "
        "The rewards—and the consequences—will reshape relationships for some time.",
    ],
    "discovery": [
        "New information about {discovery} has come to light, challenging assumptions that many had held for granted. "
        "Those who learn of it must now reconsider their positions and plans.",
        "The revelation of {discovery} sends ripples through the community. "
        "Knowledge, once shared, cannot be unshared—and its implications are far-reaching.",
    ],
}


class NarrativeSurfaceEngine:
    """Translates internal simulation events into player-facing narrative.
    
    This engine is the bridge between simulation depth and player perception.
    It ensures that the rich internal state (emotions, memory, relationships)
    is surfaced to the player in a compelling, narratively satisfying way.
    
    Key Methods:
    - narrate: Full narration for an event with all layers
    - _headline: Generate attention-grabbing headline
    - _describe: Generate contextual description
    - _emotion: Extract emotional context
    - _memory: Generate memory echo callback
    """
    
    def __init__(
        self,
        memory_system: Optional[Any] = None,
        headline_cache_size: int = 20,
        description_cache_size: int = 20,
    ):
        """Initialize the NarrativeSurfaceEngine.
        
        Args:
            memory_system: Optional memory system for generating echoes.
            headline_cache_size: Max recent headlines to track for anti-repeat.
            description_cache_size: Max recent descriptions to track.
        """
        self._events_processed = 0
        self._headline_variety: List[str] = []
        self._memory_system = memory_system
        
        # Anti-repeat caches (Tier 14 Critical Patch)
        self._recent_headlines: deque = deque(maxlen=headline_cache_size)
        self._recent_descriptions: deque = deque(maxlen=description_cache_size)
        
        self._stats = {
            "events_surfaced": 0,
            "headlines_generated": 0,
            "memory_echoes": 0,
            "emotional_contexts": 0,
        }
    
    def narrate(
        self,
        event: Dict[str, Any],
        world: Optional[Dict[str, Any]] = None,
        player: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate full player-facing narration for an event.
        
        This is the main entry point for the narrative surfacing engine.
        It combines all layers (headline, description, emotion, memory)
        into a cohesive player-facing narrative output.
        
        Args:
            event: Internal event dict from simulation.
            world: Optional world state for context.
            player: Optional player profile for personalization.
            
        Returns:
            Dict with keys:
                - headline: Attention-grabbing summary
                - description: Expanded event description
                - emotional_context: Emotional framing
                - memory_echo: Callback to past event (if applicable)
            Or None if event importance is below threshold.
                
        Example:
            {
                "headline": "Tension Rises Between Alice and Bob",
                "description": "Their alliance shows signs of fracture.",
                "emotional_context": "Alice seems distrustful.",
                "memory_echo": "This mirrors the conflict from years ago.",
            }
        """
        # Fix 1: Enforce normalization at the entry point
        try:
            from app.apps.rpg.narration.narrative.event_adapter import normalize_event
        except ModuleNotFoundError:
            from app.apps.rpg.narration.narrative.event_adapter import normalize_event
        event = normalize_event(event)

        # Fix 5: Filter low-importance events to avoid narrative noise
        importance = event.get("importance", 0.5)
        if importance < 0.2:
            return None

        self._events_processed += 1
        self._stats["events_surfaced"] += 1

        # Fix 6: Resolution awareness — success/failure tone
        success = event.get("success")
        if success is False:
            return {
                "headline": self._headline(event),
                "description": "The attempt fails, leaving consequences in its wake.",
                "emotional_context": self._emotion(event),
                "memory_echo": self._memory(event, player),
            }
        
        return {
            "headline": self._headline(event),
            "description": self._describe(event, world),
            "emotional_context": self._emotion(event),
            "memory_echo": self._memory(event, player),
        }
    
    def _headline(self, event: Dict[str, Any]) -> str:
        """Generate attention-grabbing headline for player.
        
        Uses event type and emotional tone to select an appropriate
        headline that feels dynamic and narratively interesting.
        
        Anti-repeat logic ensures the same headline isn't generated
        too frequently, maintaining narrative diversity.
        
        Args:
            event: Event dict with type and optional emotion data.
            
        Returns:
            Headline string.
        """
        self._stats["headlines_generated"] += 1
        
        event_type = event.get("type", "general")
        emotional_tone = self._determine_headline_tone(event)
        
        # Get templates for this event type and tone
        type_templates = HEADLINE_TEMPLATES.get(event_type, HEADLINE_TEMPLATES["general"])
        tone_templates = type_templates.get(emotional_tone, type_templates.get("neutral", ["Something Happens"]))
        
        # Anti-repeat: try to find a non-recent headline
        headline = self._pick_headline_with_fallback(tone_templates, event)
        
        # Track headline variety for testing
        self._headline_variety.append(headline)
        if len(self._headline_variety) > 100:
            self._headline_variety = self._headline_variety[-100:]
        
        # Add to recent headlines cache
        self._recent_headlines.append(headline)
        
        return headline
    
    def _pick_headline_with_fallback(
        self,
        templates: List[str],
        event: Dict[str, Any],
        max_attempts: int = 5,
    ) -> str:
        """Pick a headline template avoiding recent repeats.
        
        Args:
            templates: List of candidate templates.
            event: Event dict for substitution.
            max_attempts: Max attempts to find non-repeated headline.
            
        Returns:
            Selected headline with placeholders substituted.
        """
        event_index = stable_sub_index(event)
        for attempt in range(max_attempts):
            template = rng_for_current_turn(
                "text:surface_headline",
                event_index + attempt,
            ).choice(templates)
            candidate = self._substitute(template, event)
            if candidate not in self._recent_headlines:
                return candidate
        
        # If all are repeated, just pick one (fallback)
        return self._substitute(
            rng_for_current_turn(
                "text:surface_headline_fallback",
                event_index,
            ).choice(templates),
            event,
        )
    
    def _describe(
        self,
        event: Dict[str, Any],
        world: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate contextual event description.
        
        Anti-repeat logic ensures descriptions don't feel repetitive
        even when the same event type fires multiple times.
        
        Args:
            event: Event dict.
            world: Optional world state.
            
        Returns:
            Description string.
        """
        event_type = event.get("type", "general")
        
        # Get description templates for this event type
        desc_templates = DESCRIPTION_TEMPLATES.get(event_type, DESCRIPTION_TEMPLATES.get("general", ["Events continue to unfold."]))
        
        # Anti-repeat: try to pick a non-recent description
        description = self._pick_description_with_fallback(desc_templates, event, world)
        
        # Add to recent descriptions cache
        self._recent_descriptions.append(description)
        
        return description
    
    def _pick_description_with_fallback(
        self,
        templates: List[str],
        event: Dict[str, Any],
        world: Optional[Dict[str, Any]] = None,
        max_attempts: int = 5,
    ) -> str:
        """Pick a description template avoiding recent repeats.
        
        Args:
            templates: List of candidate templates.
            event: Event dict for substitution.
            world: Optional world context (affects description text).
            max_attempts: Max attempts to find non-repeated description.
            
        Returns:
            Selected description string.
        """
        event_index = stable_sub_index(event)
        for attempt in range(max_attempts):
            template = rng_for_current_turn(
                "text:surface_description",
                event_index + attempt,
            ).choice(templates)
            candidate = self._build_description(template, event, world)
            if candidate not in self._recent_descriptions:
                return candidate
        
        # If all are repeated, just pick one (fallback)
        return self._build_description(
            rng_for_current_turn(
                "text:surface_description_fallback",
                event_index,
            ).choice(templates),
            event,
            world,
        )
    
    def _build_description(
        self,
        template: str,
        event: Dict[str, Any],
        world: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build complete description with world context.
        
        Args:
            template: Description template string.
            event: Event dict.
            world: Optional world state.
            
        Returns:
            Complete description string.
        """
        description = self._substitute(template, event)
        
        # Add world context if available
        if world:
            world_state = world.get("state", "")
            if world_state:
                description += f" The broader situation: {world_state}."
            
            # Add faction context
            factions = world.get("active_factions", [])
            if factions:
                description += " Faction dynamics may influence the outcome."
        
        return description
    
    def _emotion(self, event: Dict[str, Any]) -> str:
        """Extract emotional context for player.
        
        Translates internal emotional state into a player-facing
        emotional description that feels natural and grounded.
        
        Args:
            event: Event dict with optional emotions.
            
        Returns:
            Emotional context string.
        """
        emotions = event.get("emotions", {})
        if not emotions:
            return "The situation carries an ambiguous emotional weight."
        
        # Filter out None values from emotions
        valid_emotions = {k: v for k, v in emotions.items() if v is not None}
        if not valid_emotions:
            return "The situation carries an ambiguous emotional weight."
        
        # Find dominant emotion above threshold
        threshold = 0.3
        dominant = max(valid_emotions.items(), key=lambda x: x[1])
        emotion_name, intensity = dominant
        
        if intensity < threshold:
            return "Emotions run low beneath the surface."
        
        self._stats["emotional_contexts"] += 1
        
        # Map emotion to player-facing description
        emotion_descriptions = {
            "anger": f"Anger simmers at {intensity:.0%} intensity.",
            "fear": "Undercurrents of fear are palpable.",
            "trust": "A sense of trust permeates the situation.",
            "sadness": "Melancholy hangs in the air.",
            "joy": "Joy radiates from the participants.",
            "grief": "Grief weighs heavily on those involved.",
            "guilt": "Guilt lingers in the background.",
            "pride": "Pride colors perceptions.",
            "betrayal": "The sting of treachery is fresh.",
            "relief": "A wave of relief washes over the participants.",
        }
        
        return emotion_descriptions.get(emotion_name, "Emotions play their part.")
    
    def _memory(
        self,
        event: Dict[str, Any],
        player: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generate memory echo callback.
        
        Connects current event to past events, creating the feeling
        of a living, continuous narrative world.
        
        Args:
            event: Current event dict.
            player: Optional player profile.
            
        Returns:
            Memory echo string, or None if no relevant memory found.
        """
        # Check for character-based echoes
        characters = event.get("characters", [])
        if characters and self._memory_system:
            # Look up if we have past events with these characters
            past = self._memory_system.get_relevant_events(characters)
            if past:
                self._stats["memory_echoes"] += 1
                past_event = past[0]
                return f"This echoes {past_event.get('description', 'events of the past')}."
        
        # Check for theme-based echoes from event history
        event_type = event.get("type", "")
        event_history = event.get("history", [])
        # Fix 4: Add similarity threshold to prevent echo overfiring
        intensity_diff_threshold = 0.3
        for past in event_history[-20:]:
            if past.get("type") == event_type:
                past_intensity = float(past.get("intensity", 0.5))
                current_intensity = float(event.get("intensity", 0.5))
                if abs(past_intensity - current_intensity) < intensity_diff_threshold:
                    self._stats["memory_echoes"] += 1
                    return f"A situation reminiscent of {past.get('description', 'earlier times')}."
        
        # Check for player-relevant echoes
        if player:
            player_history = player.get("memories", [])
            for char in characters:
                for mem in player_history:
                    if char in mem.get("characters", []):
                        self._stats["memory_echoes"] += 1
                        return f"You recall a previous encounter involving {char}."
        
        return None
    
    def _determine_headline_tone(self, event: Dict[str, Any]) -> str:
        """Determine the emotional tone for headline selection.
        
        Args:
            event: Event dict.
            
        Returns:
            Tone string (tension, crisis, shock, grief, etc.).
        """
        emotions = event.get("emotions", {})
        importance = event.get("importance", 0.5)
        event_type = event.get("type", "general")
        
        # Event-type-based tones
        type_tones = {
            "faction_conflict": "tension" if importance < 0.7 else "crisis",
            "betrayal": "shock" if importance > 0.6 else "anger",
            "death": "grief",
            "quest_complete": "satisfaction" if importance > 0.5 else "neutral",
            "alliance_formed": "hope",
            "character_growth": "inspiration",
            "discovery": "wonder",
        }
        
        base_tone = type_tones.get(event_type, "neutral")
        
        # Emotion overrides can shift tone
        if emotions.get("anger", 0) and float(emotions.get("anger", 0)) > 0.6:
            return "anger"
        if emotions.get("fear", 0) and float(emotions.get("fear", 0)) > 0.6:
            return "crisis"
        if emotions.get("grief", 0) and float(emotions.get("grief", 0)) > 0.5:
            return "grief"
        
        return base_tone
    
    def _substitute(self, template: str, event: Dict[str, Any]) -> str:
        """Substitute placeholders in template with event data.
        
        Args:
            template: Template string with {placeholder} markers.
            event: Event dict with values.
            
        Returns:
            Template with placeholders substituted.
        """
        # Fix 2: Map canonical actor/target to faction_a/faction_b to improve
        #        narrative quality and close the placeholder coverage gap.
        actor_val = event.get("actor", event.get("character", "someone"))
        target_val = event.get("target", event.get("character", "someone"))
        replacements = {
            "{actor}": actor_val,
            "{target}": target_val,
            "{faction_a}": event.get("faction_a") or actor_val or "someone",
            "{faction_b}": event.get("faction_b") or target_val or "someone",
            "{character}": actor_val,
            "{betrayer}": event.get("betrayer", actor_val or "a trusted ally"),
            "{victim}": event.get("victim", target_val or "an unsuspecting party"),
            "{group}": event.get("group", "the community"),
            "{location}": event.get("location", "the distance"),
            "{issue}": event.get("issue", "matters of importance"),
            "{objective}": event.get("objective", "their goal"),
            "{discovery}": event.get("discovery", "new information"),
            "{reward}": event.get("reward", "the prize"),
            "{loss}": event.get("loss", "something precious"),
        }
        
        result = template
        for key, value in replacements.items():
            result = result.replace(key, str(value))
        
        return result
    
    def get_narrative_diversity_ratio(self) -> float:
        """Calculate headline diversity ratio (0.0-1.0).
        
        Higher ratio means more varied headlines.
        This is useful for monitoring narrative quality.
        
        Returns:
            Ratio of unique headlines to total headlines.
        """
        if not self._headline_variety:
            return 1.0
        unique = len(set(self._headline_variety))
        total = len(self._headline_variety)
        return unique / total
    
    def get_stats(self) -> Dict[str, Any]:
        """Get surface engine statistics.
        
        Returns:
            Stats dict.
        """
        return {
            **self._stats,
            "narrative_diversity": self.get_narrative_diversity_ratio(),
            "headline_cache_size": len(self._headline_variety),
        }
    
    def reset(self) -> None:
        """Reset statistics and variety tracking."""
        self._events_processed = 0
        self._headline_variety = []
        self._recent_headlines.clear()
        self._recent_descriptions.clear()
        self._stats = {
            "events_surfaced": 0,
            "headlines_generated": 0,
            "memory_echoes": 0,
            "emotional_contexts": 0,
        }
    
