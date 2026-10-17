"""Resource System — Economy, stamina, and consequence tracking.

This module implements CRITICAL PATCH 7 from the RPG design specification:
"NO RESOURCE / ECONOMY SYSTEM"

The Problem: Everything is free, unlimited, and consequence-less.
Without resource constraints, there's no strategic depth.

The Solution: A Resource system that tracks stamina, mana, gold,
and enforces costs on actions.

Architecture:
    ResourcePool(stamina, mana, gold) →
        Check cost before action →
        Consume on execution →
        Restore over time

Usage:
    resources = ResourcePool(entity_id="player")
    resources.consume("stamina", 10)  # Attack costs 10 stamina
    if resources.can_afford("stamina", 20):
        # Can perform action
    resources.restore("stamina", 5)  # Rest over time

Key Features:
    - Multiple resource types (stamina, mana, gold, health)
    - Action cost enforcement
    - Regeneration over time
    - Minimum thresholds (can't act below threshold)
    - Debt system (negative resources allowed with consequences)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Resource types with default configurations
RESOURCE_CONFIG = {
    "stamina": {
        "max": 100,
        "regen_per_tick": 0.5,
        "min_for_action": 5,
        "exhaustion_penalty": 0.5,  # Action effectiveness when exhausted
    },
    "mana": {
        "max": 50,
        "regen_per_tick": 1.0,
        "min_for_action": 0,
        "exhaustion_penalty": 0.0,
    },
    "gold": {
        "max": 99999,
        "regen_per_tick": 0.0,
        "min_for_action": 0,
        "exhaustion_penalty": 0.0,
    },
    "health": {
        "max": 100,
        "regen_per_tick": 0.0,  # Health doesn't auto-regen
        "min_for_action": 1,
        "exhaustion_penalty": 0.3,
    },
}


class ResourcePool:
    """Manages resources for a single entity.
    
    Each entity (NPC or player) has a ResourcePool that tracks
    their stamina, mana, gold, and health.
    
    Attributes:
        entity_id: The entity this pool belongs to.
        resources: Dict of resource name → current value.
        max_values: Dict of resource name → max value.
        allow_debt: If True, resources can go negative.
    """
    
    def __init__(
        self,
        entity_id: str,
        initial_stamina: float = 100,
        initial_mana: float = 50,
        initial_gold: float = 0,
        initial_health: float = 100,
        config: Optional[Dict[str, Dict[str, Any]]] = None,
        allow_debt: bool = False,
    ):
        """Initialize ResourcePool.
        
        Args:
            entity_id: Entity identifier.
            initial_stamina: Starting stamina.
            initial_mana: Starting mana.
            initial_gold: Starting gold.
            initial_health: Starting health.
            config: Custom resource configuration.
            allow_debt: Allow negative resource values.
        """
        self.entity_id = entity_id
        self.config = config or RESOURCE_CONFIG
        self.allow_debt = allow_debt
        
        self.resources: Dict[str, float] = {
            "stamina": initial_stamina,
            "mana": initial_mana,
            "gold": initial_gold,
            "health": initial_health,
        }
        
        self.max_values: Dict[str, float] = {
            name: cfg.get("max", 100)
            for name, cfg in self.config.items()
        }
        
        # Set max for initial resources
        for name in ["stamina", "mana", "gold", "health"]:
            if name in self.max_values:
                self.resources[name] = min(
                    self.resources[name],
                    self.max_values[name],
                )
                
    def can_afford(self, resource: str, amount: float) -> bool:
        """Check if entity can afford a resource cost.
        
        Args:
            resource: Resource type (stamina, mana, gold).
            amount: Required amount.
            
        Returns:
            True if entity has enough resources.
        """
        current = self.resources.get(resource, 0)
        
        if self.allow_debt:
            return True  # Debt always "affordable"
            
        # Check minimum action threshold
        min_threshold = self.config.get(resource, {}).get("min_for_action", 0)
        if current - amount < min_threshold:
            return False
            
        return current >= amount
        
    def consume(self, resource: str, amount: float) -> bool:
        """Consume resources.
        
        Args:
            resource: Resource type.
            amount: Amount to consume.
            
        Returns:
            True if consumption succeeded.
        """
        if not self.can_afford(resource, amount):
            return False
            
        self.resources[resource] = self.resources.get(resource, 0) - amount
        return True
        
    def restore(self, resource: str, amount: float) -> None:
        """Restore resources (e.g., from resting or healing).
        
        Args:
            resource: Resource type.
            amount: Amount to restore.
        """
        current = self.resources.get(resource, 0)
        max_val = self.max_values.get(resource, 100)
        self.resources[resource] = min(current + amount, max_val)
        
    def tick(self) -> Dict[str, float]:
        """Process resource regeneration for one tick.
        
        Returns:
            Dict of resource changes (name → delta).
        """
        changes = {}
        
        for name, cfg in self.config.items():
            regen = cfg.get("regen_per_tick", 0)
            if regen > 0:
                current = self.resources.get(name, 0)
                max_val = self.max_values.get(name, 100)
                delta = min(regen, max_val - current)
                self.resources[name] = current + delta
                changes[name] = delta
                
        return changes
        
    def is_exhausted(self, resource: str = "stamina") -> bool:
        """Check if entity is exhausted in a resource.
        
        Exhausted means below minimum action threshold.
        
        Args:
            resource: Resource type to check.
            
        Returns:
            True if exhausted.
        """
        current = self.resources.get(resource, 0)
        min_threshold = self.config.get(resource, {}).get("min_for_action", 0)
        return current < min_threshold
        
    def get_status(self) -> Dict[str, Any]:
        """Get resource status summary.
        
        Returns:
            Dict with resource information.
        """
        status = {}
        for name in self.resources:
            current = self.resources[name]
            max_val = self.max_values.get(name, 100)
            pct = current / max(max_val, 1)
            status[name] = {
                "current": round(current, 1),
                "max": max_val,
                "pct": round(pct, 3),
                "exhausted": self.is_exhausted(name),
            }
        return status
        
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "entity_id": self.entity_id,
            "resources": {k: round(v, 1) for k, v in self.resources.items()},
            "max_values": self.max_values,
            "allow_debt": self.allow_debt,
        }


class ResourceManager:
    """Central manager for all entity resource pools.
    
    Tracks resources for all entities and enforces costs
    at the action execution level.
    
    Usage:
        mgr = ResourceManager()
        mgr.register("player", initial_stamina=100)
        cost = mgr.get_action_cost("attack")
        if mgr.can_afford("player", cost):
            mgr.consume("player", cost)
            # Execute action
    """
    
    def __init__(self):
        """Initialize ResourceManager."""
        self.pools: Dict[str, ResourcePool] = {}
        self.action_costs: Dict[str, Dict[str, float]] = {
            "attack": {"stamina": 10},
            "defend": {"stamina": 5},
            "move": {"stamina": 2},
            "heal": {"mana": 15, "stamina": 5},
            "speak": {},  # Free action
            "wander": {"stamina": 1},
            "observe": {"stamina": 1},
            "flee": {"stamina": 15},
        }
        
    def register(
        self,
        entity_id: str,
        initial_stamina: float = 100,
        initial_mana: float = 50,
        initial_gold: float = 0,
        initial_health: float = 100,
    ) -> ResourcePool:
        """Register an entity's resource pool.
        
        Args:
            entity_id: Entity identifier.
            initial_stamina: Starting stamina.
            initial_mana: Starting mana.
            initial_gold: Starting gold.
            initial_health: Starting health.
            
        Returns:
            Created ResourcePool.
        """
        pool = ResourcePool(
            entity_id=entity_id,
            initial_stamina=initial_stamina,
            initial_mana=initial_mana,
            initial_gold=initial_gold,
            initial_health=initial_health,
        )
        self.pools[entity_id] = pool
        return pool
        
    def get_action_cost(self, action_name: str) -> Dict[str, float]:
        """Get resource costs for an action.
        
        Args:
            action_name: Action identifier.
            
        Returns:
            Dict of resource → cost.
        """
        return self.action_costs.get(action_name, {})
        
