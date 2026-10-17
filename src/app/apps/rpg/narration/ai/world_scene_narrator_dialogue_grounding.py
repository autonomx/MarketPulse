"""Split helpers for RPG world scene narration."""

from __future__ import annotations

from app.apps.rpg.narration.ai.world_scene_narrator_common import (
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
)


def _build_ambient_conversation_line(narration_context: Dict[str, Any]) -> str:
    narration_context = _safe_dict(narration_context)
    beat = _safe_dict(narration_context.get("beat"))

    speaker_id = _safe_str(beat.get("speaker_id")).strip() or "someone"
    speaker = _title_case_token(speaker_id) or "Someone"

    summary = _safe_str(beat.get("summary")).strip()
    stance = _safe_str(beat.get("stance")).strip().lower()
    addressed_to = [
        _title_case_token(x)
        for x in _safe_list(beat.get("addressed_to"))
        if _safe_str(x).strip()
    ]
    mentions = [
        _title_case_token(x)
        for x in _safe_list(beat.get("mentions"))
        if _safe_str(x).strip()
    ]

    summary = summary.rstrip(".!? ").strip()
    if not summary:
        summary = "says something under their breath"

    prefix = f"{speaker}: "
    if stance in {"warning", "cautious", "worried"}:
        prefix = f"{speaker} lowers their voice. "
    elif stance in {"challenge", "angry", "threat"}:
        prefix = f"{speaker} snaps back. "
    elif stance in {"friendly", "warm", "supportive"}:
        prefix = f"{speaker} says warmly, "
    elif stance in {"secretive", "whisper", "hushed"}:
        prefix = f"{speaker} whispers, "

    # Address target naturally if present.
    if addressed_to:
        if len(addressed_to) == 1:
            target_phrase = f" to {addressed_to[0]}"
        else:
            target_phrase = f" to {', '.join(addressed_to[:2])}"
    else:
        target_phrase = ""

    line = prefix
    if prefix.endswith(": "):
        line = f"{speaker}{target_phrase}: {summary}"
    else:
        line = f"{prefix}{summary}"

    # Soft mention enrichment, bounded and presentation-only.
    if mentions:
        mention = mentions[0]
        summary_lower = summary.lower()
        if mention.lower() not in summary_lower:
            line = f"{line} ({mention})"

    return line.strip()


def _bound_text(value: Any, limit: int = 180) -> str:
    text = _safe_str(value).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _clean_npc_dialogue_line(value: Any) -> str:
    """Clean NPC dialogue without presentation truncation."""
    text = _safe_str(value).strip()
    if not text:
        return ""

    text = text.strip().strip('"').strip("'").strip()
    if text.startswith("{") or text.startswith("["):
        return ""

    # Hard safety cap only. Do not insert ellipses into normal dialogue.
    max_chars = 1200
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        if text and not text.endswith((".", "!", "?", '"', "'")):
            text += "."

    return text


def _is_accommodation_request(narration_context: Dict[str, Any]) -> bool:
    narration_context = _safe_dict(narration_context)
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    semantic_action = _safe_dict(turn_contract.get("semantic_action"))
    action = _safe_dict(turn_contract.get("action"))
    metadata = _safe_dict(action.get("metadata"))
    nested_semantic = _safe_dict(metadata.get("semantic_action"))

    haystack = " ".join(
        [
            _safe_str(semantic_action.get("activity_label")),
            _safe_str(semantic_action.get("reason")),
            _safe_str(semantic_action.get("action_type")),
            _safe_str(nested_semantic.get("activity_label")),
            _safe_str(nested_semantic.get("reason")),
            _safe_str(nested_semantic.get("action_type")),
        ]
    ).lower()

    return any(
        token in haystack
        for token in (
            "room",
            "rent",
            "accommodation",
            "lodging",
            "inn",
            "request_accommodation",
            "asking_to_rent",
        )
    )


def _has_authoritative_accommodation_offer(narration_context: Dict[str, Any]) -> bool:
    narration_context = _safe_dict(narration_context)
    resolved = _safe_dict(narration_context.get("resolved_result"))
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    resolved_from_contract = _safe_dict(
        turn_contract.get("resolved_result") or turn_contract.get("resolved_action")
    )

    for source in (resolved, resolved_from_contract):
        action_metadata = _safe_dict(source.get("action_metadata"))
        effect_result = _safe_dict(source.get("effect_result"))
        service_effects = _safe_dict(effect_result.get("service_effects"))

        if _safe_str(action_metadata.get("transaction_kind")):
            return True
        if _safe_str(action_metadata.get("price_source")):
            return True
        if service_effects:
            return True
        if _safe_str(
            source.get("service_id") or source.get("room_id") or source.get("offer_id")
        ):
            return True

    return False


def _ground_accommodation_npc_line(line: str, narration_context: Dict[str, Any]) -> str:
    if not _is_accommodation_request(narration_context):
        return line

    if _has_authoritative_accommodation_offer(narration_context):
        return line

    lower = _safe_str(line).lower()
    invented_terms = (
        # Availability / offer claims
        "vacant room",
        "vacant rooms",
        "available room",
        "available rooms",
        "room available",
        "rooms available",
        "we do have",
        "i do have",
        "i've got",
        "ive got",
        "we've got",
        "we have a room",
        "i have a room",
        "got a room",
        "got a cozy",
        "cozy little room",
        "vacancies",
        "vacancy",
        "no vacancies",
        "haven't had any vacancies",
        "havent had any vacancies",
        "might have somethin",
        "might have something",
        "something for you",
        "somethin' for you",
        # Scene movement / transition claims
        "follow me",
        "come with me",
        "let me show you",
        "show you the room",
        # Specific room/location facts
        "top floor",
        "above the inn",
        "down the hall",
        "down the corridor",
        "best view",
        "garden out back",
        "stable accommodations",
        "accommodations in town",
        # Price / transaction claims
        "what'll it cost",
        "what will it cost",
        "cost you",
        "price",
        "five silver",
        "silver",
        "gold",
        "copper",
        # Quality/assignment claims
        "perfect for a traveler",
        "perfect for you",
        "just right for",
        "settle you in",
    )

    if not any(term in lower for term in invented_terms):
        return line

    return (
        "A room, you say? Let me check what I can offer before we settle the details."
    )


def _service_result_from_context(narration_context: Dict[str, Any]) -> Dict[str, Any]:
    narration_context = _safe_dict(narration_context)
    turn_contract = _safe_dict(narration_context.get("turn_contract"))

    # 1. Prefer direct authoritative/applied service result.
    #
    # Runtime service purchase mutation updates resolved_result. Older copies
    # under turn_contract.service_result or action.metadata.service_result may
    # still say purchase_ready. The narrator must use the applied copy.
    direct_service = _safe_dict(narration_context.get("service_result"))
    if direct_service.get("matched"):
        return direct_service

    direct_resolved = _safe_dict(narration_context.get("resolved_result"))
    direct_resolved_service = _safe_dict(direct_resolved.get("service_result"))
    if direct_resolved_service.get("matched"):
        return direct_resolved_service

    # 2. Then use resolved contract state.
    resolved = _safe_dict(
        turn_contract.get("resolved_result") or turn_contract.get("resolved_action")
    )
    resolved_service = _safe_dict(resolved.get("service_result"))
    if resolved_service.get("matched"):
        return resolved_service

    # 3. Then fallback to top-level contract state.
    direct = _safe_dict(turn_contract.get("service_result"))
    if direct.get("matched"):
        return direct

    # 4. Then fallback to action metadata.
    action = _safe_dict(turn_contract.get("action"))
    action_nested = _safe_dict(action.get("service_result"))
    if action_nested.get("matched"):
        return action_nested

    metadata = _safe_dict(action.get("metadata"))
    metadata_nested = _safe_dict(metadata.get("service_result"))
    if metadata_nested.get("matched"):
        return metadata_nested

    return {}


def _recalled_service_memories_from_context(
    narration_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    narration_context = _safe_dict(narration_context)
    memories = narration_context.get("recalled_service_memories")
    if isinstance(memories, list):
        return [_safe_dict(memory) for memory in memories if _safe_dict(memory)]
    return []


def _format_recalled_service_memories_for_prompt(
    narration_context: Dict[str, Any],
) -> str:
    memories = _recalled_service_memories_from_context(narration_context)
    if not memories:
        return "None."

    lines: List[str] = []
    for memory in memories[:5]:
        summary = _safe_str(memory.get("summary"))
        kind = _safe_str(memory.get("kind"))
        sentiment = _safe_str(memory.get("sentiment"))
        service_kind = _safe_str(memory.get("service_kind"))
        if not summary:
            continue
        details = ", ".join(
            part
            for part in [
                f"kind={kind}" if kind else "",
                f"service={service_kind}" if service_kind else "",
                f"sentiment={sentiment}" if sentiment else "",
            ]
            if part
        )
        if details:
            lines.append(f"- {summary} ({details})")
        else:
            lines.append(f"- {summary}")

    return "\n".join(lines) if lines else "None."


def _recalled_npc_memories_from_context(
    narration_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    narration_context = _safe_dict(narration_context)
    memories = narration_context.get("recalled_npc_memories")
    if isinstance(memories, list):
        return [_safe_dict(memory) for memory in memories if _safe_dict(memory)]
    return []


def _format_recalled_npc_memories_for_prompt(narration_context: Dict[str, Any]) -> str:
    memories = _recalled_npc_memories_from_context(narration_context)
    if not memories:
        return "None."

    lines: List[str] = []
    for memory in memories[:6]:
        summary = _safe_str(memory.get("summary"))
        kind = _safe_str(memory.get("kind"))
        sentiment = _safe_str(memory.get("sentiment"))
        if not summary:
            continue
        suffix = ", ".join(part for part in [kind, sentiment] if part)
        lines.append(f"- {summary}" + (f" ({suffix})" if suffix else ""))

    return "\n".join(lines) if lines else "None."


def _conversation_result_from_context(
    narration_context: Dict[str, Any],
) -> Dict[str, Any]:
    narration_context = _safe_dict(narration_context)
    direct = _safe_dict(narration_context.get("conversation_result"))
    if direct:
        return direct
    resolved = _safe_dict(narration_context.get("resolved_result"))
    return _safe_dict(resolved.get("conversation_result"))


def _format_conversation_beat_for_prompt(narration_context: Dict[str, Any]) -> str:
    conversation = _conversation_result_from_context(narration_context)
    if not conversation.get("triggered"):
        return "None."
    beat = _safe_dict(conversation.get("beat"))
    topic = _safe_dict(conversation.get("topic"))
    participation = _safe_dict(conversation.get("player_participation"))
    speaker = _safe_str(beat.get("speaker_name"))
    listener = _safe_str(beat.get("listener_name"))
    line = _safe_str(beat.get("line"))
    topic_title = _safe_str(topic.get("title") or beat.get("topic"))
    if not line:
        return "None."
    mode = _safe_str(
        participation.get("mode")
        or conversation.get("participation_mode")
        or "overheard"
    )
    return f'{speaker} speaks to {listener} about {topic_title} [{mode}]: "{line}"'


def _apply_grounded_conversation_beat(
    payload: Dict[str, Any],
    narration_context: Dict[str, Any],
) -> None:
    conversation = _conversation_result_from_context(narration_context)
    if not conversation.get("triggered"):
        return
    beat = _safe_dict(conversation.get("beat"))
    speaker = _safe_str(beat.get("speaker_name"))
    line = _safe_str(beat.get("line"))
    if not speaker or not line:
        return

    # Preserve the normal narration/action, but force the NPC line to the
    # deterministic conversation beat. The LLM can frame the scene but cannot
    # invent the actual NPC-to-NPC line.
    payload["npc"] = {
        "speaker": speaker,
        "line": line,
    }
    participation = _safe_dict(conversation.get("player_participation"))
    if participation.get("pending_response"):
        hooks = payload.get("followup_hooks")
        if not isinstance(hooks, list):
            hooks = []
        prompt = _safe_str(participation.get("prompt"))
        if prompt:
            hooks.append(prompt)
        payload["followup_hooks"] = hooks


def _line_has_prior_memory_reference(line: str) -> bool:
    lower = _safe_str(line).lower()
    if not lower:
        return False
    markers = (
        "remember",
        "last time",
        "again",
        "earlier",
        "still short",
        "short on coin",
        "same as before",
        "as i told you",
        "as i said",
        "you came by",
        "you asked",
        "you bought",
        "you tried",
    )
    return any(marker in lower for marker in markers)


def _memory_reference_is_backed(line: str, narration_context: Dict[str, Any]) -> bool:
    if not _line_has_prior_memory_reference(line):
        return True

    memories = _recalled_service_memories_from_context(narration_context)
    if not memories:
        return False

    lower = _safe_str(line).lower()
    for memory in memories:
        summary = _safe_str(memory.get("summary")).lower()
        kind = _safe_str(memory.get("kind"))
        if kind and kind in lower:
            return True
        if (
            "short" in lower
            and _safe_str(memory.get("blocked_reason")) == "insufficient_funds"
        ):
            return True
        if (
            "coin" in lower
            and _safe_str(memory.get("blocked_reason")) == "insufficient_funds"
        ):
            return True
        if "bought" in lower and kind == "service_purchase":
            return True
        if "asked" in lower and kind == "service_inquiry":
            return True
        if summary and any(
            token in summary for token in lower.split() if len(token) > 5
        ):
            return True

    specific_claim_terms = ("short", "coin", "bought", "paid", "purchased", "failed")
    if not any(term in lower for term in specific_claim_terms):
        return True

    return False


def _strip_unbacked_memory_reference_from_npc_line(
    line: str,
    narration_context: Dict[str, Any],
) -> str:
    service_backed = _memory_reference_is_backed(line, narration_context)
    npc_backed = memory_reference_is_backed(
        line,
        _recalled_npc_memories_from_context(narration_context),
    )
    if service_backed or npc_backed:
        return line

    from app.apps.rpg.narration.ai.world_scene_narrator_service_grounding import (
        _service_grounded_npc_line as _service_grounded_npc_line,
    )

    grounded_line = _service_grounded_npc_line(narration_context)
    if grounded_line:
        return grounded_line

    return "What can I help you with?"


def _strip_service_meta_language(text: str, narration_context: Dict[str, Any]) -> str:
    text = _safe_str(text)
    if not text:
        return text

    lower = text.lower()
    service_result = _service_result_from_context(narration_context)
    if not service_result.get("matched"):
        return text

    provider_name = _safe_str(service_result.get("provider_name") or "The provider")
    purchase = _safe_dict(service_result.get("purchase"))
    service_application = _safe_dict(narration_context.get("service_application"))
    blocked_reason = _safe_str(
        service_application.get("blocked_reason") or purchase.get("blocked_reason")
    )

    meta_markers = (
        "the system confirms",
        "the request to purchase",
        "is processed by",
        "the transaction is processed",
        "the intent to buy",
    )
    if not any(marker in lower for marker in meta_markers):
        return text

    if blocked_reason == "insufficient_funds":
        return f"{provider_name} checks the available offer and current coin, then finds the purchase cannot be completed."

    if blocked_reason == "offer_not_found":
        return f"{provider_name} checks the available offers and finds no matching item or service."

    if _safe_str(service_result.get("kind")) == "service_purchase":
        return f"{provider_name} checks the available offer and current terms."

    return text


def _service_offer_label_with_price(offer: Dict[str, Any]) -> str:
    offer = _safe_dict(offer)
    label = _safe_str(offer.get("label") or offer.get("offer_id")).strip()
    price = _safe_dict(offer.get("price"))

    parts = []
    gold = int(price.get("gold") or 0)
    silver = int(price.get("silver") or 0)
    copper = int(price.get("copper") or 0)

    if gold:
        parts.append(f"{gold} gold")
    if silver:
        parts.append(f"{silver} silver")
    if copper:
        parts.append(f"{copper} copper")

    if parts:
        return f"{label} for {', '.join(parts)}"
    return label


def _join_natural(items: List[str]) -> str:
    items = [item for item in items if _safe_str(item).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


__all__ = (
    'annotations',
    'json',
    'logging',
    're',
    'traceback',
    'dataclass',
    'field',
    'Any',
    'Callable',
    'Dict',
    'List',
    'Optional',
    'normalize_grounding_settings',
    'select_grounded_narration_candidate',
    'memory_reference_is_backed',
    'build_runtime_npc_response_architecture',
    'build_runtime_current_turn_prompt_contract',
    'format_runtime_prompt_contract_block',
    'build_runtime_presentation_guardrails_block',
    'sanitize_unsupported_combat_payload',
    'parse_runtime_provider_payload',
    'build_encounter_view',
    'logger',
    '_ACTIVE_NARRATIONS',
    'NARRATION_JSON_FORMAT_VERSION',
    'NARRATION_JSON_SCHEMA_HINT',
    '_extract_llm_text',
    '_llm_text',
    '_attach_social_context',
    '_safe_str_p6',
    '_attach_npc_mind_context',
    '_NARRATION_MAX_MARKDOWN',
    '_safe_str',
    '_safe_dict',
    '_safe_list',
    '_title_case_token',
    '_force_live_llm_required',
    '_build_ambient_conversation_line',
    '_bound_text',
    '_clean_npc_dialogue_line',
    '_is_accommodation_request',
    '_has_authoritative_accommodation_offer',
    '_ground_accommodation_npc_line',
    '_service_result_from_context',
    '_recalled_service_memories_from_context',
    '_format_recalled_service_memories_for_prompt',
    '_recalled_npc_memories_from_context',
    '_format_recalled_npc_memories_for_prompt',
    '_conversation_result_from_context',
    '_format_conversation_beat_for_prompt',
    '_apply_grounded_conversation_beat',
    '_line_has_prior_memory_reference',
    '_memory_reference_is_backed',
    '_strip_unbacked_memory_reference_from_npc_line',
    '_strip_service_meta_language',
    '_service_offer_label_with_price',
    '_join_natural',
)
