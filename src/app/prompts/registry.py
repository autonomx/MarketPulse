"""Where Omnix declares its prompt templates (WP-8.4).

Features declare each model-facing prompt once, as a module-level
``prompt_template(...)`` constant beside the code that sends it (see
``app.prompts.models.prompt_template``). ``PROMPT_MODULES`` lists those
modules; ``load_templates`` imports them and returns the templates by id. The
golden test pins every template's version and text hash, so changing a prompt
is a reviewed diff that also bumps its version.

Nothing imports this module at runtime: it exists for tests and tools.
"""
from __future__ import annotations

import importlib

from .models import PromptTemplate

PROMPT_MODULES: tuple[str, ...] = (
    "app.platform.agent_runtime.coding_quality",
    "app.platform.agent_runtime.pi_runtime",
    "app.platform.agent_runtime.pi_runtime_core",
    "app.platform.agent_runtime.planning_review",
    "app.platform.agent_runtime.review_orchestration_core",
    "app.platform.agent_runtime.semantic_classifier",
    "app.platform.agent_runtime.semantic_task_parser",
    "app.platform.agent_runtime.service",
    "app.platform.agent_runtime.service_core",
    "app.platform.assistant_memory.initiative",
    "app.platform.assistant_memory.paralinguistic_state",
    "app.platform.assistant_memory.structured_provider",
    "app.platform.characters.avatar_generation_service",
    "app.platform.characters.avatar_viseme_generation",
    "app.platform.chat.assist.hermes",
    "app.platform.chat.live_agent_store",
    "app.platform.chat.live_call_greeting",
    "app.platform.chat.live_conversation_proactive",
    "app.platform.chat.session_defaults",
    "app.platform.chat.session_identity",
    "app.apps.desktop_companion.commentary",
    "app.apps.desktop_companion.observation",
    "app.providers.chatgpt_codex_provider",
    "app.providers.desktop_vision",
    "app.apps.trading.screener_words",
    "app.apps.rpg.session.action_intelligence",
    "app.apps.rpg.session.compact_dialogue",
    "app.apps.rpg.narration.ai.conversation_prompt_builder",
    "app.apps.rpg.narration.ai.grounding_soft_audit",
    "app.apps.rpg.narration.ai.llm_mind.npc_prompt_builder",
    "app.apps.rpg.narration.ai.memory_narration_grounding",
    "app.apps.rpg.session.semantic_action_intelligence",
    "app.apps.rpg.narration.ai.survival_narration_grounding",
    "app.apps.rpg.narration.ai.world_scene_narrator_prompts",
    "app.apps.rpg.edge.api.feature_routes.rpg_world_image_routes",
    "app.apps.rpg.narration.cognitive.intent_enrichment",
    "app.apps.rpg.narration.cognitive.resolution_engine",
    "app.apps.rpg.session.jobs.turn_executor",
    "app.apps.rpg.narration.combat_prompt",
    "app.apps.rpg.narration.narrator",
    "app.apps.rpg.narration.runtime_narration_legacy",
    "app.apps.rpg.narration.narrative.narrative_generator",
    "app.apps.rpg.narration.narrative_provider",
    "app.apps.rpg.narration.npc_dialogue.intelligence",
    "app.apps.rpg.narration.presentation.current_turn_prompt_contract",
    "app.apps.rpg.narration.presentation.personality",
    "app.apps.rpg.genesis.profiles.llm_profile_drafter",
    "app.apps.rpg.genesis.profiles.profile_portraits",
    "app.apps.rpg.session.ability_detail",
    "app.apps.rpg.session.environment_narration",
    "app.apps.rpg.genesis.forge.campaign_lore_store",
    "app.apps.rpg.genesis.forge.runtime_lore_materialization",
    "app.apps.rpg.genesis.forge.runtime_materialization",
    "app.apps.rpg.genesis.forge.world_forge_dossiers",
    "app.apps.rpg.session.item_detail",
    "app.apps.rpg.narration.memory_prompt",
    "app.apps.rpg.session.player_agency_contract",
    "app.apps.rpg.session.semantic_state_changes",
    "app.apps.rpg.genesis.worlds.generation_first_pass_provider",
    "app.apps.rpg.genesis.worlds.providers.single_pass",
    "app.apps.rpg.genesis.worlds.providers.world_forge_foundation",
    "app.apps.rpg.genesis.worlds.world_images",
    "app.apps.story.jobs",
    "app.apps.trading.research.market_brief",
    "app.apps.trading.research.market_research",
)


def load_templates(modules: tuple[str, ...] = PROMPT_MODULES) -> dict[str, PromptTemplate]:
    """Import every prompt module and return its templates by id."""
    templates: dict[str, PromptTemplate] = {}
    for name in modules:
        declared = [value for value in vars(importlib.import_module(name)).values()
                    if isinstance(value, PromptTemplate)]
        if not declared:
            raise ValueError(f"{name} declares no prompt templates")
        for template in declared:
            if template.id in templates and templates[template.id] is not template:
                raise ValueError(f"prompt id {template.id} is declared twice")
            templates[template.id] = template
    return templates
