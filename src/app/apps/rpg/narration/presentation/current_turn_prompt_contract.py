from __future__ import annotations
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract', "1", "resolve_short_followup_against_immediately_previous_topic")
_PROMPT_2 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_2', "1", "resolve_short_followup_against_immediately_previous_topic")
_PROMPT_3 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_3', "1", "answer_causal_why_or_state_unknown_cause_with_grounded_lead")
_PROMPT_4 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_4', "1", "deterministic_code_only_vetoes_impossible_service_or_economy")
_PROMPT_5 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_5', "1", "service_economy_categories_blocked_without_authoritative_contract_support")
_PROMPT_6 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_6', "1", "do_not_repeat_old_investigation_threads_as_the_current_answer")
_PROMPT_7 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_7', "1", "do_not_treat_profile_memory_as_new_current_turn_outcome")
_PROMPT_8 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_8', "1", "do_not_offer_drinks_rooms_or_prices_unless_current_action_explicitly_buys_or_rents")
_PROMPT_9 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_9', "1", "do_not_repeat_the_previous_npc_answer_when_the_current_player_action_asks_a_new_question")
_PROMPT_10 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_10', "1", "answer_causal_why_or_state_unknown_cause_with_grounded_lead")
_PROMPT_11 = prompt_template('rpg.presentation_current_turn_prompt_contract.build_runtime_current_turn_prompt_contract_11', "1", "document_evidence_without_explicit_service_request")

"""Runtime current-turn presentation prompt contract.

N117.0 promotes the battle-tested autoplay prompt-contract shape into the RPG
runtime.  The contract is intentionally bounded: simulation/turn-contract data is
truth, NPC profile data is tone/continuity only, and deterministic code only
vetoes impossible service/economy framing rather than positively classifying the
whole turn.
"""

import json
import re
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _short(value: Any, limit: int = 400) -> str:
    text = _safe_str(value).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "...[truncated]"
    return text


def _norm(value: Any) -> str:
    text = _safe_str(value).lower().strip()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_final_question(action_text: Any) -> str:
    """Return the latest/current question embedded in the player's turn.

    A player often acknowledges the previous NPC line and then asks a new
    question in the same utterance, e.g. "definitely better than nothing, any
    troubles lately?".  The narrator must answer that final question, not the
    older topic that appears in recent conversation context.
    """

    text = " ".join(_safe_str(action_text).split()).strip()
    if not text:
        return ""

    # Prefer the final explicit question mark span.
    if "?" in text:
        before = text.rsplit("?", 1)[0]
        start = max(before.rfind("."), before.rfind("!"), before.rfind(";"), before.rfind("\n"))
        question = before[start + 1 :].strip(" ,:;-'\"")
        if question:
            return _short(question + "?", 300)

    # Also catch question fragments when speech input omitted punctuation.
    lowered = text.lower()
    starters = (
        "any ",
        "what ",
        "where ",
        "when ",
        "why ",
        "how ",
        "who ",
        "is there ",
        "are there ",
        "do you ",
        "did you ",
        "have you ",
        "can you ",
        "could you ",
    )
    best = -1
    for starter in starters:
        idx = lowered.rfind(starter)
        if idx > best:
            best = idx
    if best >= 0:
        return _short(text[best:].strip(" ,:;-'\"") + "?", 300)
    return ""


_FOLLOWUP_STARTERS = (
    "why",
    "but why",
    "and why",
    "do you know why",
    "but do you know why",
    "how so",
    "what do you mean",
    "what happened",
    "tell me more",
    "about that",
    "what about that",
    "can you explain",
    "could you explain",
)


def _is_short_followup_question(question_text: Any) -> bool:
    question_n = _norm(question_text)
    if not question_n or len(question_n.split()) > 12:
        return False
    return any(question_n == starter or question_n.startswith(starter + " ") for starter in _FOLLOWUP_STARTERS)


_DOCUMENT_EVIDENCE_TERMS = (
    "sealed order",
    "sealed orders",
    "captured order",
    "captured orders",
    "written order",
    "written orders",
    "route paper",
    "route papers",
    "ledger",
    "ledger entry",
    "ledger entries",
    "manifest",
    "manifest mark",
    "manifest marks",
    "payment mark",
    "payment marks",
    "marked coin",
    "marked coin proof",
    "coin proof",
    "route cipher",
    "coded message",
    "coded messages",
    "toll marker",
    "toll markers",
    "courier route",
    "paymaster",
    "proof",
    "evidence",
)

_EXPLICIT_SERVICE_TERMS = (
    "buy",
    "purchase",
    "pay for",
    "rent",
    "hire",
    "order a drink",
    "order ale",
    "order beer",
    "order wine",
    "order a meal",
    "book a room",
    "get a room",
    "sell",
    "trade",
    "repair my",
)


def is_document_evidence_without_explicit_service(action_text: Any) -> bool:
    text = _safe_str(action_text).lower()
    if not text:
        return False
    if any(term in text for term in _EXPLICIT_SERVICE_TERMS):
        return False
    return any(term in text for term in _DOCUMENT_EVIDENCE_TERMS)


def _turn_contract_from_context(narration_context: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(
        narration_context.get("turn_contract")
        or _safe_dict(narration_context.get("resolved_result")).get("turn_contract")
    )


def _player_action_from_context(narration_context: Dict[str, Any]) -> str:
    turn_contract = _turn_contract_from_context(narration_context)
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    action = (
        narration_context.get("player_input")
        or narration_context.get("player_action")
        or interpreted.get("input")
        or interpreted.get("text")
        or interpreted.get("action")
        or _safe_dict(narration_context.get("resolved_result")).get("player_input")
    )
    return _short(action, 600)


def _service_result_from_context(narration_context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _safe_dict(narration_context.get("resolved_result"))
    turn_contract = _turn_contract_from_context(narration_context)
    candidates = (
        narration_context.get("service_result"),
        resolved.get("service_result"),
        resolved.get("service"),
        turn_contract.get("service_result"),
        _safe_dict(turn_contract.get("mechanics")).get("service_result"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _compact_service_result(service_result: Dict[str, Any], *, action_text: str) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    if not service_result:
        return {}

    compact = {
        "matched": bool(service_result.get("matched")),
        "kind": _safe_str(service_result.get("kind")),
        "status": _safe_str(service_result.get("status")),
        "service_kind": _safe_str(service_result.get("service_kind") or service_result.get("kind")),
        "provider_name": _safe_str(
            _safe_dict(service_result.get("provider")).get("name")
            or service_result.get("provider_name")
        ),
        "reason": _safe_str(service_result.get("reason")),
    }
    if is_document_evidence_without_explicit_service(action_text) and compact["matched"]:
        return {
            "matched": False,
            "kind": "not_service",
            "status": "service_false_positive_vetoed",
            "reason": "document_evidence_without_explicit_service_request",
            "original_service_result": compact,
            "veto_only": True,
            "forced_positive_classification": False,
        }
    return compact


def _required_focus_for_action(
    *,
    action_text: str,
    turn_contract: Dict[str, Any],
    service_result: Dict[str, Any],
) -> List[str]:
    action_n = _norm(action_text)
    resolved_n = _norm(
        _safe_dict(turn_contract.get("resolved_result")).get("summary")
        or turn_contract.get("narration_brief")
        or turn_contract.get("action_result")
    )
    focus: List[str] = []

    def add(item: str) -> None:
        if item and item not in focus:
            focus.append(item)

    document_evidence = is_document_evidence_without_explicit_service(action_text)
    service_matched = bool(_safe_dict(service_result).get("matched"))
    final_question = _extract_final_question(action_text)

    if document_evidence:
        add("answer_current_evidence_document_or_route_context_first")
        add("do_not_treat_document_order_words_as_shop_or_drink_orders")
        add("llm_classifies_presentation_intent")
        add("deterministic_code_only_vetoes_impossible_service_or_economy")
    elif service_matched:
        add("acknowledge_the_service_or_economy_request_first")
        add("mention_item_quantity_price_or_refusal_only_if_present_in_contract")

    if final_question:
        add("answer_the_final_question_in_player_action_first")
        add("do_not_answer_an_older_question_from_conversation_history")
    if any(term in action_n for term in ("ask", "question", "tell", "report", "warn", "explain")):
        add("answer_current_question")
    if any(term in action_n for term in ("look", "inspect", "search", "scout", "examine", "listen", "study", "review", "decode", "trace")):
        add("observed_evidence_or_limits")
    if any(term in action_n for term in ("travel", "follow", "leave", "go to", "road", "route", "courier")) or "travel" in resolved_n:
        add("current_travel_or_route_action")
    if any(term in action_n for term in ("attack", "strike", "fight", "combat", "defend")):
        add("combat_claims_must_match_authoritative_combat_facts")

    return focus[:10]


def _compact_turn_contract(turn_contract: Dict[str, Any]) -> Dict[str, Any]:
    turn_contract = _safe_dict(turn_contract)
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    return {
        "format_version": _safe_str(turn_contract.get("format_version")) or "runtime_turn_contract_compact_v1",
        "narration_brief": _short(turn_contract.get("narration_brief"), 800),
        "interpreted_action": {
            "intent": _safe_str(interpreted.get("intent")),
            "target_id": _safe_str(interpreted.get("target_id")),
            "target_name": _safe_str(interpreted.get("target_name")),
            "followup_reference": _compact_followup_reference(_safe_dict(interpreted.get("followup_reference"))),
        },
        "state_delta": _safe_dict(turn_contract.get("state_delta")),
        "combat_result": _safe_dict(turn_contract.get("combat_result")),
        "reward": turn_contract.get("reward"),
    }


def _compact_followup_reference(reference: Dict[str, Any]) -> Dict[str, Any]:
    reference = _safe_dict(reference)
    if not reference:
        return {}
    return {
        "target_id": _safe_str(reference.get("target_id")),
        "target_name": _safe_str(reference.get("target_name")),
        "topic": _short(reference.get("topic") or reference.get("previous_player_input"), 300),
        "source": _safe_str(reference.get("source")),
    }


def _followup_reference_from_context(
    narration_context: Dict[str, Any],
    *,
    current_question: str,
    turn_contract: Dict[str, Any],
) -> Dict[str, Any]:
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    direct = _compact_followup_reference(_safe_dict(interpreted.get("followup_reference")))
    if direct:
        return direct
    if not _is_short_followup_question(current_question):
        return {}

    runtime_state = _safe_dict(narration_context.get("runtime_state"))
    candidates: List[Dict[str, Any]] = []
    for key in ("last_player_action", "last_player_action_context"):
        row = _safe_dict(runtime_state.get(key))
        if row:
            candidates.append(row)
    last_turn = _safe_dict(runtime_state.get("last_turn_result"))
    if last_turn:
        candidates.append(last_turn)
        for key in ("semantic_action", "action", "resolved_result"):
            row = _safe_dict(last_turn.get(key))
            if row:
                candidates.append(row)

    for row in candidates:
        target_id = _safe_str(row.get("target_id") or row.get("npc_id")).strip()
        target_name = _safe_str(row.get("target_name") or row.get("npc_name")).strip()
        topic = _short(row.get("text") or row.get("player_input") or row.get("summary") or row.get("message"), 300)
        if target_id or target_name or topic:
            return {
                "target_id": target_id,
                "target_name": target_name,
                "topic": topic,
                "source": "runtime_recent_dialogue_followup",
            }
    return {}


def _compact_loaded_npc_profiles(narration_context: Dict[str, Any], limit: int = 6) -> Dict[str, Any]:
    runtime_state = _safe_dict(narration_context.get("runtime_state"))
    npc_evolution = _safe_dict(runtime_state.get("npc_evolution"))
    loaded = _safe_dict(npc_evolution.get("loaded_profiles"))

    # Runtime narration paths may provide a direct summary instead of the
    # autoplay-style loaded profile map.
    direct_profile = _safe_dict(narration_context.get("npc_profile_summary"))
    if direct_profile and not loaded:
        key = _safe_str(direct_profile.get("npc_id") or direct_profile.get("id") or direct_profile.get("name") or "target_npc")
        loaded = {key: {"profile": direct_profile}}

    out: Dict[str, Any] = {}
    for npc_id, row_any in list(loaded.items())[:limit]:
        row = _safe_dict(row_any)
        profile = _safe_dict(row.get("profile") or row)
        out[str(npc_id)] = {
            "name": _safe_str(profile.get("name") or profile.get("display_name") or npc_id),
            "role": _safe_str(profile.get("role") or profile.get("occupation")),
            "arc_stage": _safe_str(profile.get("arc_stage") or "stable"),
            "persona": _safe_dict(profile.get("persona") or profile.get("personality")),
            "axes": _safe_dict(profile.get("axes")),
            "memories": _safe_list(profile.get("memories"))[-4:],
            "milestones": _safe_list(profile.get("milestones"))[-3:],
            "future_hooks": _safe_list(profile.get("future_hooks"))[-3:],
            "world_signals": _safe_list(profile.get("world_signals"))[-3:],
        }
    return out


def build_runtime_current_turn_prompt_contract(
    *,
    scene: Dict[str, Any] | None = None,
    narration_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    scene = _safe_dict(scene)
    narration_context = _safe_dict(narration_context)
    turn_contract = _turn_contract_from_context(narration_context)
    action_text = _player_action_from_context(narration_context)
    current_question = _extract_final_question(action_text)
    service_result = _compact_service_result(
        _service_result_from_context(narration_context),
        action_text=action_text,
    )
    followup_reference = _followup_reference_from_context(
        narration_context,
        current_question=current_question,
        turn_contract=turn_contract,
    )
    required_focus = _required_focus_for_action(
        action_text=action_text,
        turn_contract=turn_contract,
        service_result=service_result,
    )
    if followup_reference and _PROMPT_1.text not in required_focus:
        required_focus.append(_PROMPT_2.text)
    if followup_reference and _norm(current_question).startswith(("why", "but why", "do you know why", "but do you know why")):
        if _PROMPT_3.text not in required_focus:
            required_focus.append(_PROMPT_10.text)
    document_service_veto = is_document_evidence_without_explicit_service(action_text)

    return {
        "format_version": "runtime_current_turn_prompt_contract_v1",
        "source": "runtime_presentation_pipeline",
        "player_action": action_text,
        "current_question": current_question,
        "required_response": {
            "must_answer_current_question": bool(current_question),
            "question_text": current_question,
            "answer_priority": "answer_this_before_recent_lines_or_profile_memory" if current_question else "current_action_first",
            "do_not_answer_older_question_from_history": bool(current_question),
            "followup_reference": followup_reference,
            "must_resolve_short_followup": bool(followup_reference),
        },
        "scene": {
            "title": _safe_str(scene.get("title")),
            "location": _safe_str(scene.get("location_name") or scene.get("location_id")),
            "present_actors": _safe_list(scene.get("actors"))[:8],
        },
        "turn_contract": _compact_turn_contract(turn_contract),
        "service_result": service_result,
        "required_focus": required_focus,
        "classification_policy": {
            "llm_classifies_presentation_intent": True,
            _PROMPT_4.text: True,
            _PROMPT_5.text: True,
            "veto_only_no_forced_positive_classification": True,
        },
        "forbidden_stale_topics": [
            _PROMPT_6.text,
            _PROMPT_7.text,
            _PROMPT_8.text,
            _PROMPT_9.text,
        ],
        "service_resolver_veto": {
            "service_false_positive_vetoed": bool(document_service_veto and _safe_dict(service_result).get("status") == "service_false_positive_vetoed"),
            "reason": _PROMPT_11.text if document_service_veto else "",
            "veto_only": True,
            "forced_positive_classification": False,
        },
        "npc_profile_context": {
            "usage": "tone_and_continuity_only",
            "loaded_npc_profiles": _compact_loaded_npc_profiles(narration_context),
        },
    }


def format_runtime_prompt_contract_block(contract: Dict[str, Any], max_chars: int = 7000) -> str:
    text = json.dumps(_safe_dict(contract), ensure_ascii=False, indent=2, default=str)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n...[truncated]"
    return text
