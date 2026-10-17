"""Phase 10 — RPG Presentation package.

Provides presentation builders for scene/dialogue payloads, speaker cards,
personality style helpers, and product-layer UX helpers used by both
the API layer and frontend rendering.
"""
from .dialogue_fallbacks import (
    build_deterministic_dialogue_fallback,
    build_deterministic_scene_fallback,
)
from .dialogue_presentation import (
    build_dialogue_presentation_payload,
)
from .dialogue_prompt_builder import (
    build_dialogue_llm_payload,
    build_scene_llm_payload,
)
from .dialogue_ux import (
    build_dialogue_ux_payload,
)
from .intro_scene import (
    build_intro_scene_payload,
)
from .live_provider_bridge import (
    build_live_provider_presentation_payload,
)
from .narrative_recap import (
    build_narrative_recap_payload,
)
from .orchestration_bridge import (
    build_orchestration_presentation_payload,
)
from .personality import (
    build_personality_prompt_hints,
    build_personality_style_tags,
)
from .personality_state import (
    build_personality_summary,
    ensure_personality_state,
    get_actor_personality_profile,
)
from .player_inspector import (
    build_player_inspector_overlay_payload,
)
from .runtime_bridge_n1222 import (
    build_runtime_presentation_payload,
)
from .save_load_ux import (
    build_save_load_ux_payload,
)
from .scene_presentation import (
    build_scene_presentation_payload,
)
from .setup_flow import (
    build_setup_flow_payload,
)
from .speaker_cards import (
    build_nearby_npc_cards,
    build_party_speaker_cards,
    build_speaker_cards,
)
from .visible_response import build_visible_response, visible_response_text

__all__ = [
    "build_personality_prompt_hints",
    "build_personality_style_tags",
    "ensure_personality_state",
    "get_actor_personality_profile",
    "build_personality_summary",
    "build_speaker_cards",
    "build_party_speaker_cards",
    "build_nearby_npc_cards",
    "build_scene_presentation_payload",
    "build_dialogue_presentation_payload",
    "build_dialogue_llm_payload",
    "build_scene_llm_payload",
    "build_deterministic_dialogue_fallback",
    "build_deterministic_scene_fallback",
    "build_runtime_presentation_payload",
    "build_orchestration_presentation_payload",
    "build_live_provider_presentation_payload",
    "build_setup_flow_payload",
    "build_intro_scene_payload",
    "build_dialogue_ux_payload",
    "build_player_inspector_overlay_payload",
    "build_save_load_ux_payload",
    "build_narrative_recap_payload",
    "build_visible_response",
    "visible_response_text",
]
