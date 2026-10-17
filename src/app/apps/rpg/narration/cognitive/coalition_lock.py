"""Coalition Lock — Tier 12: Coalition Commitment Lock.

This module implements Tier 12's Coalition Commitment Lock that prevents
intent oscillation when coalition and learning systems conflict.

Problem:
    Coalition encourages attack
    Attack fails
    Learning reduces attack priority
    Coalition re-boosts it
    
    Result: infinite oscillation, jittery behavior

Solution:
    When a coalition commits to a coordinated action, a lock is placed
    that prevents the intent from being changed until the lock expires.
    This creates stability and follows through on coalition commitments.

Usage:
    lock_manager = CoalitionLockManager()
    
    # When coalition forms a plan
    lock_manager.acquire_lock(
        character_id="npc_1",
        target="enemy_2",
        intent_type="coordinated_attack",
        duration_ticks=10,
        current_tick=50,
    )
    
    # Check if locked before changing intent
    if lock_manager.is_locked("npc_1"):
        intent = lock_manager.enforce_lock("npc_1", intent)

Design Rules:
    - Locks are temporary and expire automatically
    - Locks only affect specific intent types
    - Locks can be overridden in emergencies (priority < threshold)
    - Locks expire after configured duration
    - Multiple locks can exist for same character
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_LOCK_DURATION = 10  # Ticks
EMERGENCY_THRESHOLD = 2.0   # Below this priority, lock can be broken
MAX_LOCKS_PER_CHARACTER = 3


@dataclass
class CoalitionLock:
    """A lock on a character's intent due to coalition commitment.
    
    Attributes:
        character_id: Character this lock applies to.
        target: Target of the locked intent.
        intent_type: Intent type that is locked.
        acquired_tick: Tick when lock was acquired.
        expires_tick: Tick when lock expires.
        coalition_id: Coalition that placed this lock.
        priority: Priority level of the locked intent.
    """
    
    character_id: str
    target: str
    intent_type: str
    acquired_tick: int = 0
    expires_tick: int = 0
    coalition_id: str = ""
    priority: float = 5.0
    
    def is_active(self, current_tick: int) -> bool:
        """Check if lock is currently active.
        
        Args:
            current_tick: Current simulation tick.
            
        Returns:
            True if lock is active.
        """
        return current_tick < self.expires_tick
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize lock to dict.
        
        Returns:
            Lock data dict.
        """
        return {
            "character_id": self.character_id,
            "target": self.target,
            "intent_type": self.intent_type,
            "acquired_tick": self.acquired_tick,
            "expires_tick": self.expires_tick,
            "coalition_id": self.coalition_id,
            "priority": self.priority,
            "active": True,
        }


class CoalitionLockManager:
    """Manages coalition commitment locks to prevent intent oscillation.
    
    The CoalitionLockManager ensures that when NPCs commit to coalition
    actions, they follow through without flip-flopping due to
    conflicting signals from learning or other systems.
    
    Usage:
        manager = CoalitionLockManager()
        
        # Acquire lock when coalition coordinates
        manager.acquire_lock(
            "npc_1", "enemy_1", "coordinated_attack",
            duration=10, current_tick=50,
            coalition_id="coalition_1"
        )
        
        # Enforce lock during intent modification
        intent = manager.enforce_lock("npc_1", intent, current_tick=55)
    """
    
    def __init__(
        self,
        default_duration: int = DEFAULT_LOCK_DURATION,
        emergency_threshold: float = EMERGENCY_THRESHOLD,
    ):
        """Initialize the CoalitionLockManager.
        
        Args:
            default_duration: Default lock duration in ticks.
            emergency_threshold: Priority below which locks can be broken.
        """
        self.default_duration = default_duration
        self.emergency_threshold = emergency_threshold
        
        # Active locks: char_id -> list of CoalitionLock
        self._locks: Dict[str, List[CoalitionLock]] = {}
        
        self._stats = {
            "locks_acquired": 0,
            "locks_expired": 0,
            "locks_enforced": 0,
            "locks_broken": 0,
        }
    
    def acquire_lock(
        self,
        character_id: str,
        target: str,
        intent_type: str,
        duration: int = 0,
        current_tick: int = 0,
        coalition_id: str = "",
        priority: float = 5.0,
    ) -> Optional[CoalitionLock]:
        """Acquire a coalition lock for a character.
        
        Args:
            character_id: Character to lock.
            target: Target of the locked intent.
            intent_type: Intent type to lock.
            duration: Lock duration in ticks (0 = default).
            current_tick: Current simulation tick.
            coalition_id: Coalition placing the lock.
            priority: Priority level of locked intent.
            
        Returns:
            CoalitionLock if acquired, None if limit reached.
        """
        # Check lock limit
        existing = self._locks.get(character_id, [])
        if len(existing) >= MAX_LOCKS_PER_CHARACTER:
            # Clean up expired locks first
            existing = self._clean_expired_locks(character_id, current_tick)
            if len(existing) >= MAX_LOCKS_PER_CHARACTER:
                logger.warning(
                    f"Cannot acquire lock for {character_id}: "
                    f"max locks ({MAX_LOCKS_PER_CHARACTER}) reached"
                )
                return None
        
        lock = CoalitionLock(
            character_id=character_id,
            target=target,
            intent_type=intent_type,
            acquired_tick=current_tick,
            expires_tick=current_tick + (duration or self.default_duration),
            coalition_id=coalition_id,
            priority=priority,
        )
        
        if character_id not in self._locks:
            self._locks[character_id] = []
        self._locks[character_id].append(lock)
        
        self._stats["locks_acquired"] += 1
        logger.debug(
            f"Coalition lock acquired for {character_id}: "
            f"{intent_type} -> {target} (expires tick {lock.expires_tick})"
        )
        
        return lock
    
    def is_locked(
        self,
        character_id: str,
        current_tick: int = 0,
        intent_type: Optional[str] = None,
    ) -> bool:
        """Check if character has an active lock.
        
        Args:
            character_id: Character to check.
            current_tick: Current simulation tick.
            intent_type: Optional intent type to filter.
            
        Returns:
            True if character has active lock matching criteria.
        """
        locks = self._locks.get(character_id, [])
        
        for lock in locks:
            if lock.is_active(current_tick):
                if intent_type is None or lock.intent_type == intent_type:
                    return True
        
        return False
    
    def enforce_lock(
        self,
        character_id: str,
        intent: Dict[str, Any],
        current_tick: int = 0,
    ) -> Dict[str, Any]:
        """Enforce coalition lock on intent.
        
        If character has an active lock, modifies intent to match
        the locked intent type and target.
        
        Args:
            character_id: Character with potential lock.
            intent: Current intent dict.
            current_tick: Current simulation tick.
            
        Returns:
            Modified intent if lock enforced, original otherwise.
        """
        if intent is None:
            return intent
        
        locks = self._locks.get(character_id, [])
        active_locks = [l for l in locks if l.is_active(current_tick)]
        
        if not active_locks:
            return intent
        
        # Check for emergency override
        intent_priority = intent.get("priority", 5.0)
        if intent_priority < self.emergency_threshold:
            # Emergency: allow intent change despite lock
            self._stats["locks_broken"] += 1
            return intent
        
        # Find matching lock
        intent_type = intent.get("type", "")
        matching_lock = None
        for lock in active_locks:
            if lock.intent_type == intent_type or intent_type.startswith("coordinated"):
                matching_lock = lock
                break
        
        # If no exact match, use first active lock
        if matching_lock is None:
            matching_lock = active_locks[0]
        
        # Enforce lock - override intent
        original_type = intent_type
        original_target = intent.get("target")
        
        intent["type"] = matching_lock.intent_type
        intent["target"] = matching_lock.target
        intent["priority"] = max(intent_priority, matching_lock.priority)
        intent["coalition_locked"] = True
        intent["coalition_id"] = matching_lock.coalition_id
        intent["lock_expires"] = matching_lock.expires_tick
        intent["reasoning"] = (
            f"{intent.get('reasoning', '')} "
            f"[Coalition Lock: {matching_lock.intent_type} "
            f"until tick {matching_lock.expires_tick}]"
        )
        
        self._stats["locks_enforced"] += 1
        
        logger.debug(
            f"Coalition lock enforced for {character_id}: "
            f"{original_type}/{original_target} -> "
            f"{matching_lock.intent_type}/{matching_lock.target}"
        )
        
        return intent
    
    def release_lock(
        self,
        character_id: str,
        intent_type: Optional[str] = None,
    ) -> bool:
        """Release a coalition lock early.
        
        Args:
            character_id: Character to release lock for.
            intent_type: Optional specific intent type to release.
            
        Returns:
            True if lock was released.
        """
        locks = self._locks.get(character_id, [])
        
        if intent_type:
            for i, lock in enumerate(locks):
                if lock.intent_type == intent_type:
                    self._locks[character_id].pop(i)
                    return True
        else:
            if locks:
                self._locks[character_id].pop(0)
                return True
        
        return False
    
    def _clean_expired_locks(
        self,
        character_id: str,
        current_tick: int,
    ) -> List[CoalitionLock]:
        """Clean up expired locks for a character.
        
        Args:
            character_id: Character to clean locks for.
            current_tick: Current simulation tick.
            
        Returns:
            List of remaining active locks.
        """
        locks = self._locks.get(character_id, [])
        expired = [l for l in locks if not l.is_active(current_tick)]
        
        if expired:
            active = [l for l in locks if l.is_active(current_tick)]
            self._locks[character_id] = active
            self._stats["locks_expired"] += len(expired)
            logger.debug(
                f"Cleaned {len(expired)} expired locks for {character_id}"
            )
        
        return self._locks.get(character_id, [])
    
    def get_stats(self) -> Dict[str, int]:
        """Get lock manager statistics.
        
        Returns:
            Stats dict.
        """
        active_count = sum(
            len(self._get_active_locks_for_char(char_id))
            for char_id in self._locks
        )
        
        return {
            **self._stats,
            "active_locks": active_count,
            "locked_characters": len(
                [c for c in self._locks if self._get_active_locks_for_char(c)]
            ),
        }
    
    def _get_active_locks_for_char(self, character_id: str) -> List[CoalitionLock]:
        """Get active locks for a character (internal, no tick check).
        
        Args:
            character_id: Character to query.
            
        Returns:
            List of locks.
        """
        return self._locks.get(character_id, [])
    
    def reset(self) -> None:
        """Reset all locks and statistics."""
        self._locks.clear()
        self._stats = {
            "locks_acquired": 0,
            "locks_expired": 0,
            "locks_enforced": 0,
            "locks_broken": 0,
        }