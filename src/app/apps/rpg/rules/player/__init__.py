# Player module for RPG system

from .agency_system import AgencySystem, PlayerChoice
from .player_codex import update_codex_from_state
from .player_creation import (
    apply_character_creation,
    build_default_stat_allocation,
    validate_stat_allocation,
)
from .player_dialogue_state import enter_dialogue_mode, exit_dialogue_mode
from .player_encounter import build_encounter_view
from .player_experience import (
    PLAYER_STYLES,
    PLAYER_VALUES,
    AttentionDirector,
    EmotionalFeedbackLoop,
    MemoryEcho,
    MemoryEchoSystem,
    NarrativeSurfacer,
    PlayerExperienceEngine,
    PlayerProfile,
    SurfacedEvent,
)

# Phase 9 — Inventory system
from .player_inventory import (
    build_player_inventory_view,
    ensure_player_inventory,
)
from .player_journal import update_journal_from_state

# Phase 9.2 — Party system
from .player_party import (
    ensure_player_party,
)

# Phase 18.3A — Progression
from .player_progression_state import (
    allocate_starting_stats,
    award_player_xp,
    award_skill_xp,
    ensure_player_progression_state,
    get_skill_level,
    get_stat_modifier,
    resolve_level_ups,
    resolve_skill_level_ups,
)

# Phase 8 — Player-facing UX Layer
from .player_scene_state import (
    ensure_player_state,
    push_scene_history,
    set_current_scene,
)

__all__ = [
    "AgencySystem",
    "PlayerChoice",
    "PlayerExperienceEngine",
    "PlayerProfile",
    "SurfacedEvent",
    "MemoryEcho",
    "NarrativeSurfacer",
    "AttentionDirector",
    "EmotionalFeedbackLoop",
    "MemoryEchoSystem",
    "PLAYER_STYLES",
    "PLAYER_VALUES",
    # Phase 8
    "ensure_player_state",
    "set_current_scene",
    "push_scene_history",
    "enter_dialogue_mode",
    "exit_dialogue_mode",
    "update_journal_from_state",
    "update_codex_from_state",
    "build_encounter_view",
    # Phase 9
    "ensure_player_inventory",
    "build_player_inventory_view",
    # Phase 9.2
    "ensure_player_party",
    # Phase 18.3A
    "ensure_player_progression_state",
    "allocate_starting_stats",
    "award_player_xp",
    "award_skill_xp",
    "resolve_level_ups",
    "resolve_skill_level_ups",
    "get_stat_modifier",
    "get_skill_level",
    "build_default_stat_allocation",
    "validate_stat_allocation",
    "apply_character_creation",
]
