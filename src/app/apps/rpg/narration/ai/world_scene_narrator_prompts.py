"""Split helpers for RPG world scene narration."""

from __future__ import annotations

from app.apps.rpg.narration.ai.memory_narration_grounding import memory_narration_prompt_block
from app.apps.rpg.narration.ai.world_scene_survival_grounding_bridge import (
    append_survival_grounding_to_prompt,
)

from app.apps.rpg.narration.ai.world_scene_narrator_structured import (
    annotations as annotations, json as json, logging as logging, re as re, traceback as traceback, dataclass as dataclass, field as field, Any as Any, Callable as Callable,
    Dict as Dict, List as List, Optional as Optional, normalize_grounding_settings as normalize_grounding_settings,
    select_grounded_narration_candidate as select_grounded_narration_candidate, memory_reference_is_backed as memory_reference_is_backed,
    build_runtime_npc_response_architecture as build_runtime_npc_response_architecture, build_runtime_current_turn_prompt_contract as build_runtime_current_turn_prompt_contract,
    format_runtime_prompt_contract_block as format_runtime_prompt_contract_block, build_runtime_presentation_guardrails_block as build_runtime_presentation_guardrails_block,
    sanitize_unsupported_combat_payload as sanitize_unsupported_combat_payload, parse_runtime_provider_payload as parse_runtime_provider_payload,
    build_encounter_view as build_encounter_view, logger as logger, _ACTIVE_NARRATIONS as _ACTIVE_NARRATIONS, NARRATION_JSON_FORMAT_VERSION as NARRATION_JSON_FORMAT_VERSION,
    NARRATION_JSON_SCHEMA_HINT as NARRATION_JSON_SCHEMA_HINT, _extract_llm_text as _extract_llm_text, _llm_text as _llm_text, _attach_social_context as _attach_social_context,
    _safe_str_p6 as _safe_str_p6, _attach_npc_mind_context as _attach_npc_mind_context, _NARRATION_MAX_MARKDOWN as _NARRATION_MAX_MARKDOWN, _safe_str as _safe_str,
    _safe_dict as _safe_dict, _safe_list as _safe_list, _title_case_token as _title_case_token, _force_live_llm_required as _force_live_llm_required,
    sanitize_memory_narration_payload as sanitize_memory_narration_payload, _merge_bs1_sanitized_payload as _merge_bs1_sanitized_payload,
    _build_ambient_conversation_line as _build_ambient_conversation_line, _bound_text as _bound_text, _clean_npc_dialogue_line as _clean_npc_dialogue_line,
    _is_accommodation_request as _is_accommodation_request, _has_authoritative_accommodation_offer as _has_authoritative_accommodation_offer,
    _ground_accommodation_npc_line as _ground_accommodation_npc_line, _service_result_from_context as _service_result_from_context,
    _recalled_service_memories_from_context as _recalled_service_memories_from_context,
    _format_recalled_service_memories_for_prompt as _format_recalled_service_memories_for_prompt, _recalled_npc_memories_from_context as _recalled_npc_memories_from_context,
    _format_recalled_npc_memories_for_prompt as _format_recalled_npc_memories_for_prompt, _conversation_result_from_context as _conversation_result_from_context,
    _format_conversation_beat_for_prompt as _format_conversation_beat_for_prompt, _apply_grounded_conversation_beat as _apply_grounded_conversation_beat,
    _line_has_prior_memory_reference as _line_has_prior_memory_reference, _memory_reference_is_backed as _memory_reference_is_backed,
    _strip_unbacked_memory_reference_from_npc_line as _strip_unbacked_memory_reference_from_npc_line, _strip_service_meta_language as _strip_service_meta_language,
    _service_offer_label_with_price as _service_offer_label_with_price, _join_natural as _join_natural, _strip_basic_markdown as _strip_basic_markdown,
    _travel_result_from_context as _travel_result_from_context, _grounded_travel_narration as _grounded_travel_narration, _grounded_travel_action as _grounded_travel_action,
    _final_grounded_service_action_text as _final_grounded_service_action_text, _service_grounded_action_result as _service_grounded_action_result,
    _service_grounded_npc_line as _service_grounded_npc_line, _normalized_text_for_compare as _normalized_text_for_compare,
    _fallback_non_service_narration as _fallback_non_service_narration, _sanitize_repeated_player_input_narration as _sanitize_repeated_player_input_narration,
    _naturalize_service_debug_language as _naturalize_service_debug_language, _service_grounded_narration_text as _service_grounded_narration_text,
    _service_narration_needs_grounding as _service_narration_needs_grounding, _service_claim_needs_grounding as _service_claim_needs_grounding,
    _service_purchase_is_applied as _service_purchase_is_applied, _selected_service_offer as _selected_service_offer,
    _service_extract_price_tokens as _service_extract_price_tokens, _successful_service_purchase_text_needs_grounding as _successful_service_purchase_text_needs_grounding,
    _ground_action_result_text as _ground_action_result_text, _player_input_action_text as _player_input_action_text,
    _build_authoritative_action_line as _build_authoritative_action_line, _build_action_result_line as _build_action_result_line, _build_rewards_block as _build_rewards_block,
    _titleize_action as _titleize_action, _first_nonempty as _first_nonempty, _extract_text_lines as _extract_text_lines, _normalize_speaker_block as _normalize_speaker_block,
    _build_safe_prompt_context as _build_safe_prompt_context, _build_speaker_turns as _build_speaker_turns, _extract_json_object_from_text as _extract_json_object_from_text,
    _normalize_narration_json as _normalize_narration_json, _parse_llm_narration_payload as _parse_llm_narration_payload, _strict_narration_payload as _strict_narration_payload,
    _recent_authoritative_facts as _recent_authoritative_facts, _extract_continuity_price_facts as _extract_continuity_price_facts,
    _extract_present_actor_names as _extract_present_actor_names, _extract_price_tokens as _extract_price_tokens, _sanitize_narration_text as _sanitize_narration_text,
    _authoritative_action_text as _authoritative_action_text, _authoritative_reward_text as _authoritative_reward_text, _allowed_npc_speakers as _allowed_npc_speakers,
    _sanitize_npc_block as _sanitize_npc_block, _desystemify_text as _desystemify_text, _strip_meta_narration as _strip_meta_narration,
    _fallback_in_world_narration as _fallback_in_world_narration, _enforce_npc_behavior as _enforce_npc_behavior, _sanitize_narration_payload as _sanitize_narration_payload,
    _render_narration_text_from_json as _render_narration_text_from_json, _recover_narration_from_raw_text as _recover_narration_from_raw_text,
    _structured_fallback_response as _structured_fallback_response, _build_scene_summary as _build_scene_summary, _build_combat_facts_block as _build_combat_facts_block,
    _pick_npc_reply_text as _pick_npc_reply_text, _build_npc_reply_block as _build_npc_reply_block, _collect_emphasis_markers as _collect_emphasis_markers,
    apply_narration_emphasis as apply_narration_emphasis, build_structured_narration as build_structured_narration,
)


from app.apps.rpg.narration.memory_prompt import (
    build_relevant_memory_context_from_runtime,
    build_relevant_memory_prompt_block,
)
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.ai_world_scene_narrator_prompts.response_length_prompt_rules', "1", (
    "NARRATOR: 2 to 3 short sentence describing the scene.\n"
        "ACTION: 2 to 3 short sentence describing the result of the player's action.\n"
        'NPC: <npc_name>: "2 - 3 short reply" (omit if none)\n'
        "REWARD: <xp/items if any, else omit>"
))
_PROMPT_2 = prompt_template('rpg.ai_world_scene_narrator_prompts.prompt', "1", 'You are a deterministic RPG narration engine.\n\nCONTEXT:\n{v0}\n\nRecent authoritative facts:\n{v1}\n\nAuthoritative combat facts:\n{v2}\n\nTurn contract PRIMARY TRUTH:\n{v3}\n\nCURRENT_TURN_PROMPT_CONTRACT_JSON:\n{v4}\n\nNPC_RESPONSE_ARCHITECTURE_JSON:\n{v5}\n\nCURRENT_TURN_SEMANTIC_VISIBLE_RESPONSE_JSON:\n{v6}\n\n{v7}\n\nNPC STATE SUMMARY (must influence tone and dialogue):\n{v8}\n\nNPC behavior context:\n{v9}\n\nOngoing conversation threads:\n{v10}\n\n{v11}\n\n{v12}\n\nYOUR ONLY TASK: Generate narration for a player\'s action in an RPG.\n\nOUTPUT ONLY VALID JSON.\nDo not include markdown fences.\nDo not include commentary outside JSON.\n{v13}\n\n IMPORTANT RULES:\n - Output ONLY valid JSON with no extra text\n - NO markdown fences or commentary outside the JSON object\n - NO content about ticks, time, or system messages\n - NO faction goals, loyalty, awareness, or ambient content\nTURN CONTRACT RULES:\n- turn_contract is the primary truth for this turn.\n- CURRENT_TURN_PROMPT_CONTRACT_JSON is the presentation boundary for this exact player action.\n- required_focus must be addressed before older context, memories, profile hooks, or recent events.\n- NPC_RESPONSE_ARCHITECTURE_JSON may shape speaker, tone, persona, and continuity only.\n- resolved_result is legacy compatibility; prefer turn_contract when both are present.\n- CURRENT_TURN_SEMANTIC_VISIBLE_RESPONSE_JSON, when present, is current-turn dialogue guidance from the intent/advisory pass. Preserve its speaker, answer intent, and emotional direction unless the authoritative turn_contract forbids NPC dialogue.\n- CURRENT_TURN_SEMANTIC_VISIBLE_RESPONSE_JSON outranks conversation_threads recent_lines, older NPC memories, and prior NPC questions. Use those older records only for continuity after answering this current player input.\n- Do not copy an older NPC question from conversation_threads when the current player input is answering, correcting, or emotionally disclosing to that question.\n- CURRENT_TURN_SEMANTIC_VISIBLE_RESPONSE_JSON is not permission to invent rewards, combat, travel, purchases, inventory changes, or quest progress.\n- Relevant Memory is continuity context only. It may shape tone, recall, and wording after the current turn is satisfied.\n- Relevant Memory never authorizes new rewards, combat, travel, purchases, inventory changes, quest progress, secret disclosure, or relationship changes.\n- Private Relevant Memory may shape NPC tone only; do not reveal private memory directly unless current runtime state or turn_contract exposes it.\n- You MUST base the narration primarily on turn_contract.narration_brief.\n- You MUST reflect turn_contract.state_delta when it exists.\n- You MUST NOT invent state changes outside turn_contract.state_delta, resolved_result, or combat facts.\n- HIGH-RISK CLAIM RULE:\n    You MUST NOT mention rewards, currency, items, XP, inventory, combat, injury, blood, death, location travel, quest completion, objective completion, secret facts, or NPC knowledge unless they are explicitly present in turn_contract, state_delta, resolved_result, or combat facts.\n- If the player makes an unsupported claim such as "you owe me gold", the primary and safe_fallback must refuse or defer the claim unless the turn contract explicitly authorizes payment.\n- The safe_fallback must be conservative and natural. It must never include rewards, combat, injury, blood, travel, quest completion, or hidden facts.\n- The safe_fallback should sound in-character, but it must be safe over dramatic.\n- You may freely add sensory detail, body language, pacing, and natural dialogue as presentation only.\n- NEVER copy or restate narration_brief directly. Convert it into in-world description.\n- NEVER refer to "the player" in narration. Always describe actions in-world (e.g., "You step forward..." or omit subject).\n- NEVER output internal IDs like npc:0, npc_bran, player, target_id, action_type, state_delta, narration_brief, or turn_contract.\n- The final prose must sound like an RPG narrator, not a debug summary.\n- If your output resembles an instruction, rewrite it into a natural in-world description.\n- If narration sounds like a system description, rewrite it before finalizing.\n- Never output generic filler like "Action: You act."\n\nNPC REACTION RULES:\n- If turn_contract.interpreted_action.target_id exists, that NPC MUST visibly react.\n- If npc_behavior_context.required_reaction is true, include either:\n  1. physical/body-language reaction, or\n  2. direct dialogue, preferably both.\n- NPC dialogue must match npc_behavior_context.reaction_tone.\n- hostile/angry NPCs should not respond as friendly.\n- wary NPCs should remain cautious even after an apology.\n- recent_memories MUST influence tone and dialogue.\n- If a memory includes violence or betrayal, NPC should reference or emotionally reflect it.\n- If the player recently harmed an NPC, that NPC should remember it and respond accordingly.\n- NPC dialogue should sound natural, not like a summary of emotions.\n- Avoid phrases like "I am wary" or "I feel cautious".\n- Express emotion through tone, word choice, and implication.\n- Any combat description MUST match the authoritative combat facts block\n- Do NOT invent hits, misses, damage, knockdowns, or combatants\n- The reward field MUST stay empty unless the authoritative context explicitly shows XP, item, or level gain\n- Do NOT invent gold, reputation, items, guards, factions, or bystanders not present in the scene/context\n- NPC speaker MUST be one present actor or the explicit target NPC from context\n- Keep continuity with the recent authoritative facts block below\n- Do NOT change previously established prices, speakers, outcomes, or conflict state unless the current resolved result changed them\n- Do not end the response with an ellipsis\n- Finish with complete sentences\n- Do not leave dialogue, action, or scene description trailing mid-thought\n\nConversation thread rules:\n- If conversation_threads are provided, treat them as ongoing local dialogue context.\n- Do not restart the same NPC line from scratch.\n- Continue from recent_lines when the player\'s input references an ongoing exchange.\n- NPCs may answer, pivot, interrupt, or defer, but must not invent rewards, inventory, combat results, locations, or new NPCs.\n- If a thread has world_signals, phrase them as rumors, tension, suspicions, or social shifts only.\n- Do not resolve or mutate authoritative state unless action_result already says it happened.\n\nRelevant NPC memories from deterministic simulation:\n{v14}\n\nRelevant general NPC memories:\n{v15}\n\nDeterministic NPC-to-NPC conversation beat:\n{v16}\n\nMemory rules:\n- NPCs may reference prior interactions only if they appear in Relevant NPC memories or Relevant general NPC memories.\n- Do not invent prior purchases, debts, failed purchases, promises, favors, or relationships.\n- If Relevant NPC memories is None, do not say "again", "last time", "remember", or imply a previous encounter.\n- If a deterministic NPC-to-NPC conversation beat is provided, use only that speaker and line for the NPC dialogue. Do not invent additional conversation consequences.\n\nSCENE:\nTitle: {v17}\nLocation: {v18}\nTone: {v19}\nTension: {v20}\nSummary: {v21}\nActors present:\n{v22}\nStakes: {v23}\n')
_PROMPT_3 = prompt_template('rpg.ai_world_scene_narrator_prompts.prompt_2', "1", 'You are generating NPC reactions for an RPG.\n\nCharacter: {v0}\n{v1}\n{v2}\n{v3}\n{v4}\n{v5}\n{v6}\n{v7}\n{v8}\n    {v9}\n    {v10}\n    {v11}\n    {v12}\n    {v13}\n    {v14}\n\nScene: {v15}\n\nNarrative:\n{v16}\n\n=== INSTRUCTIONS ===\nDescribe {v17}\'s internal reaction to what just happened.\n- Use the NPC\'s active goals to shape what they want right now.\n- Use belief_summary about the player to determine tone.\n- Use memory_summary to maintain continuity.\n- Use last_decision so reactions align with recent intent.\n- Do not contradict the provided structured state.\nThen provide a short line of dialogue they might say.\nSpecify their emotional state (one of: calm, tense, angry, fearful, curious, excited, neutral).\nSpecify their immediate intent (one of: observe, act, confront, flee, negotiate, wait).\n\nRespond ONLY in JSON format:\n{{\n  "reaction": "...",\n  "dialogue": "...",\n  "emotion": "...",\n  "intent": "..."\n}}\n')
_PROMPT_4 = prompt_template('rpg.ai_world_scene_narrator_prompts.prompt_3', "1", 'You are generating player choices for an RPG scene.\n\nScene: {v0}\nStakes: {v1}\n{v2}\nNarrative situation:\n{v3}\n\n=== INSTRUCTIONS ===\nGenerate exactly {v4} meaningful choices for the player.\nEach choice should have:\n  - A short, action-oriented description (5-10 words)\n  - An implied risk or consequence\n  - A distinct approach (combat, stealth, diplomacy, observation, etc.)\n  - A mapped action type from the available action types above\n\nRespond ONLY in JSON format:\n{{\n  "choices": [\n    {{\n      "text": "...",\n      "type": "action|observe|dialogue|stealth|combat|diplomacy",\n      "action": {{\n        "type": "intervene_thread|escalate_conflict|observe_situation|...",\n        "target_id": "..."\n      }}\n    }}\n  ]\n}}\n')
_PROMPT_5 = prompt_template('rpg.ai_world_scene_narrator_prompts.response_length_prompt_rules_2', "1", (
    "NARRATOR: 5 to 7 sentences describing the scene.\n"
            "ACTION: 5 to 7 sentences describing the result of the player's action.\n"
            'NPC: <npc_name>: "no restrictions on length" (omit if none)\n'
            "REWARD: <xp/items if any, else omit>"
))
_PROMPT_6 = prompt_template('rpg.ai_world_scene_narrator_prompts.response_length_prompt_rules_3', "1", (
    "NARRATOR: 3 to 5 sentences describing the scene.\n"
            "ACTION: 3 to 5 sentences describing the result of the player's action.\n"
            'NPC: <npc_name>: "3 to 5 short sentences" (omit if none)\n'
            "REWARD: <xp/items if any, else omit>"
))
_PROMPT_7 = prompt_template('rpg.ai_world_scene_narrator_prompts.schema', "1", """
Use exactly this object shape:
{
    "format_version": "rpg_narration_candidates_v1",
    "primary": {
        "format_version": "rpg_narration_v2",
        "narration": "<descriptive scene narration grounded in turn_contract>",
        "action": "<short, in-world description of what happened; consequence only, no meta language>",
        "npc": {
            "speaker": "<target NPC name if an allowed/present NPC reacts, otherwise empty string>",
            "line": "<natural in-character dialogue, or empty string only if no NPC reaction is needed>"
        },
        "reward": null,
        "followup_hooks": []
    },
    "safe_fallback": {
        "format_version": "rpg_narration_v2",
        "narration": "<safe conservative narration that refuses or defers unsupported claims>",
        "action": "<safe consequence only; no state changes unless explicitly in turn_contract>",
        "npc": {
            "speaker": "<same allowed speaker as primary when possible>",
            "line": "<safe in-character fallback line; no rewards, no combat, no travel, no quest completion, no hidden facts>"
        },
        "reward": null,
        "followup_hooks": []
    }
}
""")
_PROMPT_8 = prompt_template('rpg.ai_world_scene_narrator_prompts.schema_2', "1", """
Use exactly this object shape:
{
    "format_version": "rpg_narration_v2",
    "narration": "<descriptive scene narration grounded in turn_contract>",
    "action": "<short, in-world description of what happened; consequence only, no meta language>",
    "npc": {
        "speaker": "<target NPC name if an allowed/present NPC reacts, otherwise empty string>",
        "line": "<natural in-character dialogue, or empty string only if no NPC reaction is needed>"
    },
    "reward": null,
    "followup_hooks": []
}
""")
_PROMPT_9 = prompt_template('rpg.ai_world_scene_narrator_prompts.hooks_text', "1", '\nAvailable action types:\n  - intervene_thread: target={v0}\n  - escalate_conflict: target={v1}\n  - observe_situation: target={v2}\n')


@dataclass
class NPCReaction:
    """An NPC's reaction to a scene event."""

    npc_id: str = ""
    npc_name: str = ""
    reaction: str = ""
    dialogue: str = ""
    emotion: str = "neutral"
    intent: str = ""


@dataclass
class NarrativeResult:
    """Complete result from scene narration."""

    narrative: str
    choices: List[Dict[str, Any]] = field(default_factory=list)
    npc_reactions: List[NPCReaction] = field(default_factory=list)
    dialogue_blocks: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _normalize_response_length(value: Any) -> str:
    value = str(value or "").strip().lower()
    if value in ("short", "medium", "long"):
        return value
    return "short"


def _response_length_prompt_rules(response_length: str) -> str:
    response_length = _normalize_response_length(response_length)

    if response_length == "long":
        return (
            _PROMPT_5.text
        )

    if response_length == "medium":
        return (
            _PROMPT_6.text
        )

    return (
        _PROMPT_1.text
    )


def _current_turn_semantic_visible_response(
    narration_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Return visible-response guidance bound to the current turn only."""
    narration_context = _safe_dict(narration_context)
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    resolved = _safe_dict(narration_context.get("resolved_result"))
    contract_resolved = _safe_dict(
        turn_contract.get("resolved_result") or turn_contract.get("resolved_action")
    )
    action = _safe_dict(turn_contract.get("action") or narration_context.get("action"))
    action_metadata = _safe_dict(action.get("metadata"))
    semantic_action = _safe_dict(turn_contract.get("semantic_action"))
    metadata_semantic_action = _safe_dict(action_metadata.get("semantic_action"))
    resolved_semantic_action = _safe_dict(
        resolved.get("semantic_action") or contract_resolved.get("semantic_action")
    )

    candidates = (
        (
            "turn_contract.current_turn_visible_response",
            turn_contract.get("current_turn_visible_response"),
        ),
        (
            "turn_contract.semantic_visible_response",
            turn_contract.get("semantic_visible_response"),
        ),
        (
            "turn_contract.semantic_action.visible_response",
            semantic_action.get("visible_response"),
        ),
        (
            "turn_contract.semantic_action.semantic_visible_response",
            semantic_action.get("semantic_visible_response"),
        ),
        ("resolved_result.visible_response", resolved.get("visible_response")),
        (
            "turn_contract.resolved_result.visible_response",
            contract_resolved.get("visible_response"),
        ),
        (
            "resolved_result.semantic_action.visible_response",
            resolved_semantic_action.get("visible_response"),
        ),
        (
            "action.metadata.semantic_action.visible_response",
            metadata_semantic_action.get("visible_response"),
        ),
        (
            "action.metadata.semantic_action.semantic_visible_response",
            metadata_semantic_action.get("semantic_visible_response"),
        ),
        (
            "action.metadata.visible_response_if_no_runtime_needed",
            action_metadata.get("visible_response_if_no_runtime_needed"),
        ),
    )

    for source, value in candidates:
        visible = _safe_dict(value)
        if not visible:
            continue
        npc = _safe_dict(visible.get("npc"))
        narration = _safe_str(visible.get("narration") or visible.get("text")).strip()
        npc_line = _safe_str(npc.get("line") or npc.get("text")).strip()
        npc_speaker = _safe_str(npc.get("speaker") or npc.get("name")).strip()
        if not narration and not npc_line:
            continue
        return {
            "source": source,
            "narration": narration[:600],
            "npc": {
                "speaker": npc_speaker[:120],
                "line": npc_line[:600],
            },
        }
    return {}


def _compact_prompt_json(value: Any, max_chars: int, fallback: Any = None) -> str:
    if fallback is None:
        fallback = {}
    try:
        text = json.dumps(
            value if value is not None else fallback,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        text = _safe_str(value)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "...[truncated]"
    return text


def _compact_prompt_text(value: Any, max_chars: int) -> str:
    text = " ".join(_safe_str(value).split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "...[truncated]"
    return text


def build_scene_prompt(scene, narration_context, tone="dramatic"):
    """Build an LLM prompt to narrate a scene with strict structured output format.

    Returns:
        Prompt string for the LLM.
    """
    # ✅ Apply scene grounding FIRST before any prompt construction
    from app.apps.rpg.narration.scene_grounding import (
        _apply_grounded_scene_overlay as _apply_grounded_scene_overlay,
        _derive_grounded_scene_context as _derive_grounded_scene_context,
        _normalize_prompt_location_name as _normalize_prompt_location_name,
    )

    simulation_state = narration_context.get("simulation_state") or {}
    runtime_state = narration_context.get("runtime_state") or {}
    turn_result = narration_context.get("resolved_result") or {}

    grounded = _derive_grounded_scene_context(
        simulation_state, runtime_state, turn_result
    )
    scene = _apply_grounded_scene_overlay(scene, grounded)

    # ✅ Get final values from authoritative grounded state
    title = (
        _safe_str(scene.get("title") or grounded.get("scene_title")).strip()
        or "Current Scene"
    )
    summary = _safe_str(scene.get("summary") or grounded.get("scene_summary")).strip()

    # ✅ Normalize actors: convert dicts to names, always have safe fallback
    raw_actors = _safe_list(scene.get("actors") or grounded.get("present_actor_names"))
    actors = []
    for a in raw_actors:
        if isinstance(a, dict):
            actors.append(_safe_str(a.get("name") or a.get("id") or "Unknown"))
        else:
            actors.append(_safe_str(a))
    actors = [a for a in actors if _safe_str(a).strip()][:10]

    # ✅ Hard fallback: Actors present is never empty
    if not actors:
        actors = ["Other people nearby"]

    raw_location = _safe_str(
        scene.get("location_name") or turn_result.get("location_name")
    ).strip()
    location = (
        _normalize_prompt_location_name(
            raw_location, _safe_str(grounded.get("location_name"))
        )
        or "Current Location"
    )
    stakes = scene.get("stakes", "much is at stake")
    tension = scene.get("tension", "moderate")

    actor_list = ""
    if actors:
        if isinstance(actors, list):
            actor_list = "\n".join(f"  - {a}" for a in actors)
        elif isinstance(actors, dict):
            actor_list = "\n".join(f"  - {k}: {v}" for k, v in actors.items())
        else:
            actor_list = str(actors)

    safe_context = _build_safe_prompt_context(scene, narration_context)

    # Build conversation threads context
    conversation_threads = _safe_list(narration_context.get("conversation_threads"))
    conversation_threads_block = ""
    if conversation_threads:
        lines = ["ONGOING CONVERSATION THREADS:"]
        for thread in conversation_threads[:2]:
            thread = _safe_dict(thread)
            topic = _safe_dict(thread.get("topic"))
            lines.append(
                f"- {_safe_str(thread.get('thread_id'))} | participants={', '.join(_safe_str(p) for p in _safe_list(thread.get('participants'))[:4])} | topic={_compact_prompt_text(topic.get('summary'), 160)}"
            )
            for line in _safe_list(thread.get("recent_lines"))[-2:]:
                line = _safe_dict(line)
                lines.append(
                    f"  {_safe_str(line.get('speaker_name') or line.get('speaker_id'))}: {_compact_prompt_text(line.get('text'), 160)}"
                )
        conversation_threads_block = "\n".join(lines)
    else:
        conversation_threads_block = "none"

    recent_authoritative_facts = _recent_authoritative_facts(narration_context)
    recent_facts_block = (
        "\n".join(f"- {fact}" for fact in recent_authoritative_facts[:3]) or "- none"
    )
    combat_facts_block = _build_combat_facts_block(narration_context)
    current_turn_prompt_contract = build_runtime_current_turn_prompt_contract(
        scene=scene,
        narration_context=narration_context,
    )
    current_turn_visible_response = _current_turn_semantic_visible_response(
        narration_context
    )
    compact_turn = _safe_dict(current_turn_prompt_contract.get("turn_contract"))
    compact_interpreted = _safe_dict(compact_turn.get("interpreted_action"))
    visible_npc = _safe_dict(current_turn_visible_response.get("npc"))
    relevant_memory_context = build_relevant_memory_context_from_runtime(
        runtime_state,
        player_input=narration_context.get("player_input")
        or narration_context.get("player_action")
        or current_turn_prompt_contract.get("player_action"),
        actor_ids=[
            compact_interpreted.get("target_id"),
            visible_npc.get("speaker"),
        ],
        location_id=grounded.get("location_id") or scene.get("location_id"),
    )
    npc_response_architecture = build_runtime_npc_response_architecture(
        narration_context=narration_context,
        current_turn_prompt_contract=current_turn_prompt_contract,
    )
    relevant_memory_block = build_relevant_memory_prompt_block(relevant_memory_context)
    memory_grounding_block = memory_narration_prompt_block(
        {
            **_safe_dict(narration_context),
            "scene": scene,
            "relevant_memory": relevant_memory_context,
        }
    )
    runtime_guardrails_block = build_runtime_presentation_guardrails_block(
        narration_context
    )
    npc_behavior_context = _safe_dict(
        narration_context.get("npc_behavior_context")
        or _safe_dict(narration_context.get("turn_contract")).get(
            "npc_behavior_context"
        )
    )
    npc_state_summary = {
        "mood": npc_behavior_context.get("mood"),
        "relationship": npc_behavior_context.get("relationship_to_player"),
        "trust": npc_behavior_context.get("trust"),
        "fear": npc_behavior_context.get("fear"),
        "recent_memories": _safe_list(npc_behavior_context.get("recent_memories"))[:4],
    }
    safe_context_block = _compact_prompt_json(safe_context, 1400)
    turn_contract_block = _compact_prompt_json(
        _safe_dict(narration_context.get("turn_contract")), 5200
    )
    current_turn_contract_block = _compact_prompt_json(
        current_turn_prompt_contract, 3600
    )
    npc_response_architecture_block = _compact_prompt_json(
        npc_response_architecture, 3200
    )
    current_turn_visible_response_block = _compact_prompt_json(
        current_turn_visible_response or {"present": False}, 1200
    )
    npc_state_summary_block = _compact_prompt_json(npc_state_summary, 1200)
    npc_behavior_context_block = _compact_prompt_json(npc_behavior_context, 1800)

    grounding_settings = normalize_grounding_settings(
        _safe_dict(
            _safe_dict(narration_context.get("runtime_settings")).get("grounding")
        )
        or _safe_dict(_safe_dict(narration_context.get("settings")).get("grounding"))
    )
    use_safe_fallback_candidate = bool(
        grounding_settings.get("llm_safe_fallback_candidate", True)
    )

    if use_safe_fallback_candidate:
        schema = _PROMPT_7.text
    else:
        schema = _PROMPT_8.text

    prompt = _PROMPT_2.format(v0=(safe_context_block), v1=(recent_facts_block), v2=(combat_facts_block), v3=(turn_contract_block), v4=(current_turn_contract_block), v5=(npc_response_architecture_block), v6=(current_turn_visible_response_block), v7=(runtime_guardrails_block), v8=(npc_state_summary_block), v9=(npc_behavior_context_block), v10=(conversation_threads_block), v11=(relevant_memory_block), v12=(memory_grounding_block), v13=(schema), v14=(_format_recalled_service_memories_for_prompt(narration_context)), v15=(_format_recalled_npc_memories_for_prompt(narration_context)), v16=(_format_conversation_beat_for_prompt(narration_context)), v17=(title), v18=(location), v19=(tone), v20=(tension), v21=(summary), v22=(actor_list), v23=(stakes))
    logger.debug("[RPG PROMPT] Final prompt length: %d", len(prompt))
    return append_survival_grounding_to_prompt(
        prompt,
        _safe_dict(narration_context),
    )


def build_npc_reaction_prompt(
    npc: Dict[str, Any],
    scene: Dict[str, Any],
    narrative: str,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a prompt to generate an individual NPC reaction.

    Args:
        npc: NPC dict with name, personality, goals, memory, relationships, etc.
        scene: Current scene dict.
        narrative: The generated narrative text.
        state: Optional game state dict.

    Returns:
        Prompt string for the LLM.
    """
    npc_name = npc.get("name", "Unknown NPC")
    npc_personality = npc.get("personality", "")
    npc_goals = npc.get("goals", "")
    npc_relation = npc.get("relation_to_player", "neutral")
    scene_title = scene.get("title", "Unknown Scene")

    # Phase 5.1: Inject NPC state (memory, beliefs, relationships)
    # Phase 6: Enhanced with deterministic mind context
    npc_memory = npc.get("memory_summary", "")
    npc_beliefs = npc.get("beliefs", npc.get("belief_summary", {}))
    npc_relationships = npc.get("relationships", {})
    npc_active_goals = npc.get("active_goals", [])
    npc_last_decision = npc.get("last_decision", {})

    personality_info = f"Personality: {npc_personality}" if npc_personality else ""
    goals_info = f"Goals: {npc_goals}" if npc_goals else ""
    relation_info = f"Relation to player: {npc_relation}" if npc_relation else ""
    memory_info = f"Recent memory: {npc_memory}" if npc_memory else ""
    beliefs_info = (
        f"Current beliefs: {', '.join(str(v) for v in npc_beliefs.values())}"
        if npc_beliefs
        else ""
    )
    relationships_info = (
        f"Relationships: {npc_relationships}" if npc_relationships else ""
    )
    rumor_info = (
        f"Rumors in circulation: {scene.get('active_rumors', [])}"
        if scene.get("active_rumors")
        else ""
    )
    alliance_info = (
        f"Active alliances: {scene.get('active_alliances', [])}"
        if scene.get("active_alliances")
        else ""
    )
    faction_position_info = (
        f"Faction positions: {scene.get('faction_positions', {})}"
        if scene.get("faction_positions")
        else ""
    )
    # Phase 8.3: Add sandbox context to scene prompt
    sandbox_info = (
        f"Sandbox summary: {scene.get('sandbox_summary', {})}"
        if scene.get("sandbox_summary")
        else ""
    )
    world_consequence_info = (
        f"Recent world consequences: {scene.get('world_consequences', [])}"
        if scene.get("world_consequences")
        else ""
    )
    goals_list_info = f"Active goals: {npc_active_goals}" if npc_active_goals else ""
    last_decision_info = (
        f"Last decision: {npc_last_decision}" if npc_last_decision else ""
    )
    # Phase 7: Add debug context info for explainability
    debug_context_info = (
        f"Scene debug context: {scene.get('debug_context', {})}"
        if scene.get("debug_context")
        else ""
    )

    prompt = _PROMPT_3.format(v0=(npc_name), v1=(personality_info), v2=(goals_info), v3=(relation_info), v4=(memory_info), v5=(beliefs_info), v6=(relationships_info), v7=(rumor_info), v8=(alliance_info), v9=(faction_position_info), v10=(sandbox_info), v11=(world_consequence_info), v12=(goals_list_info), v13=(last_decision_info), v14=(debug_context_info), v15=(scene_title), v16=(narrative[:1000]), v17=(npc_name))
    return prompt


def build_choice_prompt(
    scene: Dict[str, Any],
    narrative: str,
    *,
    num_choices: int = 3,
    action_hooks: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a prompt to generate player choices.

    Args:
        scene: Current scene dict.
        narrative: The narrative text.
        num_choices: Number of choices to generate.
        action_hooks: Optional list of action hooks from the scene.

    Returns:
        Prompt string for the LLM.
    """
    title = scene.get("title", "Scene")
    stakes = scene.get("stakes", "")
    source = scene.get("id", scene.get("source", ""))

    # Phase 5.1: Build action hooks for choice → action binding
    hooks_text = ""
    if action_hooks:
        hooks_text = "\nAvailable action types:\n"
        for hook in action_hooks:
            hooks_text += f"  - {hook.get('type', 'unknown')}: target={hook.get('target_id', source)}\n"
    else:
        # Default action hooks
        hooks_text = _PROMPT_9.format(v0=(source), v1=(source), v2=(source))

    prompt = _PROMPT_4.format(v0=(title), v1=(stakes), v2=(hooks_text), v3=(narrative[-500:]), v4=(num_choices))
    return prompt


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def parse_scene_response(text: str) -> Dict[str, Any]:
    """Parse a raw LLM narrative response.

    Returns raw parsed fields only.
    Parses structured output format directly from LLM response.

    Handles both:
    - JSON format: {"format_version": "...", "narration": "...", "action": "...", ...}
    - Text format: NARRATOR: ...\nACTION: ...\nNPC: ...
    """
    logger.debug("[RPG PARSE] Starting to parse response, length: %d", len(text))

    result: dict[str, Any] = {
        "narrator": "",
        "action": "",
        "npc": {
            "speaker_id": "",
            "name": "",
            "text": "",
            "emotion": "",
            "portrait": "",
        },
        "reward": "",
    }

    # Clean up the text
    text = _safe_str(text).strip()
    logger.debug(
        "[RPG PARSE] Cleaned text: %r", text[:200] + "..." if len(text) > 200 else text
    )

    # Try JSON format first
    if text.startswith("{"):
        try:
            import json

            parsed_json = json.loads(text)
            if isinstance(parsed_json, dict):
                # Map JSON fields to result fields
                result["narrator"] = _safe_str(parsed_json.get("narration")).strip()
                result["action"] = _safe_str(parsed_json.get("action")).strip()

                npc = parsed_json.get("npc")
                if isinstance(npc, dict):
                    result["npc"] = {
                        "speaker_id": _safe_str(npc.get("speaker"))
                        .strip()
                        .replace(" ", "_")
                        .lower(),
                        "name": _safe_str(npc.get("speaker")).strip(),
                        "text": _bound_text(npc.get("line"), 180),
                        "emotion": "",
                        "portrait": "",
                    }

                result["reward"] = ""

                logger.debug(
                    "[RPG PARSE] Parsed JSON format: narrator=%r, action=%r, npc_text=%r",
                    result["narrator"][:50],
                    result["action"][:50],
                    result["npc"]["text"][:50],
                )
                return result
        except Exception:
            logger.debug(
                "[RPG PARSE] JSON parsing failed, falling back to text parsing"
            )

    import re  # noqa: F811 - this function uses a local parser import

    # Look for patterns anywhere in the text
    # NARRATOR pattern
    narrator_match = re.search(
        r"NARRATOR:\s*(.+?)(?=\n[A-Z]+:|\n*$)", text, re.DOTALL | re.IGNORECASE
    )
    if narrator_match:
        result["narrator"] = narrator_match.group(1).strip()
        logger.debug("[RPG PARSE] Found NARRATOR: %r", result["narrator"])

    # ACTION pattern
    action_match = re.search(
        r"ACTION:\s*(.+?)(?=\n[A-Z]+:|\n*$)", text, re.DOTALL | re.IGNORECASE
    )
    if action_match:
        result["action"] = action_match.group(1).strip()
        logger.debug("[RPG PARSE] Found ACTION: %r", result["action"])

    # NPC pattern
    npc_match = re.search(
        r"NPC:\s*(.+?)(?=\n[A-Z]+:|\n*$)", text, re.DOTALL | re.IGNORECASE
    )
    if npc_match:
        npc_text = npc_match.group(1).strip()
        logger.debug("[RPG PARSE] Found NPC text: %r", npc_text)
        if ":" in npc_text:
            name, text_part = npc_text.split(":", 1)
            npc_name = name.strip()
            result["npc"] = {
                "speaker_id": npc_name.lower().replace(" ", "_"),
                "name": npc_name,
                "text": _bound_text(text_part.strip().strip('"'), 180),
                "emotion": "",
                "portrait": "",
            }
            logger.debug(
                "[RPG PARSE] Parsed NPC: name=%r, text=%r",
                npc_name,
                result["npc"]["text"],
            )
        else:
            result["npc"] = {
                "speaker_id": "",
                "name": "",
                "text": _bound_text(npc_text, 180),
                "emotion": "",
                "portrait": "",
            }
            logger.debug(
                "[RPG PARSE] Parsed NPC without name: text=%r", result["npc"]["text"]
            )

    # REWARD pattern
    reward_match = re.search(
        r"REWARD:\s*(.+?)(?=\n[A-Z]+:|\n*$)", text, re.DOTALL | re.IGNORECASE
    )
    if reward_match:
        result["reward"] = _bound_text(reward_match.group(1).strip(), 120)
        logger.debug("[RPG PARSE] Found REWARD: %r", result["reward"])

    # Fallback: if no structured format found, try to extract from plain text
    if not result["narrator"] and not result["action"]:
        lines = text.split("\n")
        logger.debug(
            "[RPG PARSE] No structured format found, using fallback with %d lines",
            len(lines),
        )
        if lines:
            # Assume first line is narrator
            result["narrator"] = lines[0].strip()
            logger.debug("[RPG PARSE] Fallback NARRATOR: %r", result["narrator"])
        if len(lines) > 1:
            # Assume second line is action
            result["action"] = lines[1].strip()
            logger.debug("[RPG PARSE] Fallback ACTION: %r", result["action"])

    logger.debug("[RPG PARSE] Final parsed result: %s", result)
    return result


def _is_valid_scene_response(parsed: Dict[str, Any]) -> bool:
    parsed = _safe_dict(parsed)
    narrator = _safe_str(parsed.get("narrator")).strip()
    action = _safe_str(parsed.get("action")).strip()
    npc_text = _safe_str(parsed.get("npc", {}).get("text")).strip()

    blob = (narrator + "\n" + action + "\n" + npc_text).strip()
    if not blob:
        is_valid = False
    elif len(blob) <= 10:
        is_valid = False
    elif blob.startswith("{") or '"format_version"' in blob or '"narration"' in blob:
        # Do not treat leaked JSON / partial JSON as valid rendered prose.
        is_valid = False
    else:
        is_valid = True

    logger.warning(
        "[RPG VALIDATE] narrator=%r, action=%r, npc_text=%r -> valid=%s",
        narrator[:50],
        action[:50],
        npc_text[:50],
        is_valid,
    )
    return is_valid


def _with_scene_response_defaults(parsed: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _safe_dict(parsed)

    npc = parsed.get("npc")
    if not isinstance(npc, dict):
        parsed["npc"] = {
            "speaker_id": "unknown",
            "name": "",
            "text": _safe_str(npc).strip(),
            "emotion": "",
            "portrait": "",
        }
    if not parsed.get("narrator"):
        parsed["narrator"] = "You are here."

    return parsed


def parse_npc_reaction(text: str, npc_id: str = "", npc_name: str = "") -> NPCReaction:
    """Parse an NPC reaction response.

    Phase 5.1: Attempts JSON parsing first, falls back to text extraction.

    Args:
        text: Raw LLM response for NPC reaction.
        npc_id: NPC identifier.
        npc_name: Fallback NPC name.

    Returns:
        NPCReaction dataclass instance.
    """
    # Phase 5.1: Try JSON parsing first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return NPCReaction(
                npc_id=npc_id,
                npc_name=npc_name,
                reaction=data.get("reaction", ""),
                dialogue=data.get("dialogue", ""),
                emotion=data.get("emotion", "neutral").lower(),
                intent=data.get("intent", ""),
            )
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: text extraction
    reaction = ""
    dialogue = ""
    emotion = "neutral"
    intent = ""

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("REACTION:"):
            reaction = line[len("REACTION:") :].strip()
        elif line.startswith("DIALOGUE:"):
            dialogue = line[len("DIALOGUE:") :].strip().strip('"')
        elif line.startswith("EMOTION:"):
            emotion = line[len("EMOTION:") :].strip().lower()
        elif line.startswith("INTENT:"):
            intent = line[len("INTENT:") :].strip().lower()

    return NPCReaction(
        npc_id=npc_id,
        npc_name=npc_name,
        reaction=reaction,
        dialogue=dialogue,
        emotion=emotion,
        intent=intent,
    )


def parse_choices(text: str, source: str = "") -> List[Dict[str, Any]]:
    """Parse LLM-generated player choices.

    Phase 5.1: Attempts JSON parsing first, falls back to text extraction.
    Choices now include action binding for integration with apply_player_action.

    Args:
        text: Raw LLM response with numbered choices.
        source: Scene/source ID for action target binding.

    Returns:
        List of choice dicts with 'id', 'text', 'type', and 'action' keys.
    """
    # Phase 5.1: Try JSON parsing first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "choices" in data:
            choices_data = data["choices"]
        elif isinstance(data, list):
            choices_data = data
        else:
            choices_data = []

        if choices_data:
            choices = []
            for i, c in enumerate(choices_data):
                if isinstance(c, dict):
                    action = c.get("action", {})
                    choices.append(
                        {
                            "id": f"choice_{i + 1}",
                            "text": c.get("text", ""),
                            "type": c.get("type", "action"),
                            "action": {
                                "type": action.get("type", "intervene_thread"),
                                "target_id": action.get("target_id", source),
                            },
                        }
                    )
            if choices:
                return choices
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: text extraction with default action binding
    choices = []
    choice_types = ["action", "observe", "dialogue", "stealth", "combat", "diplomacy"]
    action_types = ["intervene_thread", "observe_situation", "escalate_conflict"]

    for line in text.split("\n"):
        line = line.strip()
        if line and (line[0].isdigit() and line[1] in (".", ")")):
            choice_text = line[2:].strip()
            idx = len(choices) + 1
            choice_type = choice_types[idx % len(choice_types)]
            action_type = action_types[idx % len(action_types)]
            choices.append(
                {
                    "id": f"choice_{idx}",
                    "text": choice_text,
                    "type": choice_type,
                    "action": {
                        "type": action_type,
                        "target_id": source,
                    },
                }
            )

    return (
        choices
        if choices
        else [
            {
                "id": "choice_1",
                "text": "Take action",
                "type": "action",
                "action": {"type": "intervene_thread", "target_id": source},
            },
            {
                "id": "choice_2",
                "text": "Wait and observe",
                "type": "observe",
                "action": {"type": "observe_situation", "target_id": source},
            },
        ]
    )


def apply_hooks_to_choices(
    choices: List[Dict[str, Any]],
    hooks: List[Dict[str, Any]],
    *,
    source: str = "",
) -> List[Dict[str, Any]]:
    """Inject action hooks into choices for binding.

    Phase 5.5: Maps scene action_hooks onto choice objects so that
    when a player selects a choice, the corresponding action is ready.

    Args:
        choices: List of choice dicts to update in-place.
        hooks: List of action hooks from the scene (e.g. from action_hooks).
        source: Fallback target_id when a hook has none.

    Returns:
        The same choices list, updated with action bindings.
    """
    for i, c in enumerate(choices):
        if i < len(hooks):
            hook = hooks[i]
            c["action"] = {
                "type": hook.get("type", "intervene_thread"),
                "target_id": hook.get("target_id", source),
            }
    return choices


# ---------------------------------------------------------------------------
# Scene narration service
# ---------------------------------------------------------------------------

__all__ = ('annotations', 'memory_narration_prompt_block', 'append_survival_grounding_to_prompt', 'json', 'logging', 're', 'traceback', 'dataclass', 'field', 'Any', 'Callable', 'Dict', 'List', 'Optional', 'normalize_grounding_settings', 'select_grounded_narration_candidate', 'memory_reference_is_backed', 'build_runtime_npc_response_architecture', 'build_runtime_current_turn_prompt_contract', 'format_runtime_prompt_contract_block', 'build_runtime_presentation_guardrails_block', 'sanitize_unsupported_combat_payload', 'parse_runtime_provider_payload', 'build_encounter_view', 'logger', '_ACTIVE_NARRATIONS', 'NARRATION_JSON_FORMAT_VERSION', 'NARRATION_JSON_SCHEMA_HINT', '_extract_llm_text', '_llm_text', '_attach_social_context', '_safe_str_p6', '_attach_npc_mind_context', '_NARRATION_MAX_MARKDOWN', '_safe_str', '_safe_dict', '_safe_list', '_title_case_token', '_force_live_llm_required', 'sanitize_memory_narration_payload', '_merge_bs1_sanitized_payload', '_build_ambient_conversation_line', '_bound_text', '_clean_npc_dialogue_line', '_is_accommodation_request', '_has_authoritative_accommodation_offer', '_ground_accommodation_npc_line', '_service_result_from_context', '_recalled_service_memories_from_context', '_format_recalled_service_memories_for_prompt', '_recalled_npc_memories_from_context', '_format_recalled_npc_memories_for_prompt', '_conversation_result_from_context', '_format_conversation_beat_for_prompt', '_apply_grounded_conversation_beat', '_line_has_prior_memory_reference', '_memory_reference_is_backed', '_strip_unbacked_memory_reference_from_npc_line', '_strip_service_meta_language', '_service_offer_label_with_price', '_join_natural', '_strip_basic_markdown', '_travel_result_from_context', '_grounded_travel_narration', '_grounded_travel_action', '_final_grounded_service_action_text', '_service_grounded_action_result', '_service_grounded_npc_line', '_normalized_text_for_compare', '_fallback_non_service_narration', '_sanitize_repeated_player_input_narration', '_naturalize_service_debug_language', '_service_grounded_narration_text', '_service_narration_needs_grounding', '_service_claim_needs_grounding', '_service_purchase_is_applied', '_selected_service_offer', '_service_extract_price_tokens', '_successful_service_purchase_text_needs_grounding', '_ground_action_result_text', '_player_input_action_text', '_build_authoritative_action_line', '_build_action_result_line', '_build_rewards_block', '_titleize_action', '_first_nonempty', '_extract_text_lines', '_normalize_speaker_block', '_build_safe_prompt_context', '_build_speaker_turns', '_extract_json_object_from_text', '_normalize_narration_json', '_parse_llm_narration_payload', '_strict_narration_payload', '_recent_authoritative_facts', '_extract_continuity_price_facts', '_extract_present_actor_names', '_extract_price_tokens', '_sanitize_narration_text', '_authoritative_action_text', '_authoritative_reward_text', '_allowed_npc_speakers', '_sanitize_npc_block', '_desystemify_text', '_strip_meta_narration', '_fallback_in_world_narration', '_enforce_npc_behavior', '_sanitize_narration_payload', '_render_narration_text_from_json', '_recover_narration_from_raw_text', '_structured_fallback_response', '_build_scene_summary', '_build_combat_facts_block', '_pick_npc_reply_text', '_build_npc_reply_block', '_collect_emphasis_markers', 'apply_narration_emphasis', 'build_structured_narration', 'build_relevant_memory_context_from_runtime', 'build_relevant_memory_prompt_block', 'NPCReaction', 'NarrativeResult', '_normalize_response_length', '_response_length_prompt_rules', '_current_turn_semantic_visible_response', '_compact_prompt_json', '_compact_prompt_text', 'build_scene_prompt', 'build_npc_reaction_prompt', 'build_choice_prompt', 'parse_scene_response', '_is_valid_scene_response', '_with_scene_response_defaults', 'parse_npc_reaction', 'parse_choices', 'apply_hooks_to_choices')
