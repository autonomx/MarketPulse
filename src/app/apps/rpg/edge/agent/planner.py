"""Planner — Multi-step action planning for autonomous NPCs.

This module implements the Planner from TIER 10 of the RPG design spec.

Purpose:
    Convert high-level intentions into concrete sequences of actions
    that NPCs can execute. The Planner bridges the gap between the
    Agent Brain's abstract decisions and the Action Executor's
    concrete world-mutating operations.

The Problem:
    - Intentions are abstract ("expand influence")
    - World requires concrete actions ("increase_power", "negotiate")
    - Complex goals require multiple steps across ticks
    - Plans need to persist and resume across simulation ticks

The Solution:
    Planner creates Plan objects containing ordered sequences of
    action dicts. Plans track their current step and can be resumed
    across multiple ticks until completion.

Usage:
    planner = Planner()
    intention = {"type": "expand_influence", "target": "mages_guild"}
    plan = planner.create_plan(intention)
    
    # Each tick:
    step = plan.next()
    if step:
        executor.execute(char, step, world)

Architecture:
    Intention (abstract goal)
         ↓
    Plan Template Selection
         ↓
    Action Sequence Generation
         ↓
    Plan Object (persistent state)
         ↓
    Step-by-step Execution (via next())

Design Rules:
    - Plans persist across ticks
    - Each step is a complete action dict
    - Plans terminate when exhausted
    - Intention types map to predefined templates
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class Plan:
    """A multi-step plan for an NPC to execute.
    
    Plans represent a sequence of actions that an NPC will perform
    over multiple simulation ticks. They maintain internal state
    tracking progress through the steps.
    
    Usage:
        plan = Plan([
            {"action": "gather_forces"},
            {"action": "attack", "target": "enemy"},
        ])
        
        # Each tick:
        step = plan.next()
        if step is None:
            # Plan complete
    """
    
    def __init__(self, steps: List[Dict[str, Any]]):
        """Initialize a Plan.
        
        Args:
            steps: List of action dicts to execute in order.
        """
        self.steps = steps
        self.current_step = 0
        self.completed = False
        self.failed = False
        
    def next(self) -> Optional[Dict[str, Any]]:
        """Get the next action in the plan.
        
        Returns:
            Action dict for the next step, or None if plan is complete.
        """
        if self.current_step >= len(self.steps):
            self.completed = True
            return None
        
        step = self.steps[self.current_step]
        self.current_step += 1
        return step
    
    def peek(self) -> Optional[Dict[str, Any]]:
        """Look at the next action without advancing.
        
        Returns:
            Next action dict, or None if plan is complete.
        """
        if self.current_step >= len(self.steps):
            return None
        return self.steps[self.current_step]
    
    def reset(self) -> None:
        """Reset plan to beginning."""
        self.current_step = 0
        self.completed = False
        self.failed = False
    
    def mark_failed(self) -> None:
        """Mark the plan as failed and stop execution."""
        self.failed = True
    
    @property
    def is_complete(self) -> bool:
        """Check if plan is complete.
        
        Returns:
            True if all steps have been executed.
        """
        return self.current_step >= len(self.steps)
    
    @property
    def progress(self) -> float:
        """Get plan progress as a percentage.
        
        Returns:
            Progress from 0.0 to 1.0.
        """
        if not self.steps:
            return 1.0
        return min(1.0, self.current_step / len(self.steps))
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize plan to dict.
        
        Returns:
            Plan data as dictionary.
        """
        return {
            "steps": self.steps,
            "current_step": self.current_step,
            "completed": self.completed,
            "failed": self.failed,
            "progress": self.progress,
        }


class Planner:
    """Creates multi-step plans from intentions.
    
    The Planner maps abstract intentions into concrete sequences of
    actions. Each intention type has a predefined plan template
    that the Planner instantiates with parameters.
    
    Plan Template Mapping:
    - expand_influence → increase_power → negotiate
    - attack_target → gather_forces → attack
    - deliver_aid → gather_resources → travel → deliver
    - gather_resources → scout → collect
    - negotiate → prepare -> meet -> agree
    - defend → fortify → wait
    - idle → wait
    
    Usage:
        planner = Planner()
        intention = {"type": "expand_influence", "target": "mages_guild"}
        plan = planner.create_plan(intention)
    """
    
    def __init__(self):
        """Initialize the Planner."""
        self._templates: Dict[str, List[Dict[str, Any]]] = {}
        self._register_default_templates()
    
    def _register_default_templates(self) -> None:
        """Register default plan templates."""
        self._templates["expand_influence"] = [
            {"action": "increase_power"},
            {"action": "negotiate"},
        ]
        self._templates["attack_target"] = [
            {"action": "gather_forces"},
            {"action": "attack"},
        ]
        self._templates["deliver_aid"] = [
            {"action": "gather_resources"},
            {"action": "travel"},
            {"action": "deliver"},
        ]
        self._templates["gather_resources"] = [
            {"action": "scout"},
            {"action": "collect"},
        ]
        self._templates["negotiate"] = [
            {"action": "prepare"},
            {"action": "meet"},
            {"action": "agree"},
        ]
        self._templates["defend"] = [
            {"action": "fortify"},
            {"action": "wait"},
        ]
        self._templates["idle"] = [
            {"action": "wait"},
        ]
    
    def create_plan(
        self,
        intention: Dict[str, Any],
    ) -> Plan:
        """Create a plan from an intention.
        
        Args:
            intention: Intention dict from AgentBrain with keys:
                - type: Intention type string
                - target: Optional target entity
                - priority: Intention priority
                
        Returns:
            Plan object ready for execution.
        """
        intention_type = intention.get("type", "idle")
        target = intention.get("target")
        
        # Get template (fallback to idle)
        template = self._templates.get(intention_type, self._templates["idle"])
        
        # Deep copy template steps and customize with target
        steps = []
        for step_template in template:
            step = dict(step_template)  # Shallow copy
            
            # Add target to each step if specified
            if target:
                step["target"] = target
            
            # Add intention metadata
            step["intention_type"] = intention_type
            step["priority"] = intention.get("priority", 5.0)
            step["reasoning"] = intention.get("reasoning", "")
            
            steps.append(step)
        
        return Plan(steps)
    
    def register_template(
        self,
        intention_type: str,
        steps: List[Dict[str, Any]],
    ) -> None:
        """Register a custom plan template.
        
        Args:
            intention_type: Intention type this template handles.
            steps: List of action dicts for the template.
        """
        self._templates[intention_type] = steps
    
