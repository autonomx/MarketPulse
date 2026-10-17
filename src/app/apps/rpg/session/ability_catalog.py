"""Deterministic ability template catalog."""
from __future__ import annotations

from typing import Any

BUILD_IDENTITY_DEFAULTS: dict[str, dict[str, Any]] = {
    "balanced_adventurer": {"primary_capability": "recon", "secondary_capabilities": ["survival", "combat"], "power_source": "mundane", "class_name": "Adventurer"},
    "warrior": {"primary_capability": "combat", "secondary_capabilities": ["survival", "influence"], "power_source": "martial", "class_name": "Champion"},
    "ranger": {"primary_capability": "recon", "secondary_capabilities": ["survival", "combat"], "power_source": "martial", "class_name": "Ranger"},
    "silver_tongue": {"primary_capability": "influence", "secondary_capabilities": ["knowledge", "recon"], "power_source": "social_power", "class_name": "Diplomat"},
}

GENRE_NORMALIZATION = {
    "classic_fantasy": "classic_fantasy",
    "fantasy": "classic_fantasy",
    "tavern_mystery": "classic_fantasy",
    "bandit_road": "classic_fantasy",
    "wilderness_survival": "classic_fantasy",
    "dungeon_delve": "classic_fantasy",
    "dark_fantasy": "dark_fantasy",
    "cyberpunk": "cyberpunk",
    "space_opera": "space_opera",
    "post_apocalyptic": "post_apocalyptic",
    "modern_occult": "modern_occult",
    "detective_noir": "detective_noir",
    "political_intrigue": "political_intrigue",
    "survival_horror": "survival_horror",
    "sandbox": "sandbox",
}

GENRE_CLASS_NAMES = {
    ("classic_fantasy", "knowledge", "magic"): "Runebinder",
    ("classic_fantasy", "combat", "martial"): "Champion",
    ("classic_fantasy", "recon", "martial"): "Ranger",
    ("classic_fantasy", "recon", "mundane"): "Ranger",
    ("cyberpunk", "technical", "technology"): "Netrunner",
    ("cyberpunk", "combat", "technology"): "Street Samurai",
    ("cyberpunk", "recon", "technology"): "Ghostwalker",
    ("detective_noir", "recon", "mundane"): "Private Eye",
    ("political_intrigue", "influence", "social_power"): "Court Schemer",
    ("post_apocalyptic", "survival", "scrap"): "Scavenger",
    ("space_opera", "knowledge", "psionic"): "Psionic",
    ("space_opera", "technical", "technology"): "Science Officer",
    ("modern_occult", "knowledge", "occult"): "Ritualist",
    ("survival_horror", "survival", "mundane"): "Survivor",
}

TEMPLATE_FAMILIES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("classic_fantasy", "knowledge", "magic"): {"family_id": "classic_fantasy_knowledge_magic_v1", "class_name": "Runebinder", "category_name": "Runes & Arcana", "passive": ("runic_memory", "Runic Memory", "on_investigation_check", "Improves symbol and lore checks."), "trait": ("academy_exile", "Academy Exile", ["recognize_arcane_orders", "unlock_mage_dialogue_paths"])},
    ("classic_fantasy", "combat", "martial"): {"family_id": "classic_fantasy_combat_martial_v1", "class_name": "Champion", "category_name": "Arms & Command", "passive": ("shield_drilled", "Shield Drilled", "on_combat_start", "Improves opening defense."), "trait": ("oathbound_warrior", "Oathbound Warrior", ["recognized_by_soldiers", "unlock_honor_dialogue_paths"])},
    ("classic_fantasy", "recon", "martial"): {"family_id": "classic_fantasy_recon_martial_v1", "class_name": "Ranger", "category_name": "Trail & Bowcraft", "passive": ("keen_trail_eye", "Keen Trail Eye", "on_investigation_check", "Improves track and terrain reads."), "trait": ("borderlands_guide", "Borderlands Guide", ["recognize_wilderness_signs", "unlock_ranger_dialogue_paths"])},
    ("classic_fantasy", "recon", "mundane"): {"family_id": "classic_fantasy_recon_mundane_v1", "class_name": "Ranger", "category_name": "Trailcraft", "passive": ("fieldcraft", "Fieldcraft", "on_enter_location", "Improves mundane travel reads."), "trait": ("roadwise_wanderer", "Roadwise Wanderer", ["recognize_road_signs", "unlock_travel_story_paths"])},
    ("cyberpunk", "technical", "technology"): {"family_id": "cyberpunk_technical_technology_v1", "class_name": "Netrunner", "category_name": "Systems Intrusion", "passive": ("packet_sense", "Packet Sense", "on_investigation_check", "Improves device and network reads."), "trait": ("corporate_defector", "Corporate Defector", ["recognize_corp_protocols", "unlock_corporate_dialogue_paths"])},
    ("cyberpunk", "combat", "technology"): {"family_id": "cyberpunk_combat_technology_v1", "class_name": "Street Samurai", "category_name": "Augmented Combat", "passive": ("cybernetic_reflexes", "Cybernetic Reflexes", "on_combat_start", "Improves initiative and position checks."), "trait": ("street_code", "Street Code", ["recognize_gang_signals", "unlock_street_contact_paths"])},
    ("detective_noir", "recon", "mundane"): {"family_id": "detective_noir_recon_mundane_v1", "class_name": "Private Eye", "category_name": "Casework", "passive": ("keen_eye", "Keen Eye", "on_investigation_check", "Improves clue discovery."), "trait": ("former_detective", "Former Detective", ["recognize_police_procedure", "unlock_detective_dialogue_paths"])},
    ("political_intrigue", "influence", "social_power"): {"family_id": "political_intrigue_influence_social_power_v1", "class_name": "Court Schemer", "category_name": "Court Leverage", "passive": ("courtly_read", "Courtly Read", "on_social_check", "Improves social pressure reads."), "trait": ("disgraced_envoy", "Disgraced Envoy", ["recognize_court_factions", "unlock_court_dialogue_paths"])},
    ("post_apocalyptic", "survival", "scrap"): {"family_id": "post_apocalyptic_survival_scrap_v1", "class_name": "Scavenger", "category_name": "Scrapcraft", "passive": ("scrap_sense", "Scrap Sense", "on_enter_location", "Improves salvage discovery."), "trait": ("wasteland_survivor", "Wasteland Survivor", ["recognize_salvage_value", "unlock_wasteland_paths"])},
    ("space_opera", "knowledge", "psionic"): {"family_id": "space_opera_knowledge_psionic_v1", "class_name": "Psionic", "category_name": "Psi Studies", "passive": ("psionic_focus", "Psionic Focus", "on_investigation_check", "Improves strange-signal interpretation."), "trait": ("trained_mind", "Trained Mind", ["recognize_psionic_echoes", "unlock_psionic_dialogue_paths"])},
    ("space_opera", "technical", "technology"): {"family_id": "space_opera_technical_technology_v1", "class_name": "Science Officer", "category_name": "Ship Systems", "passive": ("systems_discipline", "Systems Discipline", "on_investigation_check", "Improves ship and station diagnostics."), "trait": ("fleet_academy", "Fleet Academy", ["recognize_fleet_protocols", "unlock_science_dialogue_paths"])},
    ("modern_occult", "knowledge", "occult"): {"family_id": "modern_occult_knowledge_occult_v1", "class_name": "Ritualist", "category_name": "Rites & Omens", "passive": ("occult_index", "Occult Index", "on_investigation_check", "Improves cult and ritual clue reads."), "trait": ("cult_survivor", "Cult Survivor", ["recognize_cult_symbols", "unlock_cult_dialogue_paths"])},
    ("survival_horror", "survival", "mundane"): {"family_id": "survival_horror_survival_mundane_v1", "class_name": "Survivor", "category_name": "Last Light", "passive": ("steady_breathing", "Steady Breathing", "on_turn_start", "Improves panic and fatigue recovery."), "trait": ("sole_survivor", "Sole Survivor", ["recognize_horror_tells", "unlock_survivor_dialogue_paths"])},
}

ABILITY_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "recon": [
        {"ability_id": "recon_aimed_shot", "name": "Aimed Shot", "icon": "✦", "description": "Take careful aim and turn position into a decisive opening.", "purpose": "damage", "dimensions": ["position", "resources"], "resource_cost": {"stamina": 10}, "cooldown_turns": 1, "effect_ops": [{"dimension": "position", "op": "modify_next_check", "check": "ranged_attack", "amount": 2, "duration_turns": 1}, {"dimension": "resources", "op": "resource_delta", "target": "target", "resource": "hp", "amount": -10}]},
        {"ability_id": "recon_frost_arrow", "name": "Frost Arrow", "icon": "↯", "description": "Slow a dangerous target and create a safer window to move.", "purpose": "control", "dimensions": ["position", "environment"], "resource_cost": {"mana": 12}, "cooldown_turns": 2, "effect_ops": [{"dimension": "position", "op": "modify_next_check", "check": "enemy_mobility", "amount": -2, "duration_turns": 2}, {"dimension": "environment", "op": "apply_scene_status", "status": "frosted_ground", "duration_turns": 2}]},
        {"ability_id": "recon_camouflage", "name": "Camouflage", "icon": "☘", "description": "Blend into terrain and improve stealth or ambush positioning.", "purpose": "escape", "dimensions": ["position", "environment"], "resource_cost": {"stamina": 8}, "cooldown_turns": 2, "effect_ops": [{"dimension": "position", "op": "modify_next_check", "check": "stealth", "amount": 2, "duration_turns": 3}, {"dimension": "environment", "op": "apply_scene_status", "status": "concealment_found", "duration_turns": 3}]},
        {"ability_id": "recon_radiant_flare", "name": "Radiant Flare", "icon": "✹", "description": "Reveal threats, clues, or hidden movement in the immediate area.", "purpose": "information_gathering", "dimensions": ["information", "environment"], "resource_cost": {"mana": 15}, "cooldown_turns": 3, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "hidden_threat", "strength": 1}, {"dimension": "environment", "op": "apply_scene_status", "status": "illuminated", "duration_turns": 2}]},
        {"ability_id": "recon_volley", "name": "Volley", "icon": "⟡", "description": "Pressure clustered enemies or force movement across a zone.", "purpose": "control", "dimensions": ["position", "resources"], "resource_cost": {"stamina": 15}, "cooldown_turns": 2, "level_required": 2, "effect_ops": [{"dimension": "position", "op": "grant_temp_affordance", "affordance": "suppress_area", "duration_turns": 1}, {"dimension": "resources", "op": "resource_delta", "target": "target", "resource": "hp", "amount": -6}]},
        {"ability_id": "recon_dash", "name": "Dash", "icon": "⇥", "description": "Reposition quickly before danger closes in.", "purpose": "mobility", "dimensions": ["position"], "resource_cost": {"stamina": 12}, "cooldown_turns": 1, "effect_ops": [{"dimension": "position", "op": "grant_temp_affordance", "affordance": "rapid_reposition", "duration_turns": 1}]},
        {"ability_id": "recon_trail_sense", "name": "Trail Sense", "icon": "⌕", "description": "Read tracks, patterns, and subtle signs in the current scene.", "purpose": "information_gathering", "dimensions": ["information", "narrative"], "resource_cost": {"stamina": 5}, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "local_tracks", "strength": 1}, {"dimension": "narrative", "op": "unlock_dialogue_option", "option_tag": "ask_about_tracks", "duration_turns": 3}]},
    ],
    "combat": [
        {"ability_id": "combat_guarded_strike", "name": "Guarded Strike", "icon": "⚔", "description": "Attack while keeping guard high.", "purpose": "damage", "dimensions": ["resources", "position"], "resource_cost": {"stamina": 10}, "cooldown_turns": 1, "effect_ops": [{"dimension": "resources", "op": "resource_delta", "target": "target", "resource": "hp", "amount": -12}, {"dimension": "position", "op": "modify_next_check", "check": "defense", "amount": 1, "duration_turns": 1}]},
        {"ability_id": "combat_rallying_presence", "name": "Rallying Presence", "icon": "▲", "description": "Steady allies and recover momentum.", "purpose": "defense", "dimensions": ["relationships", "resources"], "resource_cost": {"stamina": 8}, "effect_ops": [{"dimension": "relationships", "op": "modify_relationship", "relationship": "party_morale", "amount": 2}, {"dimension": "resources", "op": "resource_delta", "target": "self", "resource": "stamina", "amount": 4}]},
    ],
    "technical": [
        {"ability_id": "technical_signal_probe", "name": "Signal Probe", "icon": "⌁", "description": "Scan systems or devices for a weak point.", "purpose": "information_gathering", "dimensions": ["information", "access"], "resource_cost": {"mana": 6}, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "system_weakness", "strength": 1}, {"dimension": "access", "op": "unlock_scene_affordance", "affordance": "technical_bypass_hint", "duration_turns": 3}]},
        {"ability_id": "technical_camera_loop", "name": "Camera Loop", "icon": "◉", "description": "Spoof surveillance to create a movement window.", "purpose": "access_bypass", "dimensions": ["access", "environment"], "resource_cost": {"mana": 10}, "cooldown_turns": 3, "level_required": 2, "prerequisites": ["technical_signal_probe"], "effect_ops": [{"dimension": "access", "op": "unlock_scene_affordance", "affordance": "cross_monitored_route", "duration_turns": 3}, {"dimension": "environment", "op": "apply_scene_status", "status": "surveillance_looped", "duration_turns": 3}]},
    ],
    "knowledge": [
        {"ability_id": "knowledge_read_the_room", "name": "Read the Room", "icon": "◇", "description": "Notice hidden patterns, symbols, or social pressure.", "purpose": "information_gathering", "dimensions": ["information", "narrative"], "resource_cost": {"mana": 5}, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "hidden_context", "strength": 1}, {"dimension": "narrative", "op": "unlock_dialogue_option", "option_tag": "press_hidden_context", "duration_turns": 2}]},
        {"ability_id": "knowledge_warding_formula", "name": "Warding Formula", "icon": "✹", "description": "Use knowledge to create a short-lived protection.", "purpose": "defense", "dimensions": ["environment", "position"], "resource_cost": {"mana": 12}, "cooldown_turns": 2, "effect_ops": [{"dimension": "environment", "op": "apply_scene_status", "status": "warded", "duration_turns": 3}, {"dimension": "position", "op": "modify_next_check", "check": "defense", "amount": 2, "duration_turns": 3}]},
    ],
    "influence": [
        {"ability_id": "influence_call_in_favor", "name": "Call in a Favor", "icon": "☯", "description": "Spend social capital to open a route or concession.", "purpose": "relationship_building", "dimensions": ["relationships", "access"], "resource_cost": {"mana": 5}, "cooldown_turns": 2, "effect_ops": [{"dimension": "relationships", "op": "modify_relationship", "relationship": "local_contacts", "amount": 1}, {"dimension": "access", "op": "unlock_dialogue_option", "option_tag": "call_in_favor", "duration_turns": 3}]},
        {"ability_id": "influence_cutting_insight", "name": "Cutting Insight", "icon": "✧", "description": "Name the pressure point in a negotiation.", "purpose": "social_manipulation", "dimensions": ["information", "relationships"], "resource_cost": {"stamina": 6}, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "social_leverage", "strength": 1}, {"dimension": "relationships", "op": "modify_reputation", "amount": 1}]},
    ],
    "survival": [
        {"ability_id": "survival_make_camp", "name": "Make Camp", "icon": "♨", "description": "Create a safer short rest point and recover stamina.", "purpose": "survival", "dimensions": ["resources", "environment"], "resource_cost": {}, "cooldown_turns": 4, "effect_ops": [{"dimension": "resources", "op": "resource_delta", "target": "self", "resource": "stamina", "amount": 12}, {"dimension": "environment", "op": "apply_scene_status", "status": "safe_camp", "duration_turns": 4}]},
        {"ability_id": "survival_scavenge", "name": "Scavenge", "icon": "▣", "description": "Search for usable scraps, supplies, or signs.", "purpose": "resource_generation", "dimensions": ["resources", "information", "narrative"], "resource_cost": {"stamina": 6}, "effect_ops": [{"dimension": "information", "op": "reveal_clue", "clue_tag": "salvage_source", "strength": 1}, {"dimension": "narrative", "op": "grant_temp_affordance", "affordance": "found_minor_supplies", "duration_turns": 1}]},
    ],
    "support": [
        {"ability_id": "support_field_aid", "name": "Field Aid", "icon": "✚", "description": "Stabilize injuries and restore HP.", "purpose": "healing", "dimensions": ["resources"], "resource_cost": {"mana": 8}, "cooldown_turns": 2, "effect_ops": [{"dimension": "resources", "op": "resource_delta", "target": "self", "resource": "hp", "amount": 18}]},
        {"ability_id": "support_steady_voice", "name": "Steady Voice", "icon": "☼", "description": "Calm panic and ease the next group action.", "purpose": "defense", "dimensions": ["relationships", "position"], "resource_cost": {"stamina": 6}, "effect_ops": [{"dimension": "relationships", "op": "modify_relationship", "relationship": "party_morale", "amount": 2}, {"dimension": "position", "op": "modify_next_check", "check": "group_action", "amount": 1, "duration_turns": 2}]},
    ],
}
