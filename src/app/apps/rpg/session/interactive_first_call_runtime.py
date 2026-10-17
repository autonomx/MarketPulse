from __future__ import annotations

import logging

import sys
from copy import deepcopy
from time import perf_counter
from typing import Any

from app.apps.rpg.session.action_intelligence import get_action_advisory  # noqa: F401
from app.apps.rpg.session.compact_dialogue import (
    build_compact_dialogue_advisory,
)
from app.apps.rpg.session.semantic_action_intelligence import get_semantic_action_advisory
from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway
from app.apps.rpg.session.first_call_dialogue import build_non_stateful_dialogue_result
from app.apps.rpg.session.dialogue_fallbacks import repair_dialogue_fallback
from app.apps.rpg.session.public_state_bridge import hydrate_simulation_player
from app.apps.rpg.rules.semantic_interaction import attach_semantic_interaction
from app.apps.rpg.rules.economy.service_resolver import resolve_service_turn
from app.apps.rpg.session.companion_turn_runtime import _build_turn_id
from app.apps.rpg.session.service_runtime import service_action_from_result
from app.apps.rpg.session.session_runtime_store import load_runtime_session, save_runtime_session
from app.apps.rpg.session.turn_response_composition import apply_turn as _apply_composed_turn
from app.apps.rpg.foundation.safe_values import copy_state

logger = logging.getLogger(__name__)

_FAST_DIRECT_SOURCE = "ce212_fast_direct_runtime_budget_v1"
_RECURSION_LIMIT_FLOOR = 10000


def _ensure_runtime_recursion_budget() -> int:
    current = sys.getrecursionlimit()
    if current < _RECURSION_LIMIT_FLOOR:
        sys.setrecursionlimit(_RECURSION_LIMIT_FLOOR)
    return sys.getrecursionlimit()


def _ms_since(start: float) -> float:
    return round((perf_counter() - start) * 1000.0, 3)


def _attach_manual_stage_timing(result: dict[str, Any], timing: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    result["manual_turn_stage_timing"] = dict(timing)
    nested = _d(result.get("result"))
    if nested:
        nested["manual_turn_stage_timing"] = dict(timing)
        result["result"] = nested
    return result


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value) if value is not None else ""


def _b(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"1", "true", "yes", "y", "on"}:
            return True
        if lower in {"0", "false", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _select_session(session_id: str, session_override: dict[str, Any] | None = None) -> dict[str, Any]:
    override = _d(session_override)
    if override:
        return deepcopy(override)

    loaded = load_runtime_session(session_id)
    return _d(loaded)


def _first_call_diagnostics(action_advisory: dict[str, Any], semantic_advisory: dict[str, Any]) -> dict[str, Any]:
    return _d(
        _d(semantic_advisory).get("first_call_grounding_diagnostics")
        or _d(action_advisory).get("first_call_grounding_diagnostics")
    )


def _packet_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return _d(_d(diagnostics).get("turn_grounding_packet"))


def _first_call_packet(action_advisory: dict[str, Any], semantic_advisory: dict[str, Any]) -> dict[str, Any]:
    return _packet_from_diagnostics(_first_call_diagnostics(action_advisory, semantic_advisory))


def _addressed_profile(packet: dict[str, Any]) -> dict[str, Any]:
    addressed = _l(_d(packet.get("npc_context")).get("addressed_npcs"))
    return _d(addressed[0]) if addressed else {}


def _stateful_action_from_first_call(
    action_advisory: dict[str, Any],
    semantic_advisory: dict[str, Any],
) -> dict[str, Any]:
    semantic_advisory = _d(semantic_advisory)
    action_advisory = _d(action_advisory)
    source = semantic_advisory or action_advisory
    if not source:
        return {}
    action = {
        "action_type": _s(source.get("action_type")),
        "target_id": _s(source.get("target_id")),
        "target_name": _s(source.get("target_name")),
        "difficulty": _s(source.get("difficulty")),
        "skill_id": _s(source.get("skill_id")),
        "metadata": {
            "first_call_advisory": True,
            "first_call_action_advisory": action_advisory,
            "first_call_semantic_advisory": semantic_advisory,
            "first_call_grounding_diagnostics": _first_call_diagnostics(action_advisory, semantic_advisory),
        },
    }
    return {k: v for k, v in action.items() if v not in (None, "", {})}


def _disable_duplicate_runtime_first_call(performance_override: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(_d(performance_override))
    merged.setdefault("enable_action_advisory", False)
    merged.setdefault("enable_semantic_action_advisory", False)
    return merged


def _narration_mode(performance_override: dict[str, Any] | None, runtime_state: dict[str, Any]) -> str:
    perf = _d(performance_override)
    runtime_state = _d(runtime_state)
    settings = _d(runtime_state.get("runtime_settings") or runtime_state.get("settings"))
    mode = _s(
        perf.get("narration_mode")
        or runtime_state.get("narration_mode")
        or settings.get("narration_mode")
        or "deferred"
    ).strip().lower()
    return mode if mode in {"deferred", "blocking", "deterministic", "disabled"} else "deferred"


def _prepare_stateful_runtime_session(
    session_id: str,
    session: dict[str, Any],
    *,
    narration_mode: str,
) -> None:
    runtime_state = _d(session.get("runtime_state"))
    runtime_state["narration_mode"] = narration_mode
    runtime_state["force_sync_narration"] = narration_mode == "blocking"
    if narration_mode in {"deferred", "deterministic", "disabled"}:
        runtime_state["deferred_runtime_narration"] = True
    session["runtime_state"] = runtime_state
    try:
        session_to_save = copy_state(session)
        manifest = _d(session_to_save.get("manifest"))
        manifest["session_id"] = session_id
        manifest.setdefault("id", session_id)
        session_to_save["manifest"] = manifest
        save_runtime_session(session_to_save)
        loaded = load_runtime_session(session_id)
        if isinstance(loaded, dict):
            loaded_runtime = _d(loaded.get("runtime_state"))
            loaded_runtime["narration_mode"] = narration_mode
            loaded_runtime["force_sync_narration"] = narration_mode == "blocking"
            if narration_mode in {"deferred", "deterministic", "disabled"}:
                loaded_runtime["deferred_runtime_narration"] = True
            loaded["runtime_state"] = loaded_runtime
            save_runtime_session(loaded)
    except Exception:
        logger.debug("suppressed error in %s", "_prepare_stateful_runtime_session", exc_info=True)
        return


def _stateful_runtime_performance_override(
    performance_override: dict[str, Any] | None,
    *,
    narration_mode: str,
) -> dict[str, Any]:
    merged = _disable_duplicate_runtime_first_call(performance_override)
    merged["narration_mode"] = narration_mode
    if narration_mode in {"deferred", "deterministic", "disabled"}:
        merged["enable_live_narration_llm"] = False
    return merged


def _fast_direct_action(player_input: str, performance_override: dict[str, Any] | None) -> dict[str, Any]:
    # Disabled for now.  Keep the helper as a compatibility stub for older
    # harnesses, but never bypass the first-call semantic LLM.
    return {}


def _fast_direct_diagnostics(player_input: str, action: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _FAST_DIRECT_SOURCE,
        "provider_parse_ok": True,
        "raw_text": "",
        "prompt_preview": "fast direct deterministic runtime bypass",
        "turn_grounding_packet": {
            "format_version": "ce212_fast_direct_turn_grounding_packet_v1",
            "priority_context": {"addressed_npc_ids": []},
            "player_input": _s(player_input),
            "fast_direct_action": deepcopy(_d(action)),
        },
    }


def _fast_direct_advisory(player_input: str, action: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _fast_direct_diagnostics(player_input, action)
    return {
        "action_type": _s(action.get("action_type")),
        "target_id": _s(action.get("target_id")),
        "target_name": _s(action.get("target_name")),
        "stateful": True,
        "needs_runtime_resolution": True,
        "source": _FAST_DIRECT_SOURCE,
        "first_call_grounding_diagnostics": diagnostics,
    }


def _mark_fast_direct_runtime_state(session: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    session = _d(session)
    runtime_state = _d(session.get("runtime_state"))
    runtime_state["fast_direct_runtime"] = True
    runtime_state["fast_direct_source"] = _FAST_DIRECT_SOURCE
    metadata = _d(action.get("metadata"))
    if metadata.get("skip_sync_combat_narration"):
        runtime_state["skip_sync_combat_narration"] = True
        combat_state = _d(runtime_state.get("combat_state"))
        if combat_state:
            combat_state["skip_sync_combat_narration"] = True
            combat_state["fast_direct_source"] = _FAST_DIRECT_SOURCE
            runtime_state["combat_state"] = combat_state
    session["runtime_state"] = runtime_state
    return session


def _attach_fast_direct_result(
    result: dict[str, Any],
    *,
    player_input: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    advisory = _fast_direct_advisory(player_input, action)
    diagnostics = _d(advisory.get("first_call_grounding_diagnostics"))
    result["first_call_action_advisory"] = deepcopy(advisory)
    result["first_call_semantic_advisory"] = {}
    result["first_call_visible_response_selection"] = {"reason": "fast_direct_runtime_bypass", "consumable": False}
    result["first_call_grounding_diagnostics"] = deepcopy(diagnostics)
    result["llm_called"] = False
    result["llm_purpose"] = "fast_direct_runtime"
    result["fast_direct_runtime"] = True
    result["fast_direct_source"] = _FAST_DIRECT_SOURCE
    nested = _d(result.get("result"))
    if nested:
        nested["first_call_grounding_diagnostics"] = deepcopy(diagnostics)
        nested["fast_direct_runtime"] = True
        nested["fast_direct_source"] = _FAST_DIRECT_SOURCE
        result["result"] = nested
    return result


def _fast_direct_performance_override(
    performance_override: dict[str, Any] | None,
    *,
    narration_mode: str,
) -> dict[str, Any]:
    merged = _stateful_runtime_performance_override(performance_override, narration_mode=narration_mode)
    merged["enable_live_narration_llm"] = False
    merged["enable_narration_retry"] = False
    merged["skip_sync_combat_narration"] = True
    merged["fast_direct_runtime"] = True
    return merged


def _deterministic_narration_from_result(result: dict[str, Any]) -> str:
    result = _d(result)
    nested = _d(result.get("result"))
    authoritative = _d(result.get("authoritative"))
    for source in (result, nested, authoritative):
        for key in ("deterministic_fallback_narration", "narration", "final_narration", "summary"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                text = "\n\n".join(_s(item).strip() for item in value if _s(item).strip()).strip()
                if text:
                    return text
    return ""


def _apply_stateful_narration_contract(
    result: dict[str, Any],
    *,
    narration_mode: str,
    action_advisory: dict[str, Any],
    semantic_advisory: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    nested = _d(result.get("result"))
    deterministic = _deterministic_narration_from_result(result)

    if narration_mode == "disabled":
        narration_status = "disabled"
        narration = ""
    elif narration_mode == "deterministic":
        narration_status = "deterministic"
        narration = deterministic
    elif narration_mode == "blocking":
        narration_status = _s(nested.get("narration_status") or result.get("narration_status") or "completed")
        narration = _s(nested.get("narration") or result.get("narration") or deterministic)
    else:
        narration_status = _s(nested.get("narration_status") or result.get("narration_status") or "queued")
        narration = _s(nested.get("narration") or result.get("narration") or deterministic)

    contract = {
        "format_version": "stateful_runtime_narration_contract_v1",
        "narration_mode": narration_mode,
        "stateful_runtime_authoritative": True,
        "first_call_may_resolve_state": False,
        "runtime_resolved_before_narration": True,
        "narration_may_mutate_state": False,
        "narration_status": narration_status,
        "first_call_visible_response_ignored_for_stateful": bool(
            _d(semantic_advisory).get("visible_response") or _d(action_advisory).get("visible_response")
        ),
        "first_call_selection_reason": _s(_d(selection).get("reason")),
        "first_call_grounding_diagnostics": _first_call_diagnostics(action_advisory, semantic_advisory),
    }

    result["stateful_runtime_narration_contract"] = deepcopy(contract)
    result["narration_mode"] = narration_mode
    result["narration_status"] = narration_status
    if narration_mode in {"disabled", "deterministic"}:
        result["narration"] = narration
        result["final_narration"] = narration
    if nested:
        nested["stateful_runtime_narration_contract"] = deepcopy(contract)
        nested["narration_status"] = narration_status
        if narration_mode in {"disabled", "deterministic"}:
            nested["narration"] = narration
            nested["raw_llm_narrative"] = ""
            nested["used_llm"] = False
        result["result"] = nested
    return result


def _is_nonstateful_direct_npc_dialogue(advisory: dict[str, Any]) -> bool:
    advisory = _d(advisory)
    if not advisory:
        return False
    if _b(advisory.get("stateful"), True):
        return False
    if _b(advisory.get("needs_runtime_resolution"), True):
        return False
    action_type = _s(advisory.get("action_type")).lower()
    semantic_family = _s(advisory.get("semantic_family")).lower()
    interaction_mode = _s(advisory.get("interaction_mode")).lower()
    diagnostics = _d(advisory.get("first_call_grounding_diagnostics"))
    packet = _packet_from_diagnostics(diagnostics)
    addressed_ids = _l(_d(packet.get("priority_context")).get("addressed_npc_ids"))
    return bool(
        action_type in {"social_activity", "observe", "investigate"}
        or semantic_family in {"social", "observation"}
        or interaction_mode == "direct"
        or addressed_ids
        or _s(advisory.get("target_id")).startswith("npc:")
    )


_NON_NPC_TARGET_HINTS = {
    "scene",
    "narrator",
    "narration",
    "gm",
    "game master",
    "omnix",
    "system",
    "player",
    "the player",
    "you",
}


def _direct_npc_target_hint(*advisories: dict[str, Any]) -> bool:
    for advisory in advisories:
        advisory = _d(advisory)
        action_intent = _d(advisory.get("action_intent"))
        for value in (
            advisory.get("target_id"),
            advisory.get("target_name"),
            action_intent.get("target_id"),
            action_intent.get("target_name"),
        ):
            text = _s(value).strip()
            if not text:
                continue
            normalized = text.casefold()
            if normalized.startswith("npc:"):
                normalized = normalized.split(":", 1)[1]
            normalized = normalized.replace("_", " ").strip()
            if normalized and normalized not in _NON_NPC_TARGET_HINTS:
                return True
    return False


def _is_direct_npc_question_from_packet(
    *,
    player_input: str,
    action_advisory: dict[str, Any],
    semantic_advisory: dict[str, Any],
) -> bool:
    """Heuristic safety net for malformed/default-stateful first-call outputs."""
    packet = _first_call_packet(action_advisory, semantic_advisory)
    priority = _d(packet.get("priority_context"))
    npc_context = _d(packet.get("npc_context"))
    addressed_ids = _l(priority.get("addressed_npc_ids"))
    addressed_profiles = _l(npc_context.get("addressed_npcs"))
    if (
        not addressed_ids
        and not addressed_profiles
        and not _direct_npc_target_hint(action_advisory, semantic_advisory)
    ):
        return False

    text = _s(player_input).strip().lower()
    if not text:
        return False

    question_mark = "?" in text
    question_terms = (
        "what do you think",
        "what are your thoughts",
        "your thoughts",
        "opinion",
        "do you think",
        "how do you feel",
        "tell me about",
        "can you tell me",
        "what can you tell",
        "why",
        "how",
        "what",
    )
    stateful_terms = (
        "buy ",
        "sell my",
        "sell this",
        "sell the",
        "sell a ",
        "sell an ",
        "sell you",
        "give me",
        "give ",
        "lend ",
        "borrow",
        "a" + "ttack",
        "h" + "it ",
        "s" + "tab",
        "s" + "hoot",
        "cast ",
        "take ",
        "s" + "teal",
        "travel",
        "go to",
        "hire",
        "join me",
        "come with me",
        "follow me",
        "pay ",
        "discount",
        "cheaper",
        "lower the price",
        "room",
        "bread",
        "ration",
    )
    if any(term in text for term in stateful_terms):
        return False
    return question_mark or any(term in text for term in question_terms)


def _should_safe_fallback_nonstateful_dialogue(
    action_advisory: dict[str, Any],
    semantic_advisory: dict[str, Any],
    selection: dict[str, Any],
    *,
    player_input: str = "",
    service_matched: bool = False,
) -> bool:
    if _d(selection).get("consumable"):
        return False
    if _d(selection).get("reason") == "service_or_commerce_runtime_wins":
        return False
    if _is_direct_npc_question_from_packet(
        player_input=player_input,
        action_advisory=action_advisory,
        semantic_advisory=semantic_advisory,
    ):
        return True
    if _d(selection).get("reason") != "no_safe_non_stateful_visible_response":
        return False
    if _is_nonstateful_direct_npc_dialogue(semantic_advisory) or _is_nonstateful_direct_npc_dialogue(action_advisory):
        return True
    from app.apps.rpg.session.interpretive_adjudication import should_use_interpretive_adjudication

    return should_use_interpretive_adjudication(
        player_input=player_input,
        semantic_advisory=_d(semantic_advisory) or _d(action_advisory),
        selection=_d(selection),
        service_matched=service_matched,
    )


def _direct_dialogue_fallback_topic(player_input: str) -> str:
    text = _s(player_input).lower()
    if any(term in text for term in ("who are you", "what are you", "your name", "tell me about yourself")):
        return "identity_inquiry"
    if any(
        term in text
        for term in (
            "how are you",
            "how's your day",
            "how is your day",
            "how was your day",
            "how has your day",
            "your day",
            "his day",
            "her day",
            "their day",
            "how business",
            "how's business",
            "how is business",
            "business is going",
            "business going",
            "how's it going",
            "how is it going",
            "how have you been",
            "how are things",
        )
    ):
        return "wellbeing_inquiry"
    if any(term in text for term in ("this place", "what is this place", "where are we", "where am i")):
        return "local_knowledge"
    topic_terms = (
        ("commerce_inquiry", ("food", "eat", "meal", "stew", "drink", "ale", "menu", "wares", "stock", "offer", "sell", "price", "cost", "lodging", "room")),
        ("rumor_inquiry", ("rumor", "rumour", "heard", "news", "gossip", "talk around", "word is")),
        ("combat_advice", ("sword", "blade", "combat", "fight", "fighting", "guard", "stance", "style", "shield", "parry")),
        ("local_knowledge", ("road", "roads", "bandit", "bandits", "caravan", "caravans", "where", "what can you tell", "tell me about")),
    )
    for topic, terms in topic_terms:
        if any(term in text for term in terms):
            return topic
    return "general_dialogue"


def _safe_dialogue_fallback_line(
    *,
    speaker: str,
    profile: dict[str, Any],
    player_input: str,
) -> tuple[str, str]:
    topic = _direct_dialogue_fallback_topic(player_input)
    personality = _d(profile.get("personality_profile"))
    examples = _l(personality.get("speech_examples"))
    speaker_is_bran = speaker.lower() == "bran"

    if topic == "identity_inquiry":
        if speaker_is_bran:
            return (
                "identity_inquiry",
                "I'm Bran, keeper of this tavern and the sort of innkeeper who keeps one ear on the road. "
                "Travelers bring trouble, rumors, and coin through my door, and I remember what matters.",
            )
        return ("identity_inquiry", f"I'm {speaker}, and I can answer plainly about who I am and what I know.")

    if topic == "wellbeing_inquiry":
        if speaker_is_bran:
            return (
                "wellbeing_inquiry",
                "Busy enough to keep the hearth warm and quiet enough that I can still hear myself think. "
                "That counts as a decent day here.",
            )
        return ("wellbeing_inquiry", "I am managing well enough for the moment. Thanks for asking.")

    if topic == "commerce_inquiry":
        if speaker_is_bran:
            return (
                "commerce_inquiry",
                "Food and drink here are simple traveler fare. I can talk through what is usually on hand, "
                "but exact stock and prices need checking at the bar.",
            )
        return ("commerce_inquiry", "I can talk through what is usually available, but exact stock and prices need checking first.")

    if topic == "rumor_inquiry":
        if speaker_is_bran:
            return (
                "rumor_inquiry",
                "Rumors come in with road dust and thirsty travelers. Ask plain what kind you want, "
                "and I will tell you what I have heard.",
            )
        return ("rumor_inquiry", "I hear pieces of news, but ask plainly what kind of rumor you want.")

    if topic == "combat_advice" and speaker_is_bran:
        return (
            "combat_advice",
            "Styles have their place, but keep your feet under you and your guard honest. "
            "Mud and panic teach faster than fancy forms.",
        )

    if topic == "local_knowledge":
        if speaker_is_bran:
            return (
                "local_knowledge",
                "This place is a tavern on the old road, close enough to town for news and far enough out for trouble. "
                "Caravans, guards, and tired travelers all leave pieces of the truth here.",
            )
        return ("local_knowledge", "This place is a tavern where the road meets town: travelers, guards, and local talk all pass through here.")

    if examples and topic in {"combat_advice", "general_dialogue", "local_knowledge"}:
        example = _s(examples[0]).strip()
        if example:
            return (topic, example)

    return repair_dialogue_fallback(
        topic=topic,
        line="Ask that plainly again, and I will answer as best I can.",
        speaker=speaker,
        profile=profile,
        player_input=player_input,
    )


def _safe_dialogue_fallback_result(
    *,
    session: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    player_input: str,
    action_advisory: dict[str, Any],
    semantic_advisory: dict[str, Any],
    selection: dict[str, Any],
    service_matched: bool = False,
) -> dict[str, Any]:
    from app.apps.rpg.session.interpretive_adjudication import (
        build_interpretive_adjudication_result,
        should_use_interpretive_adjudication,
    )

    if should_use_interpretive_adjudication(
        player_input=player_input,
        semantic_advisory=_d(semantic_advisory) or _d(action_advisory),
        selection=_d(selection),
        service_matched=service_matched,
    ):
        return build_interpretive_adjudication_result(
            session=session,
            simulation_state=simulation_state,
            runtime_state=runtime_state,
            player_input=player_input,
            action_advisory=action_advisory,
            semantic_advisory=semantic_advisory,
            selection=selection,
        )

    diagnostics = _first_call_diagnostics(action_advisory, semantic_advisory)
    packet = _packet_from_diagnostics(diagnostics)
    profile = _addressed_profile(packet)
    speaker = _s(profile.get("name") or _d(semantic_advisory).get("target_name") or "NPC").strip() or "NPC"
    fallback_topic, line = _safe_dialogue_fallback_line(
        speaker=speaker,
        profile=profile,
        player_input=player_input,
    )
    narration = f"{speaker} answers carefully."
    grounding_validation = {
        "ok": True,
        "selected_candidate": "primary",
        "fallback_topic": fallback_topic,
        "fallback_used": True,
        "fallback_source": "first_call_dialogue_safe_fallback_v1",
        "violations": [],
        "primary_violations": [],
        "first_call_grounding_packet_version": _s(packet.get("format_version")),
        "first_call_addressed_npc_ids": _l(_d(packet.get("priority_context")).get("addressed_npc_ids")),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "turn_grounding_packet": deepcopy(packet),
        "source": "first_call_dialogue_safe_fallback_v1",
    }
    visible_response = {"narration": narration, "npc": {"speaker": speaker, "line": line}}
    resolved_result = {
        "ok": True,
        "action_type": "npc_interpretive_dialogue",
        "semantic_action_type": "npc_interpretive_dialogue",
        "semantic_family": "social",
        "stateful": False,
        "needs_runtime_resolution": False,
        "visible_interaction_reason": "first_call_safe_dialogue_fallback",
        "outcome": "safe_non_stateful_dialogue_fallback",
        "summary": narration,
        "npc": deepcopy(visible_response["npc"]),
        "visible_response": deepcopy(visible_response),
        "fallback_topic": fallback_topic,
        "first_call_visible_response_selection": deepcopy(selection),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "grounding_validation": deepcopy(grounding_validation),
        "source": "first_call_dialogue_safe_fallback_v1",
    }
    result = {
        "consumed": True,
        "ok": True,
        "result": deepcopy(resolved_result),
        "resolved_result": deepcopy(resolved_result),
        "narration": narration,
        "final_narration": narration,
        "summary": narration,
        "npc": deepcopy(visible_response["npc"]),
        "visible_response": deepcopy(visible_response),
        "first_call_visible_response_selection": deepcopy(selection),
        "first_call_action_advisory": deepcopy(action_advisory),
        "first_call_semantic_advisory": deepcopy(semantic_advisory),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "grounding_validation": deepcopy(grounding_validation),
        "llm_called": True,
        "llm_purpose": "first_call_safe_dialogue_fallback",
        "stateful": False,
        "needs_runtime_resolution": False,
        "simulation_state": deepcopy(_d(simulation_state)),
        "runtime_state": deepcopy(_d(runtime_state)),
        "session": deepcopy(_d(session)),
        "player_input": _s(player_input),
        "source": "first_call_dialogue_safe_fallback_v1",
    }
    from .narrative_engine_bridge import canonicalize_direct_dialogue_result

    manifest = _d(session.get("manifest"))
    session_id = _s(
        manifest.get("session_id")
        or manifest.get("id")
        or session.get("session_id")
        or session.get("id")
        or "runtime"
    )
    return canonicalize_direct_dialogue_result(
        result,
        session_id=session_id,
        player_input=_s(player_input),
    )


def _apply_turn_core(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    session_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interactive CLI two-call entry point."""

    manual_start = perf_counter()
    recursion_limit = _ensure_runtime_recursion_budget()
    timing: dict[str, Any] = {
        "manual_turn_timing_source": "interactive_first_call_runtime_v1",
        "runtime_recursion_limit": recursion_limit,
        "pre_runtime_intent_llm_ms": 0.0,
        "deterministic_runtime_apply_ms": 0.0,
        "grounding_validation_ms": 0.0,
        "repair_ms": 0.0,
        "state_snapshot_ms": 0.0,
        "deferred_enqueue_ms": 0.0,
    }
    snapshot_start = perf_counter()
    session = _select_session(session_id, session_override=session_override)
    timing["state_snapshot_ms"] += _ms_since(snapshot_start)
    if not session:
        timing["manual_turn_ms"] = _ms_since(manual_start)
        return {"ok": False, "error": "session_not_found", "manual_turn_stage_timing": timing}

    session = hydrate_simulation_player(session)
    simulation_state = _d(session.get("simulation_state"))
    runtime_state = _d(session.get("runtime_state"))
    candidate_action = _d(action)

    runtime_state["first_call_shortcuts_disabled"] = True
    session["runtime_state"] = runtime_state
    fast_direct_action = _fast_direct_action(_s(player_input), performance_override)
    if fast_direct_action:
        narration_mode = _narration_mode(performance_override, runtime_state)
        session = _mark_fast_direct_runtime_state(session, fast_direct_action)
        prepare_start = perf_counter()
        _prepare_stateful_runtime_session(session_id, session, narration_mode=narration_mode)
        timing["state_snapshot_ms"] += _ms_since(prepare_start)
        runtime_start = perf_counter()
        result = _apply_composed_turn(
            session_id=session_id,
            player_input=_s(player_input),
            action=fast_direct_action,
            performance_override=_fast_direct_performance_override(
                performance_override,
                narration_mode=narration_mode,
            ),
        )
        timing["deterministic_runtime_apply_ms"] = _ms_since(runtime_start)
        if isinstance(result, dict):
            result = _attach_fast_direct_result(
                result,
                player_input=_s(player_input),
                action=fast_direct_action,
            )
            advisory = _fast_direct_advisory(_s(player_input), fast_direct_action)
            contract_start = perf_counter()
            result = _apply_stateful_narration_contract(
                result,
                narration_mode=narration_mode,
                action_advisory=advisory,
                semantic_advisory={},
                selection={"reason": "fast_direct_runtime_bypass", "consumable": False},
            )
            timing["grounding_validation_ms"] += _ms_since(contract_start)
            timing["deferred_enqueue_ms"] = _safe_deferred_enqueue_ms(result)
        timing["manual_turn_ms"] = _ms_since(manual_start)
        return _attach_manual_stage_timing(result, timing) if isinstance(result, dict) else result

    try:
        service_match = resolve_service_turn(
            player_input=_s(player_input),
            action=candidate_action,
            resolved_action={},
            simulation_state=simulation_state,
            runtime_state=runtime_state,
        )
    except Exception:
        service_match = {}
    service_matched = bool(_d(service_match).get("matched"))

    action_advisory: dict[str, Any] = {}
    semantic_advisory: dict[str, Any] = {}
    llm_start = perf_counter()
    try:
        gateway = build_app_llm_gateway()
        semantic_advisory = build_compact_dialogue_advisory(
            llm_gateway=gateway,
            player_input=_s(player_input),
            simulation_state=simulation_state,
            runtime_state=runtime_state,
            candidate_action=candidate_action,
            public_state=_d(session.get("state")),
        )
        if not semantic_advisory and not service_matched:
            semantic_advisory = get_semantic_action_advisory(
            llm_gateway=gateway,
            player_input=_s(player_input),
            simulation_state=simulation_state,
            runtime_state=runtime_state,
            candidate_action=candidate_action,
            )
    except Exception as exc:
        runtime_state["first_call_grounding_error"] = f"{type(exc).__name__}: {exc}"
    timing["pre_runtime_intent_llm_ms"] = _ms_since(llm_start)

    validation_start = perf_counter()
    non_stateful_result = build_non_stateful_dialogue_result(
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        player_input=_s(player_input),
        action_advisory=action_advisory,
        semantic_advisory=semantic_advisory,
        service_matched=service_matched,
    )
    timing["grounding_validation_ms"] += _ms_since(validation_start)
    if non_stateful_result.get("consumed"):
        non_stateful_result["turn_id"] = _build_turn_id(runtime_state)
        non_stateful_result["tick"] = int(runtime_state.get("tick", 0) or 0)
        non_stateful_result["first_call_action_advisory"] = action_advisory
        non_stateful_result["first_call_semantic_advisory"] = semantic_advisory
        non_stateful_result["first_call_grounding_diagnostics"] = _d(
            semantic_advisory.get("first_call_grounding_diagnostics")
            or action_advisory.get("first_call_grounding_diagnostics")
            or non_stateful_result.get("first_call_grounding_diagnostics")
        )
        timing["manual_turn_ms"] = _ms_since(manual_start)
        return _attach_manual_stage_timing(non_stateful_result, timing)

    selection = _d(non_stateful_result.get("selection"))
    repair_start = perf_counter()
    should_fallback = _should_safe_fallback_nonstateful_dialogue(
        action_advisory,
        semantic_advisory,
        selection,
        player_input=_s(player_input),
        service_matched=service_matched,
    )
    timing["repair_ms"] += _ms_since(repair_start)
    if should_fallback:
        repair_start = perf_counter()
        fallback = _safe_dialogue_fallback_result(
            session=session,
            simulation_state=simulation_state,
            runtime_state=runtime_state,
            player_input=_s(player_input),
            action_advisory=action_advisory,
            semantic_advisory=semantic_advisory,
            selection=selection,
            service_matched=service_matched,
        )
        timing["repair_ms"] += _ms_since(repair_start)
        fallback["turn_id"] = _build_turn_id(runtime_state)
        fallback["tick"] = int(runtime_state.get("tick", 0) or 0)
        timing["manual_turn_ms"] = _ms_since(manual_start)
        return _attach_manual_stage_timing(fallback, timing)

    first_call_action = _stateful_action_from_first_call(action_advisory, semantic_advisory)
    if not first_call_action:
        first_call_action = candidate_action
    if service_matched:
        first_call_action = service_action_from_result(
            _s(player_input),
            first_call_action,
            service_match,
        )
    elif semantic_advisory:
        first_call_action = attach_semantic_interaction(
            first_call_action,
            semantic_advisory,
            player_input=_s(player_input),
        )

    narration_mode = _narration_mode(performance_override, runtime_state)
    prepare_start = perf_counter()
    _prepare_stateful_runtime_session(session_id, session, narration_mode=narration_mode)
    timing["state_snapshot_ms"] += _ms_since(prepare_start)

    runtime_start = perf_counter()
    result = _apply_composed_turn(
        session_id=session_id,
        player_input=_s(player_input),
        action=first_call_action,
        performance_override=_stateful_runtime_performance_override(
            performance_override,
            narration_mode=narration_mode,
        ),
    )
    timing["deterministic_runtime_apply_ms"] = _ms_since(runtime_start)
    if isinstance(result, dict):
        result["first_call_action_advisory"] = action_advisory
        result["first_call_semantic_advisory"] = semantic_advisory
        result["first_call_visible_response_selection"] = selection
        result["first_call_grounding_diagnostics"] = _first_call_diagnostics(action_advisory, semantic_advisory)
        contract_start = perf_counter()
        result = _apply_stateful_narration_contract(
            result,
            narration_mode=narration_mode,
            action_advisory=action_advisory,
            semantic_advisory=semantic_advisory,
            selection=selection,
        )
        timing["grounding_validation_ms"] += _ms_since(contract_start)
        timing["deferred_enqueue_ms"] = _safe_deferred_enqueue_ms(result)
    timing["manual_turn_ms"] = _ms_since(manual_start)
    return _attach_manual_stage_timing(result, timing) if isinstance(result, dict) else result


def apply_turn(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    session_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the interactive turn through the explicit ordered stage pipeline."""

    from app.apps.rpg.session.pipeline import TurnContext, run_turn_pipeline

    context = TurnContext(
        session_id=session_id,
        player_input=player_input,
        action=action,
        performance_override=performance_override,
        session_override=session_override,
        execute_core=lambda: _apply_turn_core(
            session_id,
            player_input,
            action,
            performance_override=performance_override,
            session_override=session_override,
        ),
    )
    return run_turn_pipeline(context).result


def _safe_deferred_enqueue_ms(result: dict[str, Any]) -> float:
    for source in (result, _d(result.get("result")), _d(result.get("turn_runtime")), _d(result.get("performance"))):
        for key in ("deferred_enqueue_ms", "background_enqueue_ms"):
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return round(float(value), 3)
    return 0.0
