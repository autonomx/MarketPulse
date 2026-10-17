"""Event Adapter — Tier 14: Event Normalizer.

This module provides event normalization utilities to ensure consistent
event shapes across the narrative surface engine.

Problem:
    Different simulation systems output events with inconsistent field names:
    - Some use "actor", others use "actor_id"
    - Some use "type", others use "action_type"
    - Emotional state may be "emotions" or "emotional_state"
    
    The NarrativeSurfaceEngine expects consistent shapes.

Solution:
    normalize_event() converts any raw event dict into the canonical
    format expected by NarrativeSurfaceEngine.narrate().

Canonical Event Format:
    {
        "actor": str,           # Primary actor name/id
        "target": str,          # Target actor name/id (optional)
        "type": str,            # Event type (conflict, alliance, betrayal, etc.)
        "intensity": float,     # 0.0-1.0 intensity value
        "emotions": dict,       # {emotion_name: intensity} mapping
        "importance": float,    # 0.0-1.0 importance value
        "location": str,        # Where the event happened (optional)
        "tick": int,            # Simulation tick when event occurred
    }

Usage:
    raw_event = {"actor_id": "Alice", "target_id": "Bob", "action_type": "conflict"}
    normalized = normalize_event(raw_event)
    # {"actor": "Alice", "target": "Bob", "type": "conflict", ...}

Design Rules:
    - Never modify the original event dict
    - Provide sensible defaults for missing fields
    - Preserve all original fields (additive, not destructive)
    - Handle None/null values gracefully
"""

from __future__ import annotations

from typing import Any, Dict

# Field name mappings: raw_field -> canonical_field
_FIELD_MAPPINGS: Dict[str, str] = {
    # Actor fields
    "actor_id": "actor",
    "agent": "actor",
    "agent_id": "actor",
    "character": "actor",
    "character_id": "actor",
    "faction_a": "actor",
    "source": "actor",
    "source_id": "actor",
    # Target fields
    "target_id": "target",
    "victim": "target",
    "victim_id": "target",
    "target_character": "target",
    "target_character_id": "target",
    "faction_b": "target",
    "destination": "target",
    "betrayer": "actor",
    # Type fields
    "action_type": "type",
    "event_type": "type",
    "kind": "type",
    "category": "type",
    # Intensity fields
    "strength": "intensity",
    "magnitude": "intensity",
    "power": "intensity",
    "impact": "intensity",
    "severity": "intensity",
    # Emotional state fields
    "emotional_state": "emotions",
    "emotions_raw": "emotions",
    "mood": "emotions",
    "affect": "emotions",
    # Location fields
    "place": "location",
    "scene": "location",
    "area": "location",
    # Other common aliases
    "description": "detail",
    "details": "detail",
    "data": "detail",
}

# Default values for canonical fields
_DEFAULTS: Dict[str, Any] = {
    "actor": "unknown",
    "target": None,
    "type": "general",
    "intensity": 0.5,
    "emotions": {},
    "importance": 0.5,
    "location": "unknown",
    "tick": -1,
}


def normalize_event(raw_event: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw event dict into canonical format.
    
    Converts various field name conventions into the canonical format
    expected by NarrativeSurfaceEngine.
    
    Args:
        raw_event: Raw event dict from simulation. May use various
                   field naming conventions.
                   
    Returns:
        New dict with canonical field names. Original event is not
        modified. All original fields are preserved alongside canonical
        fields.
        
    Example:
        >>> raw = {"actor_id": "Alice", "target_id": "Bob", "action_type": "conflict"}
        >>> normalized = normalize_event(raw)
        >>> normalized["actor"]
        'Alice'
        >>> normalized["type"]
        'conflict'
    """
    if not raw_event:
        return dict(_DEFAULTS)
    
    # Start with defaults
    result: Dict[str, Any] = dict(_DEFAULTS)
    
    # Copy all original fields (preserve original data)
    result.update(raw_event)
    
    # Map alternative field names to canonical names
    for raw_key, canonical_key in _FIELD_MAPPINGS.items():
        if raw_key in raw_event and canonical_key not in raw_event:
            value = raw_event[raw_key]
            if value is not None and value != "":
                result[canonical_key] = value
    
    # Ensure intensity is a valid float
    try:
        intensity = float(result.get("intensity", _DEFAULTS["intensity"]))
        result["intensity"] = max(0.0, min(1.0, intensity))
    except (ValueError, TypeError):
        result["intensity"] = _DEFAULTS["intensity"]
    
    # Ensure importance is a valid float
    try:
        importance = float(result.get("importance", _DEFAULTS["importance"]))
        result["importance"] = max(0.0, min(1.0, importance))
    except (ValueError, TypeError):
        result["importance"] = _DEFAULTS["importance"]
    
    # Ensure emotions is a dict
    if not isinstance(result.get("emotions"), dict):
        result["emotions"] = {}
    
    # Ensure tick is an int
    try:
        tick = result.get("tick", _DEFAULTS["tick"])
        result["tick"] = int(tick) if tick is not None else _DEFAULTS["tick"]
    except (ValueError, TypeError):
        result["tick"] = _DEFAULTS["tick"]
    
    # Normalize type to lowercase
    if isinstance(result.get("type"), str):
        result["type"] = result["type"].lower()
    
    return result


