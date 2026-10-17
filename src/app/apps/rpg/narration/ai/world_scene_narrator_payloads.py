"""Split helpers for RPG world scene narration."""

from __future__ import annotations

from app.apps.rpg.narration.ai.memory_narration_grounding import sanitize_memory_narration_payload
from app.apps.rpg.narration.ai.world_scene_survival_grounding_bridge import (
    _merge_bs1_sanitized_payload,
)

from app.apps.rpg.narration.ai.world_scene_narrator_service_grounding import (
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
    _build_ambient_conversation_line as _build_ambient_conversation_line, _bound_text as _bound_text, _clean_npc_dialogue_line as _clean_npc_dialogue_line,
    _is_accommodation_request as _is_accommodation_request, _has_authoritative_accommodation_offer as _has_authoritative_accommodation_offer,
    _ground_accommodation_npc_line as _ground_accommodation_npc_line, _service_result_from_context as _service_result_from_context,
    _recalled_service_memories_from_context as _recalled_service_memories_from_context,
    _format_recalled_service_memories_for_prompt as _format_recalled_service_memories_for_prompt, _recalled_npc_memories_from_context as _recalled_npc_memories_from_context,
    _format_recalled_npc_memories_for_prompt as _format_recalled_npc_memories_for_prompt, _conversation_result_from_context as _conversation_result_from_context,
    _format_conversation_beat_for_prompt as _format_conversation_beat_for_prompt, _apply_grounded_conversation_beat as _apply_grounded_conversation_beat,
    _line_has_prior_memory_reference as _line_has_prior_memory_reference, _memory_reference_is_backed as _memory_reference_is_backed,
    _strip_unbacked_memory_reference_from_npc_line as _strip_unbacked_memory_reference_from_npc_line, _strip_service_meta_language as _strip_service_meta_language,
    _service_offer_label_with_price as _service_offer_label_with_price, _join_natural as _join_natural,
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
    _titleize_action as _titleize_action, _first_nonempty as _first_nonempty,
)

def _extract_text_lines(text: str) -> List[str]:
    lines = []
    for part in _safe_str(text).splitlines():
        part = part.strip()
        if part:
            lines.append(part)
    return lines


def _normalize_speaker_block(npc_value: Any) -> Dict[str, Any]:
    npc_value = (
        _safe_dict(npc_value)
        if isinstance(npc_value, dict)
        else {"text": _safe_str(npc_value).strip()}
    )
    return {
        "speaker_id": _safe_str(
            npc_value.get("speaker_id") or npc_value.get("npc_id")
        ).strip(),
        "name": _safe_str(npc_value.get("name")).strip(),
        "text": _bound_text(npc_value.get("text"), 180),
        "emotion": _safe_str(npc_value.get("emotion")).strip(),
        "portrait": _safe_str(npc_value.get("portrait")).strip(),
        "role": _safe_str(npc_value.get("role")).strip(),
    }


def _build_safe_prompt_context(
    scene: Dict[str, Any], narration_context: Dict[str, Any]
) -> Dict[str, Any]:
    scene = _safe_dict(scene)
    narration_context = _safe_dict(narration_context)
    resolved = _safe_dict(narration_context.get("resolved_result"))
    xp_result = _safe_dict(narration_context.get("xp_result"))
    skill_xp_result = _safe_dict(narration_context.get("skill_xp_result"))

    return {
        "player_input": _bound_text(narration_context.get("player_input"), 120),
        "action_type": _safe_str(narration_context.get("action_type")).strip(),
        "action_result": _bound_text(
            _first_nonempty(
                resolved.get("message"),
                resolved.get("summary"),
                resolved.get("result_text"),
            ),
            140,
        ),
        "target_name": _first_nonempty(
            _safe_dict(resolved.get("combat_result")).get("target_name"),
            resolved.get("target_name"),
            resolved.get("npc_name"),
            resolved.get("target_id"),
        ),
        "damage": int(
            _safe_dict(resolved.get("combat_result")).get(
                "damage", resolved.get("damage", 0)
            )
            or 0
        ),
        "player_xp": int(xp_result.get("player_xp", 0) or 0),
        "skill_xp_awards": {
            k: int(v or 0)
            for k, v in sorted(_safe_dict(skill_xp_result.get("awards")).items())
            if int(v or 0) > 0
        },
        "level_up": bool(_safe_list(narration_context.get("level_up"))),
        "scene_title": _safe_str(scene.get("title")).strip(),
        "location_name": _first_nonempty(
            scene.get("location_name"), scene.get("location_id"), scene.get("scene_id")
        ),
    }


def _build_speaker_turns(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    parsed = _safe_dict(parsed)
    npc = _normalize_speaker_block(parsed.get("npc"))
    turns: List[Dict[str, Any]] = []

    narrator_text = " ".join(
        filter(
            None,
            [
                _safe_str(parsed.get("narrator")).strip(),
                _safe_str(parsed.get("action")).strip(),
            ],
        )
    ).strip()
    if narrator_text:
        turns.append(
            {
                "speaker_id": "narrator",
                "name": "Narrator",
                "text": _bound_text(narrator_text, 180),
            }
        )
    if npc.get("text"):
        turns.append(npc)
    return turns


def _extract_json_object_from_text(text: str) -> Dict[str, Any]:
    text = _safe_str(text).strip()
    if not text:
        return {}

    candidates = [text]
    normalized_quotes = text.replace("\\'", "'")
    if normalized_quotes != text:
        candidates.append(normalized_quotes)

    # Fast path: raw JSON
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else {}
        except Exception:
            logger.debug("suppressed error in %s", "_extract_json_object_from_text", exc_info=True)

    # Fenced code block path
    if "```" in text:
        blocks = text.split("```")
        for block in blocks:
            candidate = block.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            if not candidate:
                continue
            candidate_variants = [candidate]
            normalized_candidate = candidate.replace("\\'", "'")
            if normalized_candidate != candidate:
                candidate_variants.append(normalized_candidate)
            for candidate_variant in candidate_variants:
                try:
                    value = json.loads(candidate_variant)
                    return value if isinstance(value, dict) else {}
                except Exception:
                    logger.debug("suppressed error in %s", "_extract_json_object_from_text", exc_info=True)
                    continue

    # Loose substring path: first balanced {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        candidate_variants = [candidate]
        normalized_candidate = candidate.replace("\\'", "'")
        if normalized_candidate != candidate:
            candidate_variants.append(normalized_candidate)
        for candidate_variant in candidate_variants:
            try:
                value = json.loads(candidate_variant)
                return value if isinstance(value, dict) else {}
            except Exception:
                logger.debug("suppressed error in %s", "_extract_json_object_from_text", exc_info=True)

    return {}


def _normalize_narration_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    npc = _safe_dict(payload.get("npc"))

    return {
        "format_version": _safe_str(payload.get("format_version")).strip()
        or NARRATION_JSON_FORMAT_VERSION,
        "narration": _safe_str(payload.get("narration")).strip(),
        "action": _safe_str(payload.get("action")).strip(),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")).strip(),
            "line": _safe_str(npc.get("line")).strip(),
        },
        "reward": _safe_str(payload.get("reward")).strip(),
        "followup_hooks": _safe_list(payload.get("followup_hooks")),
    }


def _parse_llm_narration_payload(raw: Any) -> Dict[str, Any]:
    provider_payload = parse_runtime_provider_payload(raw)
    if provider_payload.get("ok"):
        return _safe_dict(provider_payload.get("payload"))

    if isinstance(raw, dict):
        return raw

    text = _safe_str(raw).strip()
    if not text:
        return {}

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()

    parsed_json = _extract_json_object_from_text(text)
    if parsed_json:
        return parsed_json

    return _recover_narration_from_raw_text(text)


def _strict_narration_payload(value: Dict[str, Any]) -> Dict[str, Any]:
    value = _safe_dict(value)
    npc = _safe_dict(value.get("npc"))
    return {
        "format_version": "rpg_narration_v2",
        "narration": _safe_str(value.get("narration")).strip(),
        # IMPORTANT: never inject "You act." here.
        "action": _safe_str(value.get("action")).strip(),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")).strip(),
            "line": _safe_str(npc.get("line")).strip(),
        },
        "reward": "",
        "followup_hooks": _safe_list(value.get("followup_hooks")),
    }


def _strip_basic_markdown(text: Any) -> str:
    text = _safe_str(text)
    if not text:
        return ""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return " ".join(text.split()).strip()


def _recent_authoritative_facts(narration_context: Dict[str, Any]) -> List[str]:
    narration_context = _safe_dict(narration_context)
    facts = []
    for row in _safe_list(narration_context.get("recent_authoritative_facts")):
        text = _safe_str(row).strip()
        if text:
            facts.append(text)
    return facts


def _extract_continuity_price_facts(narration_context: Dict[str, Any]) -> List[str]:
    facts = _recent_authoritative_facts(narration_context)
    hits: List[str] = []
    for fact in facts:
        lower = fact.lower()
        if "room" in lower and (
            "gold" in lower or "silver" in lower or "copper" in lower
        ):
            hits.append(fact)
    return hits


def _extract_present_actor_names(
    scene: Dict[str, Any], narration_context: Dict[str, Any]
) -> List[str]:
    names: List[str] = []
    seen = set()

    def _add(value: Any) -> None:
        name = _safe_str(value).strip()
        if not name:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(name)

    for actor in _safe_list(_safe_dict(scene).get("actors")):
        if isinstance(actor, dict):
            _add(actor.get("name") or actor.get("id"))
        else:
            _add(actor)

    for actor in _safe_list(
        _safe_dict(narration_context.get("grounded")).get("present_actor_names")
    ):
        _add(actor)

    resolved = _safe_dict(narration_context.get("resolved_result"))
    _add(resolved.get("target_name"))
    _add(resolved.get("npc_name"))
    _add(resolved.get("speaker_name"))
    _add(_safe_dict(resolved.get("npc")).get("name"))
    return names


def _extract_price_tokens(text: str) -> set:
    number_pattern = (
        r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    )
    return set(
        re.findall(rf"\b{number_pattern}\s*(gold|silver|copper)\b", text.lower())
    )


def _sanitize_narration_text(
    text: Any,
    scene: Dict[str, Any],
    narration_context: Dict[str, Any],
) -> str:
    text = _safe_str(text).strip()
    if not text:
        return ""

    allowed_names = {
        name.lower(): name
        for name in _extract_present_actor_names(scene, narration_context)
    }
    continuity_price_facts = _extract_continuity_price_facts(narration_context)

    banned_generic_terms = (
        "guard",
        "guards",
        "merchant guild",
        "guild",
        "town guard",
        "soldier",
        "soldiers",
    )

    IGNORE_NAMES = {
        "the tavern",
        "the room",
        "the inn",
        "the bar",
        "the rusty flagon tavern",
    }

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    kept: List[str] = []

    for sentence in sentences:
        lower = sentence.lower()

        # Reject raw JSON leakage or partial structured output.
        if (
            sentence.startswith("{")
            or '"format_version"' in sentence
            or '"narration"' in sentence
        ):
            continue

        # Reject invented off-scene enforcement / faction actors unless they are present.
        if any(term in lower for term in banned_generic_terms):
            # allow passive mentions, reject active invention
            if not re.search(
                r"\b(call|calls|called|signal|signals|signaled|summon|summons|summoned|order|orders|ordered|arrive|arrives|arrived|rush|rushes|rushed|draw|draws|drew|attack|attacks|attacked|spread|spreads|spreads)\b",
                lower,
            ):
                kept.append(sentence)
                continue
            else:
                continue

        # If a recent authoritative room price exists, reject contradictory new price narration.
        if (
            continuity_price_facts
            and "room" in lower
            and ("gold" in lower or "silver" in lower or "copper" in lower)
        ):
            prior_price_tokens = set()
            for fact in continuity_price_facts:
                prior_price_tokens.update(_extract_price_tokens(fact))
            current_price_tokens = _extract_price_tokens(sentence)
            if current_price_tokens and not current_price_tokens.issubset(
                prior_price_tokens
            ):
                continue

        # Reject named actor mentions that are not present/grounded.
        candidate_names = re.findall(
            r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b", sentence
        )
        unknown_name = False
        for raw_name in candidate_names:
            key = raw_name.strip().lower()
            if key in IGNORE_NAMES:
                continue
            if key not in allowed_names:
                unknown_name = True
                break
        if unknown_name:
            continue

        kept.append(sentence)

    if not kept:
        # fallback to authoritative action
        turn_contract = _safe_dict(narration_context.get("turn_contract"))
        narration_brief = _safe_dict(turn_contract.get("narration_brief"))
        resolved = _safe_dict(narration_context.get("resolved_result"))
        fallback = _safe_str(
            narration_brief.get("summary")
            or resolved.get("narrative_brief")
            or resolved.get("message")
            or resolved.get("summary")
            or _authoritative_action_text(narration_context)
        )
        if fallback.strip().lower() in {
            "action: you act.",
            "you act.",
            "action: you act",
        }:
            fallback = "The action changes the scene, and the people nearby react according to what just happened."
        return _bound_text(fallback, 220)

    return _bound_text(" ".join(kept), 1400)


def _authoritative_action_text(narration_context: Dict[str, Any]) -> str:
    text = _strip_basic_markdown(_build_action_result_line(narration_context)).strip()
    text = re.sub(r"^(?:\*\*)?action(?:\*\*)?\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def _authoritative_reward_text(narration_context: Dict[str, Any]) -> str:
    return _strip_basic_markdown(_build_rewards_block(narration_context))


def _allowed_npc_speakers(
    scene: Dict[str, Any], narration_context: Dict[str, Any]
) -> List[str]:
    return _extract_present_actor_names(scene, narration_context)


def _sanitize_npc_block(
    payload: Dict[str, Any],
    scene: Dict[str, Any],
    narration_context: Dict[str, Any],
) -> Dict[str, str]:
    payload = _normalize_narration_json(payload)
    npc = _safe_dict(payload.get("npc"))
    speaker = _safe_str(npc.get("speaker")).strip()
    line = _safe_str(npc.get("line")).strip()

    if not line:
        return {"speaker": "", "line": ""}

    allowed = _allowed_npc_speakers(scene, narration_context)
    allowed_lut = {name.lower(): name for name in allowed}
    resolved = _safe_dict(_safe_dict(narration_context).get("resolved_result"))

    # Prefer authoritative target speaker if present.
    preferred = _first_nonempty(
        resolved.get("target_name"),
        resolved.get("npc_name"),
        resolved.get("speaker_name"),
        _safe_dict(resolved.get("npc")).get("name"),
    )

    if speaker:
        canonical = allowed_lut.get(speaker.lower())
        if canonical:
            speaker = canonical
        elif preferred and preferred.lower() in allowed_lut:
            speaker = allowed_lut.get(preferred.lower(), preferred)
        else:
            # Fall back to preferred authoritative speaker instead of dropping entirely
            if preferred and preferred.lower() in allowed_lut:
                speaker = allowed_lut.get(preferred.lower(), preferred)
            else:
                return {"speaker": "", "line": ""}
    elif preferred and preferred.lower() in allowed_lut:
        speaker = allowed_lut.get(preferred.lower(), preferred)

    line = _clean_npc_dialogue_line(line)
    if not line:
        return {"speaker": "", "line": ""}

    return {
        "speaker": speaker,
        "line": line,
    }


def _desystemify_text(text: str) -> str:
    text = _safe_str(text).strip()
    if not text:
        return ""

    banned_prefixes = (
        "The player ",
        "The NPC ",
        "The target ",
    )

    for prefix in banned_prefixes:
        if text.startswith(prefix):
            text = text.replace("The player ", "You ", 1)
            text = text.replace("The NPC ", "", 1)
            text = text.replace("The target ", "", 1)

    # normalize various internal id patterns
    text = text.replace("npc_bran", "Bran")
    text = text.replace("npc:0", "Bran")
    text = text.replace("np:bran", "Bran")
    text = text.replace("np:", "")
    text = text.replace("npc_", "")

    # grammar cleanup
    text = text.replace("You takes", "You take")
    text = text.replace("You attempts", "You attempt")
    text = text.replace("You tries", "You try")
    text = text.replace("You goes", "You go")
    text = text.replace("You is ", "You are ")
    text = text.replace("You was ", "You were ")

    text = text.replace(" asks player", " asks")

    return text


def _strip_meta_narration(text: str) -> str:
    text = _safe_str(text)

    forbidden_phrases = (
        "The player ",
        "The NPC ",
        "The target ",
        "Narrate ",
        "Interpret the action",
        "should react",
        "according to the state delta",
    )

    for phrase in forbidden_phrases:
        if phrase in text:
            return ""

    return text


def _fallback_in_world_narration(narration_context: Dict[str, Any]) -> str:
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    npc_behavior = _safe_dict(
        narration_context.get("npc_behavior_context")
        or turn_contract.get("npc_behavior_context")
    )

    intent = _safe_str(interpreted.get("intent")).lower()
    target_name = _safe_str(
        npc_behavior.get("target_name") or interpreted.get("target_name") or "Bran"
    )

    if intent == "service":
        return f"{target_name} looks you over from behind the bar, weighing your request before answering."
    if intent == "attack":
        return f"You move suddenly, turning the exchange violent. {target_name} recoils as the tavern around you goes tense."
    if intent == "apologize":
        return f"{target_name} watches you carefully, the apology landing against the memory of what just happened."
    if intent == "ask":
        return f"{target_name} studies you for a moment before answering, still shaped by the recent tension."

    return "The room shifts around your action, attention turning toward you as the moment changes."


def _enforce_npc_behavior(
    payload: Dict[str, Any], narration_context: Dict[str, Any]
) -> Dict[str, Any]:
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    npc_behavior = _safe_dict(
        narration_context.get("npc_behavior_context")
        or turn_contract.get("npc_behavior_context")
    )

    target_id = _safe_str(interpreted.get("target_id"))
    target_name = _safe_str(
        npc_behavior.get("target_name") or interpreted.get("target_name") or target_id
    )

    if not (target_id and target_name):
        return payload

    npc = _safe_dict(payload.get("npc"))

    npc["speaker"] = target_name

    if not _safe_str(npc.get("line")):
        tone = _safe_str(npc_behavior.get("reaction_tone") or "wary")
        action_kind = (
            _safe_str(interpreted.get("intent") or interpreted.get("action_type"))
            .strip()
            .lower()
        )

        if tone == "hostile":
            npc["line"] = (
                "You have made your point. Now get out before this gets worse."
            )
        elif tone == "afraid":
            npc["line"] = "Stay back. I do not want any more trouble."
        elif tone == "friendly":
            npc["line"] = "All right, I am listening. What do you need?"
        elif action_kind in {
            "ask",
            "dialogue",
            "social",
            "social_activity",
            "conversation",
        }:
            npc["line"] = ""
        else:
            npc["line"] = "I hear you. Give me a moment to answer that plainly."

    # anti-repeat logic
    recent_lines = []
    for thread in _safe_list(narration_context.get("conversation_threads")):
        for recent in _safe_list(_safe_dict(thread).get("recent_lines")):
            recent_lines.append(
                _safe_str(_safe_dict(recent).get("text")).strip().lower()
            )

    if any(
        _safe_str(npc.get("line")).strip().lower()[:40] in line for line in recent_lines
    ):
        tone = _safe_str(npc_behavior.get("reaction_tone") or "wary")

        if tone == "hostile":
            npc["line"] = "I remember what you did. Choose your next words carefully."
        elif tone == "afraid":
            npc["line"] = "I am not forgetting that. Keep your distance."
        elif tone == "friendly":
            npc["line"] = "Go on, then. I am listening."
        else:
            npc["line"] = "Let us not pretend nothing happened."

    if npc.get("speaker") == "Player":
        npc["speaker"] = target_name

    payload["npc"] = npc
    return payload


def _sanitize_narration_payload(
    payload: Dict[str, Any],
    scene: Dict[str, Any],
    narration_context: Dict[str, Any],
    authoritative_action: str | None = None,
) -> Dict[str, Any]:
    original_payload = _safe_dict(payload)
    payload = _normalize_narration_json(payload)

    if authoritative_action is None:
        authoritative_action = _authoritative_action_text(narration_context)
    authoritative_result_action = _authoritative_action_text(narration_context)
    authoritative_reward = _authoritative_reward_text(narration_context)
    sanitized_npc = _sanitize_npc_block(payload, scene, narration_context)

    if _safe_str(sanitized_npc.get("speaker")).lower() == "player":
        sanitized_npc["speaker"] = ""

    # Presentation-only narration text remains model-authored, but sanitized against hallucinations
    llm_narration = _safe_str(payload.get("narration")).strip()
    narration_text = _sanitize_narration_text(llm_narration, scene, narration_context)

    # Action and reward are authoritative-only.
    llm_action = _safe_str(payload.get("action")).strip()
    normalized = _normalize_narration_json(
        {
            "format_version": NARRATION_JSON_FORMAT_VERSION,
            "narration": narration_text,
            "action": llm_action,
            "npc": sanitized_npc,
            "reward": _safe_str(payload.get("reward")).strip(),
            "followup_hooks": [],
        }
    )

    reward_text = _desystemify_text(_safe_str(normalized.get("reward")))
    authoritative_reward = _authoritative_reward_text(narration_context)

    if reward_text and not authoritative_reward:
        reward_text = ""

    normalized["reward"] = reward_text

    normalized = _enforce_npc_behavior(normalized, narration_context)

    # Fallback: ensure physical reaction in narration if missing
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    npc_behavior = _safe_dict(
        narration_context.get("npc_behavior_context")
        or turn_contract.get("npc_behavior_context")
    )
    target_id = _safe_str(interpreted.get("target_id"))
    target_name = _safe_str(
        npc_behavior.get("target_name") or interpreted.get("target_name") or target_id
    )
    if target_id and target_name:
        narration = _safe_str(
            normalized.get("narrator") or normalized.get("narration")
        ).strip()
        if target_name.lower() not in narration.lower():
            intent = _safe_str(interpreted.get("intent") or "").lower()
            if "attack" in intent:
                prefix = f"{target_name} recoils from the blow, the room going tense. "
            elif "apologize" in intent or "apology" in intent:
                prefix = f"{target_name} pauses, considering your words. "
            elif "ask" in intent:
                prefix = f"{target_name} turns toward you, attentive. "
            else:
                tone = _safe_str(npc_behavior.get("reaction_tone") or "wary")
                if tone == "hostile":
                    prefix = f"{target_name} snaps upright, anger flashing across their face. "
                elif tone == "afraid":
                    prefix = f"{target_name} recoils, instinctively putting space between you. "
                elif tone == "friendly":
                    prefix = (
                        f"{target_name} shifts, reacting to you with a hint of warmth. "
                    )
                else:
                    prefix = f"{target_name} stiffens, clearly affected by what just happened. "
            normalized["narration"] = prefix + narration

    narration_clean = _desystemify_text(_safe_str(normalized.get("narration")))
    narration_clean = _strip_meta_narration(narration_clean)

    service_result = _service_result_from_context(narration_context)
    service_action_override = ""
    if service_result.get("matched") and not _service_purchase_is_applied(
        service_result, narration_context
    ):
        service_action_override = _service_grounded_action_result(narration_context)
    grounded_narration = _service_grounded_narration_text(narration_context)
    if (
        service_result.get("matched")
        and grounded_narration
        and (
            not _service_purchase_is_applied(service_result, narration_context)
            or _successful_service_purchase_text_needs_grounding(
                narration_clean, narration_context
            )
        )
    ):
        narration_clean = grounded_narration

    if service_result.get("matched") and _service_narration_needs_grounding(
        narration_clean
    ):
        if grounded_narration and (
            not _service_purchase_is_applied(service_result, narration_context)
            or _successful_service_purchase_text_needs_grounding(
                narration_clean, narration_context
            )
        ):
            narration_clean = grounded_narration

    if not narration_clean:
        # only fallback if LLM truly failed
        narration_clean = ""

    narration_clean = _strip_service_meta_language(narration_clean, narration_context)
    normalized["narration"] = narration_clean
    normalized["narration"] = _naturalize_service_debug_language(
        normalized["narration"]
    )
    _sanitize_repeated_player_input_narration(normalized, narration_context)
    action_raw = _safe_str(normalized.get("action"))

    # Only strip CLEAR meta/system instructions
    if (
        "Narrate" in action_raw
        or "Interpret" in action_raw
        or "according to the state delta" in action_raw
    ):
        normalized["action"] = ""
    else:
        normalized["action"] = _desystemify_text(action_raw.strip())
        normalized["action"] = _ground_action_result_text(
            normalized["action"],
            narration_context,
        )
        if service_action_override:
            normalized["action"] = service_action_override
        elif authoritative_result_action:
            normalized["action"] = authoritative_result_action
    normalized["action"] = _final_grounded_service_action_text(
        _safe_str(normalized.get("action")),
        narration_context,
    )
    normalized["narration"] = _naturalize_service_debug_language(
        _safe_str(normalized.get("narration"))
    )
    normalized["action"] = _naturalize_service_debug_language(
        _safe_str(normalized.get("action"))
    )
    npc = _safe_dict(normalized.get("npc"))
    if npc:
        npc["line"] = _naturalize_service_debug_language(_safe_str(npc.get("line")))
        normalized["npc"] = npc
    npc["speaker"] = _desystemify_text(_safe_str(npc.get("speaker")))
    npc["line"] = _clean_npc_dialogue_line(
        _desystemify_text(_safe_str(npc.get("line")))
    )

    service_result = _service_result_from_context(narration_context)
    service_purchase = _safe_dict(service_result.get("purchase"))
    service_application = _safe_dict(narration_context.get("service_application"))
    service_status = _safe_str(service_result.get("status"))
    blocked_reason = _safe_str(
        service_application.get("blocked_reason")
        or service_purchase.get("blocked_reason")
    )
    service_purchase_applied = _safe_str(
        service_result.get("kind")
    ) == "service_purchase" and (
        service_status == "purchased"
        or bool(service_purchase.get("applied"))
        or bool(service_application.get("applied"))
    )
    service_purchase_offer_not_found = _safe_str(
        service_result.get("kind")
    ) == "service_purchase" and (
        service_status == "purchase_offer_not_found"
        or blocked_reason == "offer_not_found"
    )

    if not service_result.get("matched"):
        continuity_price_facts = _extract_continuity_price_facts(narration_context)
        npc_lower = _safe_str(npc.get("line")).lower()
        if continuity_price_facts and (
            "gold" in npc_lower or "silver" in npc_lower or "copper" in npc_lower
        ):
            prior_price_tokens = set()
            for fact in continuity_price_facts:
                prior_price_tokens.update(_extract_price_tokens(fact))
            current_price_tokens = _extract_price_tokens(npc["line"])
            if current_price_tokens and not current_price_tokens.issubset(
                prior_price_tokens
            ):
                resolved_dialogue = _safe_str(
                    _safe_dict(narration_context.get("resolved_result")).get("dialogue")
                ).strip()
                npc["line"] = _clean_npc_dialogue_line(resolved_dialogue)

    preserve_backed_memory_reference = _line_has_prior_memory_reference(
        npc["line"]
    ) and _memory_reference_is_backed(npc["line"], narration_context)

    if (
        service_result.get("matched")
        and not preserve_backed_memory_reference
        and (
            service_status
            in {
                "offers_available",
                "no_registered_offers",
                "blocked",
                "purchase_ready",
                "purchase_offer_not_found",
            }
            or service_purchase_offer_not_found
            or (
                service_purchase_applied
                and _successful_service_purchase_text_needs_grounding(
                    npc["line"], narration_context
                )
            )
            or (
                not service_purchase_applied
                and _service_claim_needs_grounding(npc["line"])
            )
        )
    ):
        grounded_line = _service_grounded_npc_line(narration_context)
        if grounded_line:
            npc["line"] = grounded_line
    else:
        npc["line"] = _ground_accommodation_npc_line(npc["line"], narration_context)

    npc["line"] = _strip_unbacked_memory_reference_from_npc_line(
        npc["line"],
        narration_context,
    )

    normalized["npc"] = npc

    if not _safe_str(_safe_dict(normalized.get("npc")).get("line")):
        original_npc = _safe_dict(payload.get("npc"))
        if _safe_str(original_npc.get("line")):
            npc = _safe_dict(normalized.get("npc"))
            npc["speaker"] = _safe_str(
                npc.get("speaker") or original_npc.get("speaker")
            ).strip()
            restored_line = _clean_npc_dialogue_line(original_npc.get("line"))
            service_result = _service_result_from_context(narration_context)
            if service_result.get("matched") and _service_claim_needs_grounding(
                restored_line
            ):
                restored_line = _service_grounded_npc_line(narration_context)
            else:
                restored_line = _ground_accommodation_npc_line(
                    restored_line, narration_context
                )
            restored_line = _strip_unbacked_memory_reference_from_npc_line(
                restored_line,
                narration_context,
            )
            npc["line"] = restored_line
            normalized["npc"] = npc

    travel_narration = _grounded_travel_narration(narration_context)
    travel_action = _grounded_travel_action(narration_context)
    if travel_narration:
        normalized["narration"] = travel_narration
    if travel_action:
        normalized["action"] = travel_action

    normalized = sanitize_unsupported_combat_payload(normalized, narration_context)

    _apply_grounded_conversation_beat(normalized, narration_context)
    conversation = _conversation_result_from_context(narration_context)
    if conversation.get("triggered"):
        normalized["action"] = "Ambient conversation continues nearby."
        if (
            not _safe_str(normalized.get("narration"))
            or "success" in _safe_str(normalized.get("narration")).lower()
        ):
            normalized["narration"] = (
                "Nearby voices continue in the living world around you."
            )

    normalized = sanitize_memory_narration_payload(
        normalized,
        {
            **_safe_dict(narration_context),
            "scene": _safe_dict(scene),
        },
    )

    return _merge_bs1_sanitized_payload(
        original_payload=original_payload,
        legacy_payload=normalized,
        narration_context=_safe_dict(narration_context),
    )


def _render_narration_text_from_json(payload: Dict[str, Any]) -> str:
    payload = _normalize_narration_json(payload)
    parts: List[str] = []

    if payload["narration"]:
        parts.append(payload["narration"])

    if payload["action"]:
        parts.append(payload["action"])

    npc = _safe_dict(payload.get("npc"))
    speaker = _safe_str(npc.get("speaker")).strip()
    line = _safe_str(npc.get("line")).strip()
    if speaker and line:
        parts.append(f'{speaker}: "{line}"')
    elif line:
        parts.append(line)

    if payload["reward"]:
        parts.append(f"Rewards: {payload['reward']}")

    return "\n\n".join([p for p in parts if p]).strip()


def _recover_narration_from_raw_text(text: str) -> Dict[str, Any]:
    text = _safe_str(text).strip()
    if not text:
        return _normalize_narration_json({})

    if text.startswith("{") or '"format_version"' in text or '"narration"' in text:
        extracted = _extract_json_object_from_text(text)
        if extracted:
            return _normalize_narration_json(extracted)
        return _normalize_narration_json({})

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    narration_parts: List[str] = []
    action_parts: List[str] = []
    npc_speaker = ""
    npc_line = ""
    reward = ""

    for line in lines:
        lower = line.lower()
        if lower.startswith("narrator:"):
            narration_parts.append(line.split(":", 1)[1].strip())
        elif lower.startswith("action:"):
            action_parts.append(line.split(":", 1)[1].strip())
        elif lower.startswith("npc:"):
            rest = line.split(":", 1)[1].strip()
            if ":" in rest:
                maybe_speaker, maybe_line = rest.split(":", 1)
                npc_speaker = maybe_speaker.strip()
                npc_line = maybe_line.strip().strip('"')
            else:
                npc_line = rest.strip().strip('"')
        elif lower.startswith("reward:"):
            reward = line.split(":", 1)[1].strip()
        else:
            narration_parts.append(line)

    return _normalize_narration_json(
        {
            "format_version": NARRATION_JSON_FORMAT_VERSION,
            "narration": " ".join(narration_parts).strip(),
            "action": " ".join(action_parts).strip(),
            "npc": {
                "speaker": npc_speaker,
                "line": npc_line,
            },
            "reward": reward,
            "followup_hooks": [],
        }
    )


def _structured_fallback_response(
    narration_context: Dict[str, Any] | None = None,
) -> str:
    narration_context = _safe_dict(narration_context)
    resolved = _safe_dict(narration_context.get("resolved_result"))
    visible_response = _safe_dict(resolved.get("visible_response"))
    npc = _safe_dict(visible_response.get("npc") or resolved.get("npc"))

    npc_speaker = _safe_str(npc.get("speaker") or npc.get("name")).strip()
    npc_line = _safe_str(npc.get("line") or npc.get("text")).strip()
    narration = _safe_str(
        visible_response.get("narration")
        or resolved.get("final_narration")
        or resolved.get("narration")
        or resolved.get("message")
        or resolved.get("summary")
        or "The moment settles, and the scene waits for your next move."
    ).strip()
    action = _authoritative_action_text(narration_context) or "The action resolves."

    parts = [
        f"NARRATOR: {_bound_text(narration, 260)}",
        f"ACTION: {_bound_text(action, 180)}",
    ]
    if npc_line:
        if npc_speaker:
            parts.append(f'NPC: {npc_speaker}: "{_bound_text(npc_line, 180)}"')
        else:
            parts.append(f'NPC: "{_bound_text(npc_line, 180)}"')
    return "\n".join(parts)


__all__ = (
    'annotations',
    'sanitize_memory_narration_payload',
    '_merge_bs1_sanitized_payload',
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
    '_strip_basic_markdown',
    '_travel_result_from_context',
    '_grounded_travel_narration',
    '_grounded_travel_action',
    '_final_grounded_service_action_text',
    '_service_grounded_action_result',
    '_service_grounded_npc_line',
    '_normalized_text_for_compare',
    '_fallback_non_service_narration',
    '_sanitize_repeated_player_input_narration',
    '_naturalize_service_debug_language',
    '_service_grounded_narration_text',
    '_service_narration_needs_grounding',
    '_service_claim_needs_grounding',
    '_service_purchase_is_applied',
    '_selected_service_offer',
    '_service_extract_price_tokens',
    '_successful_service_purchase_text_needs_grounding',
    '_ground_action_result_text',
    '_player_input_action_text',
    '_build_authoritative_action_line',
    '_build_action_result_line',
    '_build_rewards_block',
    '_titleize_action',
    '_first_nonempty',
    '_extract_text_lines',
    '_normalize_speaker_block',
    '_build_safe_prompt_context',
    '_build_speaker_turns',
    '_extract_json_object_from_text',
    '_normalize_narration_json',
    '_parse_llm_narration_payload',
    '_strict_narration_payload',
    '_recent_authoritative_facts',
    '_extract_continuity_price_facts',
    '_extract_present_actor_names',
    '_extract_price_tokens',
    '_sanitize_narration_text',
    '_authoritative_action_text',
    '_authoritative_reward_text',
    '_allowed_npc_speakers',
    '_sanitize_npc_block',
    '_desystemify_text',
    '_strip_meta_narration',
    '_fallback_in_world_narration',
    '_enforce_npc_behavior',
    '_sanitize_narration_payload',
    '_render_narration_text_from_json',
    '_recover_narration_from_raw_text',
    '_structured_fallback_response',
)
