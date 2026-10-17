"""Dialogue Engine — Goal-Driven Dialogue System.

TIER 5: Experience Orchestration — System 2 of 3

Purpose:
    Generates NPC dialogue based on beliefs, relationships, emotional state,
    and dialogue goals. NPCs pursue outcomes through dialogue rather than
    merely reacting — they intimidate, persuade, deceive, threaten, or reveal
    information based on their current goals.

Architecture:
    MemoryManager (beliefs/goals) + Speaker + Target → Intent → Goal → Tactic → Line

The DialogueEngine first determines the speaker's goal, selects a
rhetorical tactic based on that goal, and generates dialogue that
matches both the tactic and emotional tone.

Usage:
    engine = DialogueEngine(memory_manager)
    line = engine.generate_dialogue("guard", "player")
    # "The guard watches the player suspiciously."

Design Compliance:
    - TIER 5 from rpg-design.txt: Belief-Driven Dialogue System
    - Integrates with NarrativeGenerator for dialogue integration
    - Uses BeliefSystem for relationship-aware dialogue
    - NPCs act with intention, not just react
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Goal-based dialogue templates — NPCs pursue outcomes through dialogue
GOAL_DIALOGUE_TEMPLATES: Dict[str, List[str]] = {
    "intimidate": [
        "{speaker} leans in. \"You should leave. Now.\"",
        "{speaker} looms over {target}. \"Cross me and you'll regret it.\"",
        "{speaker} narrows their eyes at {target}. \"Know your place.\"",
        "{speaker} cracks their knuckles. \"I won't ask again.\"",
        "{speaker} glares at {target}. \"Try me. See what happens.\"",
    ],
    "persuade": [
        "{speaker} says softly, \"You can trust me.\"",
        "{speaker} leans forward earnestly. \"Listen — I know what's best.\"",
        "{speaker} gestures to {target}. \"Think about it. We both win.\"",
        "{speaker} pleads with {target}. \"Just this once, hear me out.\"",
        "{speaker} smiles at {target}. \"Together, we could do great things.\"",
    ],
    "deceive": [
        "{speaker} smiles. \"Everything is under control.\"",
        "{speaker} shrugs casually. \"Nothing to worry about here.\"",
        "{speaker} nods confidently at {target}. \"You have my word.\"",
        "{speaker} averts their gaze. \"I wouldn't know anything about that.\"",
        "{speaker} laughs nervously. \"That's... a misunderstanding.\"",
    ],
    "threaten": [
        "{speaker} grips their weapon. \"One more step. I dare you.\"",
        "{speaker} points at the door. \"Out. Before I change my mind.\"",
        "{speaker} speaks coldly to {target}. \"You don't want to test me.\"",
        "{speaker} blocks {target}'s path. \"Not so fast.\"",
        "{speaker} whispers to {target}. \"People who ask questions disappear.\"",
    ],
    "reveal": [
        "{speaker} lowers their voice. \"I need to tell you something.\"",
        "{speaker} glances around, then speaks to {target}. \"There's something you should know.\"",
        "{speaker} sighs. \"The truth is, I've been keeping a secret.\"",
        "{speaker} pulls {target} aside. \"Between us? I've seen it myself.\"",
        "{speaker} hesitates, then tells {target}. \"You deserve to know.\"",
    ],
    "inquire": [
        "{speaker} eyes {target} curiously. \"What brings you here?\"",
        "{speaker} asks {target} cautiously. \"What do you know?\"",
        "{speaker} leans in. \"I have a question for you.\"",
        "{speaker} studies {target}. \"Tell me — what's your story?\"",
        "{speaker} speaks to {target} gently. \"Can I ask you something?\"",
    ],
    "comfort": [
        "{speaker} places a hand on {target}'s shoulder. \"It'll be alright.\"",
        "{speaker} speaks softly to {target}. \"You're not alone in this.\"",
        "{speaker} offers {target} a reassuring smile. \"We'll get through this.\"",
        "{speaker} sits beside {target} quietly. \"I'm here if you need me.\"",
        "{speaker} speaks gently. \"You've done enough. Rest now.\"",
    ],
    "evade": [
        "{speaker} changes the subject smoothly. \"Beautiful weather today, isn't it?\"",
        "{speaker} laughs. \"That's not really my area of expertise.\"",
        "{speaker} shrugs. \"Could be anything, really.\"",
        "{speaker} looks away from {target}. \"I'd rather not discuss that.\"",
        "{speaker} deflects. \"I think you should ask someone else.\"",
    ],
}

# Fallback when no goals exist — still reactive, but useful
NO_GOAL_DIALOGUE: List[str] = [
    "{speaker} looks at {target}. \"Hmm, I don't know you.\"",
    "{speaker} regards {target} curiously. \"You're a stranger here.\"",
    "{speaker} eyes {target} with curiosity. \"Who are you?\"",
    "{speaker} speaks to {target} neutrally. \"I haven't seen you before.\"",
]

# Self-directed dialogue (no target)
SELF_DIALOGUE_TEMPLATES: Dict[str, List[str]] = {
    "hostile": [
        "{speaker} mutters angrily. \"They'll pay for what they did.\"",
        "{speaker} clenches their fists. \"I won't forgive this.\"",
    ],
    "friendly": [
        "{speaker} smiles to themselves. \"Good people still exist.\"",
        "{speaker} hums happily. \"Life is good.\"",
    ],
    "cautious": [
        "{speaker} scans the area nervously. \"Something feels off.\"",
        "{speaker} keeps to the shadows. \"Best to stay out of sight.\"",
    ],
    "neutral": [
        "{speaker} looks thoughtful. \"Just another day.\"",
        "{speaker} sighs quietly. \"Time passes.\"",
    ],
    "fearful": [
        "{speaker} trembles slightly. \"I hope nothing bad happens.\"",
        "{speaker} glances around nervously. \"It's not safe here.\"",
    ],
    "respectful": [
        "{speaker} reflects quietly. \"I must do my duty.\"",
        "{speaker} meditates. \"I must stay focused on my purpose.\"",
    ],
}

# Fallback dialogue when no beliefs exist
NO_BELIEF_DIALOGUE: List[str] = [
    "{speaker} looks at {target}. \"Hmm, I don't know you.\"",
    "{speaker} regards {target} curiously. \"You're a stranger here.\"",
    "{speaker} eyes {target} with curiosity. \"Who are you?\"",
    "{speaker} speaks to {target} neutrally. \"I haven't seen you before.\"",
]

# Self-directed dialogue (no target)
SELF_DIALOGUE_TEMPLATES = {  # noqa: F811
    "hostile": [
        "{speaker} mutters angrily. \"They'll pay for what they did.\"",
        "{speaker} clenches their fists. \"I won't forgive this.\"",
    ],
    "friendly": [
        "{speaker} smiles to themselves. \"Good people still exist.\"",
        "{speaker} hums happily. \"Life is good.\"",
    ],
    "cautious": [
        "{speaker} scans the area nervously. \"Something feels off.\"",
        "{speaker} keeps to the shadows. \"Best to stay out of sight.\"",
    ],
    "neutral": [
        "{speaker} looks thoughtful. \"Just another day.\"",
        "{speaker} sighs quietly. \"Time passes.\"",
    ],
    "fearful": [
        "{speaker} trembles slightly. \"I hope nothing bad happens.\"",
        "{speaker} glances around nervously. \"It's not safe here.\"",
    ],
    "respectful": [
        "{speaker} reflects quietly. \"I must do my duty.\"",
        "{speaker} meditates. \"I must stay focused on my purpose.\"",
    ],
}


class DialogueEngine:
    """Generates dialogue based on NPC beliefs, relationships, and goals.
    
    The DialogueEngine connects to the memory manager to retrieve
    beliefs and current goals about entities, then generates
    dialogue lines that reflect both emotional state and intended outcome.
    
    Dialogue generation pipeline:
    1. Determine speaker's goal (intimidate, persuade, deceive, etc.)
    2. Select rhetorical tactic based on goal
    3. Generate dialogue line that pursues the goal
    
    This makes NPCs active participants in the scene with their own
    agendas, not reactive mouthpieces.
    
    Supported goals:
    - intimidate: Pursue compliance through fear
    - persuade: Pursue agreement through reasoning/appeal
    - deceive: Pursue outcome through misinformation
    - threaten: Pursue compliance through explicit threats
    - reveal: Share information/confide in target
    - inquire: Gather information from target
    - comfort: Provide emotional support
    - evade: Avoid answering or engaging with topic
    
    Attributes:
        memory: MemoryManager for belief and goal retrieval.
    """
    
    def __init__(self, memory: Any = None):
        """Initialize the DialogueEngine.
        
        Args:
            memory: MemoryManager or BeliefSystem for retrieving beliefs.
                    Can be None for standalone operation with manual injection.
        """
        self.memory = memory
        self._injected_beliefs: Dict[str, Any] = {}
        
    def generate_dialogue(
        self,
        speaker: str,
        target: Optional[str] = None,
        force_goal: Optional[str] = None,
    ) -> str:
        """Generate a line of dialogue reflecting beliefs and goals.
        
        NPCs now speak with intention — they pursue goals through
        dialogue rather than merely expressing emotions.
        
        Args:
            speaker: The entity speaking.
            target: The entity being spoken to (optional for self-dialogue).
            force_goal: Override the auto-detected goal (for story-driven
                        moments where specific dialogue is needed).
            
        Returns:
            A dialogue line reflecting the speaker's goals and beliefs.
        """
        if target is None:
            return self._generate_self_dialogue(speaker)
        
        # Determine goal first, then tone
        goal = force_goal or self._get_speaker_goal(speaker, target)
        tone = self._infer_tone(speaker, target)
        
        return self._generate_with_goal(speaker, target, goal, tone)
    
    def _get_speaker_goal(self, speaker: str, target: str) -> str:
        """Determine the speaker's current dialogue goal.
        
        Goals are derived from beliefs about the target:
        - Positive beliefs → persuade, comfort, reveal
        - Negative beliefs → intimidate, threaten
        - Mixed beliefs → inquire, evade
        - Unknown beliefs → inquire (gather more info)
        
        Args:
            speaker: The entity speaking.
            target: The entity being addressed.
            
        Returns:
            Goal string (intimidate, persuade, deceive, threaten, reveal,
                        inquire, comfort, evade).
        """
        if self.memory is None:
            return "inquire"  # Default: try to learn about target
        
        beliefs = self._get_beliefs(speaker, target)
        
        if not beliefs:
            return "inquire"  # Unknown → gather info
        
        avg_value = self._compute_belief_average(beliefs)
        
        # Check specific belief patterns
        belief_types = [b.get("type", "") for b in beliefs]
        
        # Hostile beliefs → intimidation or threats
        if avg_value < -0.5:
            return "threaten"
        elif avg_value < -0.3:
            return "intimidate"
        
        # Negative beliefs with uncertainty → evasion
        elif avg_value < -0.1:
            return "evade"
        
        # Strong positive beliefs → comfort or reveal
        elif avg_value > 0.7:
            return "comfort"
        elif avg_value > 0.5:
            if "secret" in belief_types or "confide" in belief_types:
                return "reveal"
            return "persuade"
        elif avg_value > 0.3:
            return "persuade"
        
        # Neutral → inquire
        else:
            return "inquire"
    
    def _generate_with_goal(
        self,
        speaker: str,
        target: str,
        goal: str,
        tone: str,
    ) -> str:
        """Generate dialogue that pursues a specific goal.
        
        The dialogue line combines the speaker's goal with their
        emotional tone toward the target.
        
        Args:
            speaker: The entity speaking.
            target: The entity being addressed.
            goal: The speaker's dialogue goal (intimidate, persuade, etc.).
            tone: The emotional tone (hostile, friendly, cautious, etc.).
            
        Returns:
            Dialogue line that pursues the goal in the given tone.
        """
        templates = GOAL_DIALOGUE_TEMPLATES.get(goal, NO_GOAL_DIALOGUE)
        
        # Deterministic selection based on speaker+target+goal
        idx = (hash(speaker) + hash(target) + hash(goal)) % len(templates)
        template = templates[idx]
        
        return template.format(speaker=speaker, target=target)
    
    def _generate_self_dialogue(self, speaker: str) -> str:
        """Generate self-directed dialogue based on overall belief state.
        
        Args:
            speaker: The entity speaking to themselves.
            
        Returns:
            Self-directed dialogue line.
        """
        # Get overall tone from available beliefs
        tone = self._get_overall_tone(speaker)
        
        templates = SELF_DIALOGUE_TEMPLATES.get(tone, SELF_DIALOGUE_TEMPLATES["neutral"])
        template = templates[hash(speaker) % len(templates)]
        
        return template.format(speaker=speaker)
    
    def _infer_tone(self, speaker: str, target: str) -> str:
        """Infer the emotional tone from beliefs about the target.
        
        Args:
            speaker: The entity whose beliefs are queried.
            target: The entity being evaluated.
            
        Returns:
            One of: hostile, friendly, cautious, respectful, fearful, neutral.
        """
        if self.memory is None:
            return "neutral"
        
        beliefs = self._get_beliefs(speaker, target)
        
        if not beliefs:
            return "neutral"
        
        avg_value = self._compute_belief_average(beliefs)
        
        # Check for hostile targets (direct harm)
        if avg_value < -0.5:
            return "hostile"
        elif avg_value < -0.3:
            return "hostile"
        elif avg_value > 0.5:
            return "respectful"
        elif avg_value > 0.3:
            return "friendly"
        elif avg_value < -0.1:
            return "cautious"
        elif avg_value < 0.1:
            return "neutral"
        else:
            return "cautious"
    
    def _get_beliefs(self, speaker: str, target: str) -> List[Dict[str, Any]]:
        """Retrieve beliefs about the target from memory.
        
        Args:
            speaker: The entity whose beliefs are queried.
            target: The entity being evaluated.
            
        Returns:
            List of belief dicts relevant to the speaker-target relationship.
        """
        if self.memory is None:
            return []
        
        # Try to retrieve through memory manager
        if hasattr(self.memory, "retrieve"):
            memories = self.memory.retrieve(
                query_entities=[speaker, target],
                limit=10,
            )
            return [
                m for _, m in memories
                if isinstance(m, dict) and m.get("type") == "relationship"
            ]
        
        # Try belief system
        if hasattr(self.memory, "get"):
            belief_system = self.memory
            if hasattr(belief_system, "belief_system"):
                belief_system = belief_system.belief_system
            
            beliefs = []
            hostile = belief_system.get("hostile_targets", [])
            trusted = belief_system.get("trusted_allies", [])
            
            if target in hostile:
                beliefs.append({"value": -0.5, "reason": "hostile"})
            if target in trusted:
                beliefs.append({"value": 0.5, "reason": "trusted"})
            
            return beliefs
        
        return []
    
    def _compute_belief_average(self, beliefs: List[Dict[str, Any]]) -> float:
        """Compute average belief value.
        
        Args:
            beliefs: List of belief dicts with 'value' keys.
            
        Returns:
            Average belief value, or 0.0 if no beliefs.
        """
        if not beliefs:
            return 0.0
        
        values = [b.get("value", 0.0) for b in beliefs]
        return sum(values) / len(values)
    
    def _get_overall_tone(self, speaker: str) -> str:
        """Get the speaker's overall emotional tone from all beliefs.
        
        Args:
            speaker: The entity whose overall state is queried.
            
        Returns:
            Overall tone string.
        """
        if self.memory is None:
            return "neutral"
        
        # Get all beliefs
        if hasattr(self.memory, "belief_system"):
            bs = self.memory.belief_system
            threat = bs.get("world_threat_level", "low")
            
            if threat in ("high", "very_high"):
                return "fearful"
            
            hostile_count = len(bs.get("hostile_targets", []))
            trusted_count = len(bs.get("trusted_allies", []))
            
            if hostile_count > trusted_count:
                return "cautious"
            elif trusted_count > hostile_count:
                return "friendly"
        
        return "neutral"
    
