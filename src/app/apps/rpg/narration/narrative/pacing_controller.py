"""Pacing Controller — Narrative Beat Control.

TIER 5: Experience Orchestration — System 3 of 3

Purpose:
    Controls narrative length, density, and structure based on tension level.
    Intense scenes get shorter, punchier narration (fast pace, action beats).
    Calm scenes get longer, more descriptive narration (slow pace, reflection beats).
    This creates a dynamic reading experience that matches the story's mood.

Architecture:
    Narrative Text + Tension Level + Events -> Adjusted Text with Beat Control

The PacingController takes generated narration and adjusts its structure
based on the current tension level from the AI Director:
- High tension: action beats only, remove fluff
- Mid tension: balanced mix of action and description
- Low tension: include reflective beats and atmosphere

Usage:
    pacing = PacingController()
    text = pacing.adjust(narrative_text, tension=0.8)  # Fast pace, action-focused
    text = pacing.adjust(narrative_text, tension=0.2)  # Slow pace, reflective

Design Compliance:
    - TIER 5 from rpg-design.txt: Narrative Pacing Controller
    - Integrates with PlayerLoop for final narration adjustment
    - Works with AIDirector tension for dynamic pacing
"""

from __future__ import annotations

import re
from typing import List

# Word count targets for different tension levels
FAST_PACE_MAX = 60       # High tension (>0.7): short, punchy
MEDIUM_PACE_MAX = 100    # Mid tension (0.3-0.7): moderate
SLOW_PACE_MAX = 150      # Low tension (<0.3): longer, descriptive

# Beat types
BEAT_ACTION = "action"
BEAT_DESCRIPTION = "description"
BEAT_REFLECTION = "reflection"
BEAT_DIALOGUE = "dialogue"

# Sentence patterns that indicate different beat types
ACTION_PATTERNS = [
    r"\b(attacks?|strikes?|hits?|kills?|damages?|destroys?|runs?|flees?|jumps?)\b",
    r"\b(breaks?|smashes?|crashes?|explodes?|burns?|cuts?|stabs?)\b",
    r"\b(grabs?|pulls?|pushes?|throws?|catches?|blocks?|dodges?)\b",
]

REFLECTION_PATTERNS = [
    r"\b(thinks?|wonders?|remembers?|hopes?|fears?|realizes?|believes?)\b",
    r"\b(considers?|ponders?|reflects?|contemplates?|muses?)\b",
    r"\b(feels?|senses?|notices?|observes?|perceives?)\b",
]

ATMOSPHERE_PATTERNS = [
    r"\b(the (room|hall|forest|cave|sky|sun|moon|wind|rain|darkness|light))\b",
    r"\b(smells? like|sounds? like|feels? like)\b",
    r"\b(quiet|silent|dark|bright|cold|warm)\b",
]

DIALOGUE_PATTERNS = [
    r'["].+?["]',  # Quoted speech (straight double quotes)
    r"\bs(ays?|shouts?|whispers?|murmurs?|speaks?)\b",
]

# Precompiled regex patterns
_ACTION_RE = [re.compile(p, re.IGNORECASE) for p in ACTION_PATTERNS]
_REFLECTION_RE = [re.compile(p, re.IGNORECASE) for p in REFLECTION_PATTERNS]
_ATMOSPHERE_RE = [re.compile(p, re.IGNORECASE) for p in ATMOSPHERE_PATTERNS]
_DIALOGUE_RE = [re.compile(p, re.IGNORECASE) for p in DIALOGUE_PATTERNS]


class NarrativeBeat:
    """Represents a single beat (structural unit) in a narrative.
    
    Beats are the building blocks of narrative structure. Each beat
    serves a specific purpose:
    - action: Events happening (combat, movement, physical interactions)
    - description: Setting the scene, sensory details
    - reflection: Internal thoughts, realizations, emotional states
    - dialogue: Spoken words between characters
    """
    
    def __init__(self, text: str, beat_type: str):
        self.text = text
        self.beat_type = beat_type
    
    def __repr__(self) -> str:
        return f"Beat({self.beat_type}: {self.text[:30]}...)"


class PacingController:
    """Controls narrative length, density, and beat structure based on tension.
    
    The PacingController adjusts narration to match the current
    story pacing:
    - High tension (0.7-1.0): Action beats only, remove reflection/description
    - Mid tension (0.3-0.7): Balanced mix of action and description
    - Low tension (0.0-0.3): Include reflection and atmospheric beats
    
    Pacing affects structure, not just length. High tension scenes
    strip out non-essential beats; low tension scenes expand with
    reflection and atmosphere.
    
    Attributes:
        fast_max: Maximum words for fast pace.
        medium_max: Maximum words for medium pace.
        slow_max: Maximum words for slow pace.
    """
    
    def __init__(
        self,
        fast_max: int = FAST_PACE_MAX,
        medium_max: int = MEDIUM_PACE_MAX,
        slow_max: int = SLOW_PACE_MAX,
    ):
        """Initialize the PacingController.
        
        Args:
            fast_max: Maximum words for high-tension (fast) pacing.
            medium_max: Maximum words for medium-tension pacing.
            slow_max: Maximum words for low-tension (slow) pacing.
        """
        self.fast_max = fast_max
        self.medium_max = medium_max
        self.slow_max = slow_max
        
    def adjust(self, text: str, tension: float) -> str:
        """Adjust narrative structure based on tension.
        
        High tension -> extract action sentences, short and punchy
        Low tension -> keep all text including reflection and atmosphere
        
        Args:
            text: Original narrative text.
            tension: Current tension level (0.0 to 1.0).
            
        Returns:
            Adjusted narrative text with appropriate beat structure.
        """
        if not text:
            return text
            
        if tension > 0.7:
            return self._extract_action_sentences(text)
        elif tension < 0.3:
            return self._expand_with_reflection(text)
        else:
            return self._medium_pace_text(text)
    
    def _extract_action_sentences(self, text: str) -> str:
        """Extract only action-focused sentences for high-tension pacing.
        
        Strips out reflection, atmosphere, and description to leave
        only what's happening right now.
        
        Args:
            text: Full narrative text.
            
        Returns:
            Text containing only action beats.
        """
        sentences = self._split_sentences(text)
        action_sentences = []
        
        for sentence in sentences:
            beat_type = self._classify_sentence(sentence)
            if beat_type in (BEAT_ACTION, BEAT_DIALOGUE):
                action_sentences.append(sentence)
        
        if action_sentences:
            result = " ".join(action_sentences)
            # Still enforce word limit
            words = result.split()
            if len(words) > self.fast_max:
                result = " ".join(words[:self.fast_max])
                # Try to end at sentence boundary
                result = self._trim_to_sentence(result, self.fast_max)
            return result
        
        # Fallback: if no action sentences found, return trimmed original
        return self._fast_pace_text(text)
    
    def _expand_with_reflection(self, text: str) -> str:
        """Keep full text including reflection for low-tension pacing.
        
        At low tension, the reader has time to absorb atmosphere,
        character thoughts, and descriptive details.
        
        Args:
            text: Full narrative text.
            
        Returns:
            Full or expanded text with all beat types preserved.
        """
        # Allow full text up to slow max
        words = text.split()
        if len(words) <= self.slow_max:
            return text
        return " ".join(words[:self.slow_max])
    
    def _medium_pace_text(self, text: str) -> str:
        """Balanced pacing: keep action + some description, trim reflection.
        
        Args:
            text: Full narrative text.
            
        Returns:
            Moderately paced text.
        """
        sentences = self._split_sentences(text)
        kept = []
        
        for sentence in sentences:
            beat_type = self._classify_sentence(sentence)
            # Keep action, dialogue, and some description
            if beat_type in (BEAT_ACTION, BEAT_DIALOGUE, BEAT_DESCRIPTION):
                kept.append(sentence)
            elif beat_type == BEAT_REFLECTION and len(kept) < self.medium_max // 5:
                # Allow a few reflection sentences
                kept.append(sentence)
        
        if kept:
            result = " ".join(kept)
            words = result.split()
            if len(words) > self.medium_max:
                result = self._trim_to_sentence(result, self.medium_max)
            return result
        
        return self._trim_to_sentence(text, self.medium_max)
    
    def _fast_pace_text(self, text: str) -> str:
        """Fast-paced text by simple word trimming."""
        words = text.split()
        keep_count = min(len(words), self.fast_max)
        result = " ".join(words[:keep_count])
        return self._trim_to_sentence(result, keep_count)
    
    def _classify_sentence(self, sentence: str) -> str:
        """Classify a sentence into its primary beat type.
        
        Args:
            sentence: Single sentence string.
            
        Returns:
            Beat type string.
        """
        # Score against each pattern set
        action_score = sum(1 for p in _ACTION_RE if p.search(sentence))
        reflection_score = sum(1 for p in _REFLECTION_RE if p.search(sentence))
        atmosphere_score = sum(1 for p in _ATMOSPHERE_RE if p.search(sentence))
        dialogue_score = sum(1 for p in _DIALOGUE_RE if p.search(sentence))
        
        scores = {
            BEAT_ACTION: action_score,
            BEAT_REFLECTION: reflection_score,
            BEAT_DESCRIPTION: atmosphere_score,
            BEAT_DIALOGUE: dialogue_score,
        }
        
        # Return highest scoring type
        best = max(scores, key=lambda kind: scores[kind])
        return best if scores[best] > 0 else BEAT_DESCRIPTION
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences.
        
        Simple sentence splitter that respects common punctuation.
        
        Args:
            text: Full text to split.
            
        Returns:
            List of sentence strings.
        """
        # Split on sentence-ending punctuation followed by space
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _trim_to_sentence(self, text: str, max_words: int) -> str:
        """Trim text to the nearest sentence boundary.
        
        Avoids cutting sentences mid-way when possible.
        
        Args:
            text: Text to trim.
            max_words: Maximum word count allowed.
            
        Returns:
            Text trimmed to sentence boundary, or truncated if needed.
        """
        words = text.split()
        if len(words) <= max_words:
            return text
            
        # Find last sentence-ending punctuation within limit
        truncated = " ".join(words[:max_words])
        
        for punct in (".", "!", "?"):
            last_idx = truncated.rfind(punct)
            if last_idx > len(truncated) * 0.5:  # Don't trim too much
                return truncated[:last_idx + 1]
        
        # If no good sentence boundary found, add ellipsis
        return truncated + "..."
    
