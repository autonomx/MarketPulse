"""Coordinator for the Tier 14 player experience systems."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.apps.rpg.rules.player.player_experience import (
    AttentionDirector,
    EmotionalFeedbackLoop,
    MemoryEchoSystem,
    NarrativeSurfacer,
    PlayerProfile,
    SurfacedEvent,
)


class PlayerExperienceEngine:
    """Master engine for Tier 14 Player Experience & Perception Layer.

    Coordinates all sub-systems to transform simulation depth into
    compelling player experience.

    Usage:
        engine = PlayerExperienceEngine()
        surfaced = engine.surface_event(event, context)
        engine.record_player_action(player_id, action_type)
    """

    def __init__(
        self,
        max_events_per_tick: int = 3,
        max_memories: int = 50,
    ):
        """Initialize the PlayerExperienceEngine.

        Args:
            max_events_per_tick: Max events to surface per tick.
            max_memories: Max memories to retain for echoes.
        """
        self.surfacer = NarrativeSurfacer()
        self.attention = AttentionDirector(max_events_per_tick=max_events_per_tick)
        self.feedback = EmotionalFeedbackLoop()
        self.memory_echo = MemoryEchoSystem(max_memories=max_memories)
        self.player_profiles: Dict[str, PlayerProfile] = {}

        self._stats = {
            "events_surfaced": 0,
            "events_filtered": 0,
            "echoes_recorded": 0,
            "feedback_generated": 0,
        }

    def get_or_create_profile(self, player_id: str) -> PlayerProfile:
        """Get or create a player profile."""
        if player_id not in self.player_profiles:
            self.player_profiles[player_id] = PlayerProfile()
        return self.player_profiles[player_id]

    def surface_event(
        self,
        event: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        player_id: Optional[str] = None,
    ) -> Optional[SurfacedEvent]:
        """Surface an event for player presentation.

        Args:
            event: Raw event data.
            context: Optional context (relationships, memory, etc.).
            player_id: Optional player ID for profile-aware surfacing.

        Returns:
            SurfacedEvent if event is worth surfacing, else None.
        """
        if context is None:
            context = {}

        # Record in memory echo system
        significance = event.get("importance", 0.5)
        self.memory_echo.record_event(event, significance)
        self._stats["echoes_recorded"] += 1

        # Surface the event
        surfaced = self.surfacer.surface(event, context)

        # Check for memory echo
        echo_context = {
            "characters": set(event.get("characters", [])),
            "locations": set(event.get("locations", [])),
            "themes": set(event.get("themes", [])),
            "emotions": event.get("emotions", {}),
            "tick": event.get("tick", 0),
        }
        memory_echo = self.memory_echo.find_echo(echo_context)
        if memory_echo:
            surfaced.memory_echo = memory_echo.format()

        # Get player profile
        player_profile = None
        if player_id:
            player_profile = self.get_or_create_profile(player_id)
            if player_profile.matches_value(event.get("type", "")):
                surfaced.player_relevance *= 1.3

        self._stats["events_surfaced"] += 1

        return surfaced

    def filter_events(
        self,
        events: List[Dict[str, Any]],
        current_tick: int = 0,
        player_id: Optional[str] = None,
    ) -> List[SurfacedEvent]:
        """Filter and surface multiple events.

        Args:
            events: List of raw events.
            current_tick: Current game tick.
            player_id: Optional player ID.

        Returns:
            List of surfaced events worth presenting.
        """
        player_profile = None
        if player_id:
            player_profile = self.get_or_create_profile(player_id)

        filtered = self.attention.filter_events(events, current_tick, player_profile)
        self._stats["events_filtered"] += len(filtered)

        surfaced = []
        for event in filtered:
            result = self.surface_event(event, player_id=player_id)
            if result:
                surfaced.append(result)

        return surfaced

    def record_player_action(
        self,
        player_id: str,
        action_type: str,
        value_alignment: Optional[str] = None,
        relationship: Optional[str] = None,
        relationship_quality: float = 0.0,
    ) -> PlayerProfile:
        """Record a player action to update their profile.

        Args:
            player_id: Player identifier.
            action_type: Type of action taken.
            value_alignment: Optional value the action aligns with.
            relationship: Optional character involved.
            relationship_quality: Quality of the relationship interaction.

        Returns:
            Updated player profile.
        """
        profile = self.get_or_create_profile(player_id)

        profile.update_style(action_type)

        if value_alignment:
            profile.update_value_alignment(value_alignment, 1.0)

        if relationship:
            profile.record_relationship(relationship, relationship_quality)

        return profile

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics."""
        return {
            **self._stats,
            "surfacer": self.surfacer.get_stats(),
            "memory_echo": self.memory_echo.get_stats(),
            "attention_budget": self.attention.get_attention_budget(),
            "player_profiles": {
                pid: profile.to_dict()
                for pid, profile in self.player_profiles.items()
            },
        }

    def reset(self) -> None:
        """Reset all statistics (preserves player profiles and memories)."""
        self._stats = {
            "events_surfaced": 0,
            "events_filtered": 0,
            "echoes_recorded": 0,
            "feedback_generated": 0,
        }
