"""Narrator Agent — Converts events into narrative prose.

This module implements the Narrator Agent from the design spec's Multi-Agent Split.
The Narrator is separate from the Director: the Director decides what happens,
the Narrator tells the story of what happened.

Purpose:
    Convert mechanical events into compelling narrative prose.
    
Architecture:
    Events → Narrator → Narrative Text
    
Usage:
    narrator = NarratorAgent(llm)
    narrative = narrator.generate(events)
    
Design Compliance:
    - Narrator does NOT decide — only narrates
    - Narrator does NOT modify world state
    - Narrator consumes events, produces text
    - Supports both LLM and template-based narration
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, cast
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.narration_narrator.prompt', "1", "Narrate this as a story:\n\n{v0}\n\n{v1}\n\nRules:\n- Do not add events that didn't happen\n- Do not contradict the facts\n- Keep it concise (under 200 words)\n- Use present tense\n- Second person if player is involved\n\nNarrative:")


class NarratorAgent:
    """Converts events into narrative prose.
    
    The Narrator Agent is a pure function: events in, narrative text out.
    It does not modify world state or make decisions.
    
    Two modes:
    - LLM mode: Uses an LLM to generate rich narrative
    - Template mode: Uses event templates for fast narration
    
    Attributes:
        llm: Optional LLM callable for narrative generation.
        style: Narration style tag ("dramatic", "neutral", "epic").
    """
    
    def __init__(
        self,
        llm: Optional[Callable] = None,
        style: str = "dramatic",
    ):
        """Initialize the NarratorAgent.
        
        Args:
            llm: Optional LLM callable. Signature: llm(prompt: str) -> str.
            style: Narration style ("dramatic", "neutral", "epic", "minimal").
        """
        self.llm = llm
        self.style = style
        
    def generate(self, events: List[Dict[str, Any]]) -> str:
        """Generate narrative text from a list of events.
        
        This is the main entry point. It takes mechanical events
        and produces narrative prose.
        
        Args:
            events: List of event dicts to narrate.
            
        Returns:
            Narrative text string.
        """
        if not events:
            return ""
            
        # Filter to narratable events
        narratable = self._filter_narratable(events)
        if not narratable:
            return ""
            
        if self.llm and self.style != "minimal":
            return self._generate_with_llm(narratable)
        else:
            return self._generate_template(narratable)
        
    def _filter_narratable(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter events to those worth narrating.
        
        Not all events need narration. Internal state updates,
        for example, are not interesting to the player.
        
        Args:
            events: All events to filter.
            
        Returns:
            Filtered list of narratable events.
        """
        narratable_types = {
            "damage", "death", "heal", "speak", "move",
            "betrayal", "alliance_formed", "story_event",
            "critical_hit", "flee", "spawn",
        }
        
        result = []
        for event in events:
            if event.get("type") in narratable_types:
                result.append(event)
                
        return result
    
    def _generate_with_llm(self, events: List[Dict[str, Any]]) -> str:
        """Generate narrative using LLM.
        
        Args:
            events: Narratable events.
            
        Returns:
            LLM-generated narrative text.
        """
        # Build event descriptions
        event_texts = []
        for event in events:
            event_texts.append(self._event_to_text(event))
            
        combined = "\n".join(event_texts)
        
        style_prompt = {
            "dramatic": "Write in a dramatic, immersive style. Make the reader feel the tension.",
            "neutral": "Write in a clear, factual style. Just report what happened.",
            "epic": "Write in an epic, mythic style. Every action has weight and meaning.",
        }
        
        style_instruction = style_prompt.get(self.style, style_prompt["dramatic"])
        
        prompt = _PROMPT_1.format(v0=(combined), v1=(style_instruction))

        try:
            result = cast(Any, self.llm)(prompt)
            return result.strip()
        except Exception:
            return self._generate_template(events)
    
    def _generate_template(self, events: List[Dict[str, Any]]) -> str:
        """Generate narrative using event templates.
        
        Args:
            events: Narratable events.
            
        Returns:
            Template-generated narrative text.
        """
        if self.style == "minimal":
            return self._minimal_narrative(events)
            
        sentences = []
        for event in events:
            sentence = self._template_event(event)
            if sentence:
                sentences.append(sentence)
                
        if not sentences:
            return "Nothing of note happens."
            
        # Join with appropriate transitions
        if len(sentences) == 1:
            return sentences[0]
            
        result = sentences[0]
        for index, sentence in enumerate(sentences[1:]):
            result += f" {self._transition(index)}{sentence[0].lower()}{sentence[1:]}"
            
        return result
    
    def _minimal_narrative(self, events: List[Dict[str, Any]]) -> str:
        """Generate minimal factual narration.
        
        Args:
            events: Events to narrate.
            
        Returns:
            Minimal factual text.
        """
        sentences = []
        for event in events:
            sentence = self._simple_event_text(event)
            if sentence:
                sentences.append(sentence)
        return " | ".join(sentences) if sentences else ""
    
    def _event_to_text(self, event: Dict[str, Any]) -> str:
        """Convert an event dict to narrative-friendly text.
        
        Args:
            event: Event dict.
            
        Returns:
            Human-readable event description.
        """
        etype = event.get("type", "event")
        
        if etype == "damage":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            amount = event.get("amount", "")
            return f"{source} attacks {target}{' for ' + str(amount) + ' damage' if amount else ''}"
        elif etype == "death":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            return f"{target} is killed by {source}"
        elif etype == "critical_hit":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            return f"{source} lands a devastating blow on {target}"
        elif etype == "heal":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            amount = event.get("amount", "")
            return f"{source} heals {target}{' for ' + str(amount) + ' HP' if amount else ''}"
        elif etype == "speak":
            speaker = event.get("speaker", "Someone")
            target = event.get("target", "")
            message = event.get("message", "")
            to_target = f" to {target}" if target else ""
            return f'{speaker} says{to_target}: "{message}"'
        elif etype == "move":
            entity = event.get("entity", "Someone")
            to_pos = event.get("to", "somewhere")
            return f"{entity} moves to {to_pos}"
        elif etype == "story_event":
            return event.get("summary", "Something happens")
        elif etype == "spawn":
            entity = event.get("entity", event.get("entity_id", "Something"))
            return f"{entity} appears"
        elif etype == "flee":
            entity = event.get("entity", "Someone")
            from_entity = event.get("from", "")
            from_text = f" from {from_entity}" if from_entity else ""
            return f"{entity} flees{from_text}"
        else:
            return event.get("summary", str(event))
    
    def _template_event(self, event: Dict[str, Any]) -> str:
        """Generate a template narration for a single event.
        
        More descriptive than _event_to_text for dramatic style.
        
        Args:
            event: Event dict.
            
        Returns:
            Narrated sentence.
        """
        etype = event.get("type", "event")
        
        if etype == "damage":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            amount = event.get("amount", 0)
            if amount > 15:
                return f"{source} strikes {target} with a heavy blow!"
            elif amount > 5:
                return f"{source} strikes {target}."
            else:
                return f"{source} grazes {target}."
        elif etype == "death":
            target = event.get("target", "Unknown")
            return f"{target} has fallen."
        elif etype == "critical_hit":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            return f"{source} lands a devastating blow on {target}!"
        elif etype == "heal":
            source = event.get("source", "Unknown")
            target = event.get("target", "Unknown")
            return f"Light envelops {target} as {source} heals their wounds."
        elif etype == "speak":
            speaker = event.get("speaker", "Someone")
            message = event.get("message", "")
            return f'{speaker} speaks: "{message}"'
        elif etype == "move":
            entity = event.get("entity", "Someone")
            return f"{entity} shifts position."
        elif etype == "story_event":
            return event.get("summary", "Something significant happens.")
        elif etype == "flee":
            entity = event.get("entity", "Someone")
            return f"{entity} turns to flee in terror."
        else:
            return ""
    
    def _simple_event_text(self, event: Dict[str, Any]) -> str:
        """Generate simple factual text for a single event.
        
        Args:
            event: Event dict.
            
        Returns:
            Simple factual text.
        """
        etype = event.get("type", "event")
        
        if etype == "damage":
            return f"{event.get('source', '?')} damages {event.get('target', '?')}"
        elif etype == "death":
            return f"{event.get('target', '?')} dies"
        elif etype == "heal":
            return f"{event.get('source', '?')} heals {event.get('target', '?')}"
        elif etype == "speak":
            return f'{event.get("speaker", "?")}: "{event.get("message", "")}"'
        elif etype == "story_event":
            return event.get("summary", "")
        else:
            return str(event)
    
    def _transition(self, sub_index: int = 0) -> str:
        """Get a narrative transition word.
        
        Returns:
            Transition string for joining sentences.
        """
        from app.apps.rpg.foundation.core.determinism import rng_for_current_turn

        transitions = ["Then, ", "Meanwhile, ", "Suddenly, ", "Moments later, ", ""]
        return rng_for_current_turn("text:narrator_transition", sub_index).choice(transitions)
    
    def reset(self) -> None:
        """Reset narrator state."""
        pass  # Stateless, nothing to reset
