"""Narrative Generator — STEP 4 of RPG Design Implementation.

This module implements the NarrativeGenerator, which uses an LLM (or
templates) to convert structured narrative events into immersive prose.

Purpose:
    Generate vivid, emotionally appropriate narrative text from
    structured events and scene context.

Architecture:
    NarrativeEvents + SceneContext → LLM/Template → Narrative Text

Usage:
    generator = NarrativeGenerator(llm=my_llm)
    narrative = generator.generate(narrative_events, scene_context)

Design Compliance:
    - STEP 4: Narrative Generator from rpg-design.txt
    - Wraps any LLM callable with signature: llm(prompt: str) -> str
    - Falls back to template generation if no LLM is provided
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, cast

from app.apps.rpg.foundation.core.determinism import rng_for_current_turn
from app.apps.rpg.foundation.narrative_event import NarrativeEvent
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.narrative_narrative_generator.prompt', "1", "You are a cinematic RPG narrator.\n\nScene:\n- Location: {v0}\n- Participants: {v1}\n- Mood: {v2}\n\nEvents that occur:\n{v3}\n\nDialogue:\n{v4}\n\n{v5}\n\nRules:\n- Write a vivid, immersive narration of what happens\n- Include the dialogue naturally within the narrative\n- Focus on clarity, flow, and emotional impact\n- Do not contradict the events\n- Do not add events that aren't listed\n- Keep it under {v6} words\n- Use present tense\n- Second person ('you') if the player is involved\n\nNarrative:")
_PROMPT_2 = prompt_template('rpg.narrative_narrative_generator.prompt_2', "1", "You are a cinematic RPG narrator.\n\nScene:\n- Location: {v0}\n- Participants: {v1}\n- Mood: {v2}\n\nEvents that occur:\n{v3}\n\n{v4}\n\nRules:\n- Write a vivid, immersive narration of what happens\n- Focus on clarity, flow, and emotional impact\n- Do not contradict the events\n- Do not add events that aren't listed\n- Keep it under {v5} words\n- Use present tense\n- Second person ('you') if the player is involved\n\nNarrative:")

# Style prompts for different narrative moods
STYLE_PROMPTS: Dict[str, str] = {
    "cinematic": (
        "Write in a cinematic, immersive style like a movie scene. "
        "Use vivid sensory details, dynamic action, and emotional beats. "
        "Keep sentences varied between short punchy and flowing complex."
    ),
    "dramatic": (
        "Write in a dramatic style. Every action has weight. "
        "Focus on the emotional significance of what happens. "
        "Use heightened language and powerful imagery."
    ),
    "literary": (
        "Write in a literary style with elegant prose. "
        "Focus on character interiority, symbolic imagery, and thematic resonances. "
        "Vary sentence structure and use precise vocabulary."
    ),
    "minimal": (
        "Write in a sparse, minimal style. "
        "Just state what happens clearly and directly. "
        "Few adjectives, no embellishment, maximum clarity."
    ),
    "first_person": (
        "Write in first person ('I' and 'my'). "
        "The narrator is personally present and experiencing the events. "
        "Make it feel immediate and visceral."
    ),
    "epic": (
        "Write in an epic, mythic style reminiscent of ancient epics. "
        "Every action echoes with larger meaning. "
        "Use formal language, grand imagery, and archaic sentence structures."
    ),
}


class NarrativeGenerator:
    """Converts narrative events into narrative prose text.
    
    The NarrativeGenerator bridges the data layer (NarrativeEvent objects)
    and the presentation layer (natural language narration). It can use
    either an LLM for rich generation or templates for fast deterministic output.
    
    Two modes:
    - LLM mode: Uses LLM for rich, creative narration (requires LLM callable)
    - Template mode: Uses event templates for fast, deterministic output
    
    Attributes:
        llm: Optional LLM callable for narrative generation. The expected
             signature is: llm(prompt: str) -> str
        style: Narrative style for prose generation.
        max_words: Maximum word count for generated narrative.
    """
    
    def __init__(
        self,
        llm: Optional[Callable[[str], str]] = None,
        style: str = "cinematic",
        max_words: int = 200,
        dialogue_engine: Any = None,
    ):
        """Initialize the NarrativeGenerator.
        
        Args:
            llm: Optional LLM callable. Signature: llm(prompt: str) -> str.
            style: Narrative style ("cinematic", "dramatic", "literary",
                   "minimal", "first_person", "epic").
            max_words: Maximum word count for generated narrative.
            dialogue_engine: Optional DialogueEngine for belief-driven dialogue.
        """
        self.llm = llm
        self.style = style
        self.max_words = max_words
        self.dialogue_engine = dialogue_engine
        
    def generate(
        self,
        events: List[NarrativeEvent],
        scene_context: Dict[str, Any],
    ) -> str:
        """Turn structured events into narrative text.
        
        This is the main generation entry point. Combines event
        descriptions with scene context to produce immersive prose.
        
        Args:
            events: List of NarrativeEvent objects to narrate.
            scene_context: Dict with scene context (location, mood, etc).
            
        Returns:
            Generated narrative text string.
        """
        if not events:
            return ""
        
        if self.llm and self.style != "minimal":
            return self._generate_with_llm(events, scene_context)
        else:
            return self._generate_with_templates(events)
    
    def _generate_with_llm(
        self,
        events: List[NarrativeEvent],
        scene_context: Dict[str, Any],
    ) -> str:
        """Generate narrative using LLM prompt engineering.
        
        Args:
            events: Narrative events to narrate.
            scene_context: Scene context for atmosphere.
            
        Returns:
            LLM-generated narrative text.
        """
        event_descriptions = self._format_events(events)
        location = scene_context.get("location", "unknown")
        participants = ", ".join(
            str(p) for p in scene_context.get("participants", [])
        )
        mood = scene_context.get("mood", "neutral")
        
        style_instruction = STYLE_PROMPTS.get(self.style, STYLE_PROMPTS["cinematic"])
        
        prompt = _PROMPT_2.format(v0=(location), v1=(participants), v2=(mood), v3=(event_descriptions), v4=(style_instruction), v5=(self.max_words))
        
        try:
            result = cast(Any, self.llm)(prompt)
            return self._trim_to_max(result.strip())
        except Exception:
            return self._generate_with_templates(events)
    
    def _generate_with_templates(
        self,
        events: List[NarrativeEvent],
        dialogue_lines: Optional[List[str]] = None,
    ) -> str:
        """Generate narrative using event templates.
        
        Fast, deterministic fallback narration.
        
        Args:
            events: Narrative events to narrate.
            dialogue_lines: Optional pre-generated dialogue lines.
        
        Returns:
            Template-generated narrative text.
        """
        if not events:
            return "Nothing of note happens."
        
        sentences = []
        for event in events:
            sentence = self._template_event_narrative(event)
            if sentence:
                sentences.append(sentence)
        
        # Add dialogue lines
        if dialogue_lines:
            sentences.extend(dialogue_lines)
        
        if not sentences:
            return "Time passes uneventfully."
        
        result = self._join_sentences(sentences)
        return self._trim_to_max(result)
    
    @staticmethod
    def _format_events(events: List[NarrativeEvent]) -> str:
        """Format events into a list of event description strings.
        
        Args:
            events: Narrative events to format.
            
        Returns:
            Formatted event descriptions joined by newlines.
        """
        descriptions = []
        for i, event in enumerate(events, 1):
            desc = f"{i}. {event.description}"
            if event.actors:
                actors_str = "; ".join(str(a) for a in event.actors)
                desc += f" (involving: {actors_str})"
            if event.location:
                desc += f" at {event.location}"
            descriptions.append(desc)
        return "\n".join(descriptions)
    
    @staticmethod
    def _template_event_narrative(event: NarrativeEvent) -> str:
        """Generate a single template sentence for an event.
        
        Args:
            event: Narrative event to narrate.
            
        Returns:
            Generated sentence string, or empty if unknown type.
        """
        etype = event.type
        actors = event.actors
        actor_str = "; ".join(str(a) for a in actors) if actors else "Someone"
        
        if etype == "damage":
            amount = event.raw_event.get("amount", "")
            if amount:
                return f"{actor_str} takes {amount} damage in the clash."
            return f"{actor_str} strikes a blow."
        elif etype == "death":
            return f"{actor_str} falls, lifeless. Death claims another."
        elif etype == "combat":
            return f"The sound of battle echoes as {actor_str} fights."
        elif etype == "critical_hit":
            return f"{actor_str} lands a devastating, crushing blow!"
        elif etype == "heal":
            amount = event.raw_event.get("amount", "")
            if amount:
                return f"Wounds mend as {actor_str} recovers {amount} health."
            return f"{actor_str} is restored."
        elif etype == "speak":
            msg = event.raw_event.get("message", event.description)
            return f"{actor_str} says: \"{msg}\""
        elif etype == "move":
            loc = event.location or "somewhere"
            return f"{actor_str} moves toward {loc}."
        elif etype == "story_event":
            return event.description or "Something significant transpires."
        elif etype == "flee":
            return f"{actor_str} turns and runs in terror."
        else:
            return event.description or ""
    
    @staticmethod
    def _join_sentences(sentences: List[str]) -> str:
        """Join multiple sentences into a narrative paragraph.
        
        Args:
            sentences: List of sentence strings.
            
        Returns:
            Joined paragraph with transitions.
        """
        transitions = [" Then,", " Meanwhile,", " Suddenly,", " And yet,", ""]
        
        if len(sentences) <= 1:
            return " ".join(sentences)
        
        result = sentences[0]
        for index, sentence in enumerate(sentences[1:]):
            transition = rng_for_current_turn(
                "text:narrative_sentence_transition",
                index,
            ).choice(transitions)
            first_lower = sentence[0].lower() + sentence[1:]
            result += f"{transition}{first_lower}"
        
        return result
    
    def _trim_to_max(self, text: str) -> str:
        """Trim narrative to maximum word count.
        
        Args:
            text: Full narrative text.
            
        Returns:
            Trimmed text not exceeding max_words.
        """
        words = text.split()
        if len(words) <= self.max_words:
            return text
        return " ".join(words[:self.max_words]) + "..."
