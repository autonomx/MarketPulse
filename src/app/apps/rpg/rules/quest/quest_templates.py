"""Quest arc templates for the Quest Emergence Engine.

This module defines pre-defined quest arc templates that specify
multi-stage narrative structures. Each arc type has stages with
objectives and world effects that shape gameplay.

Quest Arc Types:
    - conflict: Faction conflict that escalates to war
    - betrayal: Suspicion leading to revelation and confrontation
    - supply: Economic shortage requiring player action
    - alliance: Diplomatic quest to strengthen faction relations

Each arc follows the structure:
    setup → escalation → climax → resolution
"""

from typing import Any, Dict, List

# Quest arc templates defining multi-stage narrative structures
QUEST_ARCS: Dict[str, List[Dict[str, Any]]] = {
    "conflict": [
        {
            "name": "setup",
            "description": "Tensions begin to rise between factions",
            "objectives": ["Investigate rising tensions"],
            "world_effects": {},
        },
        {
            "name": "escalation",
            "description": "Conflict intensifies",
            "objectives": ["Choose a side", "Prepare for confrontation"],
            "world_effects": {
                "tension_level": 0.3,
            },
        },
        {
            "name": "climax",
            "description": "The conflict erupts into open hostility",
            "objectives": ["Resolve the battle"],
            "world_effects": {
                "tension_level": 0.5,
                "combat_active": True,
            },
        },
        {
            "name": "resolution",
            "description": "Aftermath reshapes the world",
            "objectives": ["Deal with consequences"],
            "world_effects": {
                "faction_power_shift": True,
                "tension_level": -0.3,
            },
        },
    ],
    "betrayal": [
        {
            "name": "setup",
            "description": "Something feels off - suspicions arise",
            "objectives": ["Notice suspicious behavior"],
            "world_effects": {},
        },
        {
            "name": "reveal",
            "description": "The betrayal is uncovered",
            "objectives": ["Uncover the betrayal", "Gather evidence"],
            "world_effects": {
                "trust_network": -0.3,
            },
        },
        {
            "name": "confrontation",
            "description": "Face the betrayer",
            "objectives": ["Confront the betrayer"],
            "world_effects": {
                "trust_network": -0.2,
            },
        },
        {
            "name": "resolution",
            "description": "Decide their fate and deal with aftermath",
            "objectives": ["Decide their fate"],
            "world_effects": {
                "trust_network": -0.5,
                "reputation_change": "variable",
            },
        },
    ],
    "supply": [
        {
            "name": "setup",
            "description": "A shortage begins to be noticed",
            "objectives": ["Investigate the shortage"],
            "world_effects": {},
        },
        {
            "name": "escalation",
            "description": "The shortage becomes critical",
            "objectives": ["Find a source for the goods", "Arrange transport"],
            "world_effects": {
                "economic_pressure": 0.3,
            },
        },
        {
            "name": "climax",
            "description": "Race against time to deliver supplies",
            "objectives": ["Deliver the supplies before crisis"],
            "world_effects": {
                "economic_pressure": 0.5,
            },
        },
        {
            "name": "resolution",
            "description": "Crisis averted, markets stabilize",
            "objectives": ["Collect reward", "Assess market impact"],
            "world_effects": {
                "economic_pressure": -0.4,
                "faction_stability": 0.2,
            },
        },
    ],
    "alliance": [
        {
            "name": "setup",
            "description": "Two factions express interest in closer ties",
            "objectives": ["Speak to faction leaders"],
            "world_effects": {},
        },
        {
            "name": "escalation",
            "description": "Negotiations require delicate handling",
            "objectives": ["Negotiate terms", "Resolve disputes"],
            "world_effects": {
                "diplomatic_tension": 0.2,
            },
        },
        {
            "name": "climax",
            "description": "Final agreement requires your mediation",
            "objectives": ["Broker the final deal"],
            "world_effects": {
                "diplomatic_tension": 0.3,
            },
        },
        {
            "name": "resolution",
            "description": "Alliance formed, stability increases",
            "objectives": ["Witness the signing ceremony"],
            "world_effects": {
                "diplomatic_tension": -0.3,
                "faction_stability": 0.3,
            },
        },
    ],
    "rebellion": [
        {
            "name": "setup",
            "description": "Whispers of discontent spread",
            "objectives": ["Listen to the rumors"],
            "world_effects": {
                "political_instability": 0.1,
            },
        },
        {
            "name": "escalation",
            "description": "Organized resistance forms",
            "objectives": ["Identify rebel leaders", "Assess rebel strength"],
            "world_effects": {
                "political_instability": 0.3,
            },
        },
        {
            "name": "climax",
            "description": "The rebellion makes its move",
            "objectives": ["Choose sides in the uprising"],
            "world_effects": {
                "political_instability": 0.5,
                "combat_active": True,
            },
        },
        {
            "name": "resolution",
            "description": "The dust settles on a new order",
            "objectives": ["Deal with the aftermath"],
            "world_effects": {
                "political_instability": -0.3,
                "leadership_change": True,
            },
        },
    ],
}

# Quest type to arc type mapping for dynamic quest detection
QUEST_TYPE_ARC_MAP: Dict[str, str] = {
    "war": "conflict",
    "conflict": "conflict",
    "betrayal": "betrayal",
    "treason": "betrayal",
    "supply": "supply",
    "shortage": "supply",
    "crisis": "supply",
    "alliance": "alliance",
    "diplomacy": "alliance",
    "trade": "supply",
    "rebellion": "rebellion",
    "coup": "rebellion",
}


def get_arc_type_for_quest(quest_type: str) -> str:
    """Map a quest type to its corresponding arc type.

    Args:
        quest_type: Type of quest (e.g., "war", "betrayal").

    Returns:
        Corresponding arc type string.
    """
    return QUEST_TYPE_ARC_MAP.get(quest_type, "conflict")