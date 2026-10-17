from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""RPG session runtime responsibility module."""

from copy import deepcopy
from typing import Any, Iterable

from app.apps.rpg.narration.response_generation.strict_pipeline import StrictRpgProductionResponsePipeline
from app.apps.rpg.rules.economy.service_resolver import resolve_service_turn
from app.apps.rpg.rules.economy.currency import format_currency
from app.apps.rpg.session.public_state_bridge import (
    hydrate_simulation_player,
    project_authoritative_player,
)
from app.apps.rpg.session.service_runtime import (
    service_action_from_result,
    service_authoritative_result,
)

from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict,
)
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session,
)
from app.apps.rpg.session.semantic_direct_response import (
    _phase8_part38_iter_candidate_sources as _phase8_part38_iter_candidate_sources,
)
from app.apps.rpg.session.llm_narration_projection import (
    _phase8_part31_iter_payload_dicts as _phase8_part31_iter_payload_dicts,
)

from .semantic_direct_response import (
    _patch_semantic_direct_response as _PHASE8_PART39_BASE_APPLY_TURN_AUTHORITATIVE,
)
from .player_turn_execution import apply_turn as _PHASE8_PART39_BASE_APPLY_TURN
from app.apps.rpg.foundation.safe_values import copy_state

_PHASE8_PART39_SOURCE = "phase8_social_claim_travel_mismatch_guard_v1"
_CANONICAL_PUBLICATION_PIPELINE = StrictRpgProductionResponsePipeline()
_SOCIAL_ACTIONS = {
    "social_activity",
    "social_affection",
    "social_competition",
    "social_performance",
    "persuade",
    "deceive",
    "intimidate",
}
_TRAVEL_ACTIONS = {"exploration", "travel"}
_ACHIEVEMENT_VERBS = {
    "beat",
    "defeat",
    "defeated",
    "kill",
    "killed",
    "slay",
    "slayed",
    "slew",
    "vanquish",
    "vanquished",
}
_CLAIM_MARKERS = {
    "i was able to",
    "i have",
    "i had",
    "i killed",
    "i slew",
    "i defeated",
    "i beat",
    "i vanquished",
    "we killed",
    "we slew",
    "we defeated",
    "we beat",
    "we vanquished",
    "i claim",
    "i report",
    "i announce",
    "i tell",
    "i say",
}


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in {"", "[]", "{}", "null", "none", "false", "true"} else text


def _norm(value: Any) -> str:
    text = _clean(value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return default if value is None else bool(value)


def _iter_sources(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    seen: set[int] = set()
    for source in _phase8_part38_iter_candidate_sources(_safe_dict(payload)):
        if isinstance(source, dict) and id(source) not in seen:
            seen.add(id(source))
            yield source
    try:
        for source in _phase8_part31_iter_payload_dicts(_safe_dict(payload)):
            if isinstance(source, dict) and id(source) not in seen:
                seen.add(id(source))
                yield source
    except Exception:
        logger.debug("suppressed error in %s", "_iter_sources", exc_info=True)
        return


def _source_field(source: dict[str, Any], key: str) -> str:
    source = _safe_dict(source)
    semantic = _safe_dict(source.get("semantic_advisory"))
    intent = _safe_dict(source.get("action_intent"))
    return _clean(source.get(key) or semantic.get(key) or intent.get(key))


def _source_text(source: dict[str, Any], player_input: str) -> str:
    from app.apps.rpg.session.state_normalization import (
        _safe_list as _safe_list,
    )

    source = _safe_dict(source)
    semantic = _safe_dict(source.get("semantic_advisory"))
    visible = _safe_dict(source.get("visible_response") or source.get("final_narration_candidate"))
    npc = _safe_dict(visible.get("npc"))
    pieces = [
        player_input,
        source.get("activity_label"),
        source.get("intent_summary"),
        source.get("reason"),
        semantic.get("activity_label"),
        semantic.get("intent_summary"),
        visible.get("narration"),
        npc.get("line"),
        *_safe_list(source.get("evidence_spans")),
        *_safe_list(semantic.get("evidence_spans")),
    ]
    return _norm(" ".join(_clean(piece) for piece in pieces))


def _is_social_claim(source: dict[str, Any], player_input: str) -> bool:
    source = _safe_dict(source)
    semantic = _safe_dict(source.get("semantic_advisory"))
    action_type = _source_field(source, "action_type").casefold()
    family = _source_field(source, "semantic_family").casefold()
    risk = _source_field(source, "risk_domain").casefold()
    activity = _source_field(source, "activity_label").casefold()
    utterance = _source_field(source, "utterance_mode").casefold()
    literal = _bool(
        semantic.get("literal_action_requested", source.get("literal_action_requested")),
        False,
    )
    if not (
        family == "social"
        or action_type in _SOCIAL_ACTIONS
        or risk in {"social", "relationship_change", "social_reputation"}
    ):
        return False
    text = _source_text(source, player_input)
    has_claim = any(marker in text for marker in _CLAIM_MARKERS)
    has_achievement = any(verb in text.split() for verb in _ACHIEVEMENT_VERBS)
    declarative = (
        utterance in {"declarative", "report", "reporting", "statement"}
        or "report" in activity
        or "claim" in activity
    )
    return bool((has_claim or declarative) and has_achievement and not literal)


def _has_travel_mismatch(payload: dict[str, Any]) -> bool:
    for source in _iter_sources(payload):
        action_type = _source_field(source, "action_type").casefold()
        travel = _safe_dict(source.get("travel_result"))
        text = _norm(
            " ".join(
                _clean(source.get(key))
                for key in (
                    "action",
                    "narration",
                    "final_narration",
                    "summary",
                    "outcome",
                    "visible_interaction_reason",
                )
            )
        )
        if action_type in _TRAVEL_ACTIONS and (
            travel or "travel" in text or "moving from" in text or "village square" in text
        ):
            return True
        if travel and travel.get("matched") is not False:
            return True
    return False


def _guard_fields(player_input: str, claim_source: dict[str, Any]) -> dict[str, Any]:
    utterance = _clean(player_input) or "I report an accomplishment."
    narration = (
        f'You say, "{utterance}" The statement is treated as an unverified claim '
        "heard in the current scene, not as confirmation that the event happened. "
        "No travel, combat victory, reward, quest progress, or world-fact mutation is applied."
    )
    guard = {
        "format_version": "social_claim_runtime_guard_v1",
        "source": _PHASE8_PART39_SOURCE,
        "reason": "social_claim_must_not_fall_through_to_travel",
        "claim_veracity": "unverified",
        "verified_world_fact": False,
        "original_semantic_action_type": _source_field(claim_source, "action_type"),
        "original_activity_label": _source_field(claim_source, "activity_label"),
    }
    return {
        "narration": narration,
        "final_narration": narration,
        "raw_payload_narration": narration,
        "deterministic_fallback_narration": narration,
        "summary": narration,
        "action": "Social claim recorded as an unverified statement; no travel is performed.",
        "action_type": "social_activity",
        "semantic_action_type": "social_activity",
        "semantic_family": "social",
        "activity_label": "unverified_accomplishment_claim",
        "state_mutation_requested": False,
        "claim_veracity": "unverified",
        "verified_world_fact": False,
        "travel_result": {
            "matched": False,
            "status": "blocked",
            "reason": "social_claim_not_travel",
            "source": _PHASE8_PART39_SOURCE,
        },
        "social_claim_guard": guard,
        "narration_status": "completed",
        "used_llm": True,
        "llm_called": True,
        "llm_purpose": "semantic_social_claim_guard",
        "fallback_narration_source": _PHASE8_PART39_SOURCE,
        "skip_full_structured_narrator": True,
        "npc": {},
    }


def _patch_target(target: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    patched = dict(_safe_dict(target))
    patched.update(deepcopy(fields))
    semantic_action = _safe_dict(
        patched.get("semantic_action") or patched.get("semantic_action_record")
    )
    if semantic_action:
        semantic_action = dict(semantic_action)
        semantic_action.update(
            {
                "state_mutation_requested": False,
                "claim_veracity": "unverified",
                "verified_world_fact": False,
            }
        )
        patched["semantic_action"] = semantic_action
    return patched


def _phase8_part39_patch_social_claim_mismatch(
    payload: Any,
    *,
    player_input: str,
) -> Any:
    if not isinstance(payload, dict):
        return payload
    claim_source = next(
        (source for source in _iter_sources(payload) if _is_social_claim(source, player_input)),
        {},
    )
    if not claim_source or not _has_travel_mismatch(payload):
        return payload
    fields = _guard_fields(player_input, claim_source)
    patched = _patch_target(payload, fields)
    for key in ("result", "authoritative", "resolved_result", "payload"):
        nested = _safe_dict(patched.get(key))
        if nested:
            patched[key] = _patch_target(nested, fields)
    context = _safe_dict(patched.get("narration_context"))
    if context:
        context = dict(context)
        resolved = _safe_dict(context.get("resolved_result"))
        if resolved:
            context["resolved_result"] = _patch_target(resolved, fields)
        context["social_claim_guard"] = deepcopy(fields["social_claim_guard"])
        patched["narration_context"] = context
    if not _safe_dict(patched.get("result")):
        patched["result"] = {
            key: value for key, value in patched.items() if key != "authoritative"
        }
    if not _safe_dict(patched.get("authoritative")):
        patched["authoritative"] = dict(_safe_dict(patched.get("result")))
    return patched


def _apply_turn_authoritative(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART39_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    from app.apps.rpg.session.session_runtime_store import (
        save_runtime_session as save_runtime_session,
    )

    guarded_action = _safe_dict(action)
    service_result: dict[str, Any] = {}
    session = hydrate_simulation_player(
        copy_state(_safe_dict(load_runtime_session(session_id)))
    )
    if session:
        service_result = resolve_service_turn(
            player_input=player_input,
            action=guarded_action,
            resolved_action={},
            simulation_state=_safe_dict(session.get("simulation_state")),
            runtime_state=_safe_dict(session.get("runtime_state")),
        )
        if service_result.get("matched"):
            guarded_action = service_action_from_result(
                player_input,
                guarded_action,
                service_result,
            )
    payload = _base_authoritative(
        session_id,
        player_input,
        guarded_action,
        performance_override=performance_override,
    )
    if service_result.get("matched") and not _service_postcondition_satisfied(
        payload,
        session_id=session_id,
        service_result=service_result,
    ):
        latest_session = hydrate_simulation_player(
            copy_state(_safe_dict(load_runtime_session(session_id)))
        )
        if latest_session:
            authoritative = service_authoritative_result(
                _safe_dict(latest_session.get("simulation_state")),
                guarded_action,
            )
            latest_session["simulation_state"] = _safe_dict(
                authoritative.get("simulation_state")
            )
            latest_session = project_authoritative_player(latest_session)
            save_runtime_session(latest_session)
            payload = _patch_service_postcondition_payload(
                payload,
                session=latest_session,
                authoritative=authoritative,
            )
    return _phase8_part39_patch_social_claim_mismatch(
        payload,
        player_input=player_input,
    )


def _service_postcondition_satisfied(
    payload: dict[str, Any],
    *,
    session_id: str,
    service_result: dict[str, Any],
) -> bool:
    from app.apps.rpg.session.state_normalization import (
        _safe_list as _safe_list,
    )

    kind = _clean(service_result.get("kind"))
    selected_offer_id = _clean(service_result.get("selected_offer_id"))
    session = _safe_dict(load_runtime_session(session_id))
    simulation = _safe_dict(session.get("simulation_state"))
    if kind == "service_purchase" and selected_offer_id:
        for row in _safe_list(simulation.get("transaction_history")):
            row = _safe_dict(row)
            if _clean(row.get("offer_id") or row.get("service_id")) == selected_offer_id:
                return True
        for row in _safe_list(simulation.get("active_services")):
            row = _safe_dict(row)
            if _clean(row.get("offer_id") or row.get("service_id")) == selected_offer_id:
                return True
        return False
    if kind == "service_consumption":
        requested_kind = _clean(service_result.get("service_kind"))
        return any(
            _clean(_safe_dict(row).get("service_kind")) == requested_kind
            and _clean(_safe_dict(row).get("status")) == "consumed"
            for row in _safe_list(simulation.get("active_services"))
        )
    # Inquiries may write pending-offer and registered quest-evidence state.
    # Reapplying is safe: both helpers are bounded and clue IDs are idempotent.
    return False


def _patch_service_postcondition_payload(
    payload: dict[str, Any],
    *,
    session: dict[str, Any],
    authoritative: dict[str, Any],
) -> dict[str, Any]:
    patched = dict(_safe_dict(payload))
    simulation = deepcopy(_safe_dict(authoritative.get("simulation_state")))
    resolved = deepcopy(_safe_dict(authoritative.get("result")))
    visible = _grounded_service_visible_response(resolved)
    patched["simulation_state"] = simulation
    patched["session"] = deepcopy(session)
    patched["resolved_result"] = deepcopy(resolved)
    patched["authoritative"] = deepcopy(resolved)
    patched["service_result"] = deepcopy(_safe_dict(resolved.get("service_result")))
    patched["service_application"] = deepcopy(_safe_dict(resolved.get("service_application")))
    if visible:
        patched.update(
            {
                "narration": visible["narration"],
                "final_narration": visible["narration"],
                "deterministic_fallback_narration": visible["narration"],
                "npc": deepcopy(visible.get("npc") or {}),
                "visible_response": deepcopy(visible),
                "narration_status": "queued",
            }
        )
    nested = dict(_safe_dict(patched.get("result")))
    nested.update(
        {
            "ok": bool(resolved.get("ok", True)),
            "resolved_result": deepcopy(resolved),
            "simulation_state": simulation,
            "service_result": deepcopy(_safe_dict(resolved.get("service_result"))),
            "service_application": deepcopy(_safe_dict(resolved.get("service_application"))),
        }
    )
    if visible:
        nested.update(
            {
                "narration": visible["narration"],
                "final_narration": visible["narration"],
                "deterministic_fallback_narration": visible["narration"],
                "npc": deepcopy(visible.get("npc") or {}),
                "visible_response": deepcopy(visible),
                "narration_status": "queued",
            }
        )
        resolved["visible_response"] = deepcopy(visible)
        resolved["deterministic_fallback_narration"] = visible["narration"]
    patched["result"] = nested
    return patched


def _grounded_service_visible_response(resolved: dict[str, Any]) -> dict[str, Any]:
    resolved = _safe_dict(resolved)
    service_result = _safe_dict(resolved.get("service_result"))
    application = _safe_dict(resolved.get("service_application"))
    provider = _clean(service_result.get("provider_name")) or "The provider"
    kind = _clean(service_result.get("kind"))
    messages = []

    if kind == "service_purchase" and application.get("applied") is True:
        transaction = _safe_dict(application.get("transaction_record"))
        label = _clean(transaction.get("label")) or "the selected service"
        price = _safe_dict(_safe_dict(service_result.get("purchase")).get("price"))
        price_text = format_currency(price)
        narration = f"You pay {price_text} to {provider} for {label}. The transaction is complete."
        messages.append(
            {"kind": "npc_dialogue", "speaker": provider, "text": f"Done. {label} is settled."}
        )
    elif kind == "service_consumption":
        duration = _safe_dict(resolved.get("duration_application"))
        if duration.get("applied") is not True:
            return {}
        active = _safe_dict(duration.get("active_service"))
        label = _clean(active.get("label")) or "the reserved service"
        elapsed = int(duration.get("elapsed_minutes") or 0)
        narration = f"You use {label} and rest for {elapsed // 60} hours. The reserved service is consumed."
    elif kind == "service_inquiry":
        transition = _safe_dict(resolved.get("quest_transition"))
        if transition.get("applied") is not True and _clean(transition.get("reason")) != "quest_evidence_already_applied":
            return {}
        evidence = _safe_dict(transition.get("evidence"))
        clue = _clean(evidence.get("clue_summary"))
        objective = _clean(transition.get("objective"))
        if not clue:
            return {}
        narration = f"{provider} shares a concrete lead: {clue}"
        if objective:
            narration = f"{narration} Your objective is now: {objective}"
        messages.append(
            {"kind": "npc_dialogue", "speaker": provider, "text": clue}
        )
    else:
        return {}

    response: dict[str, Any] = {
        "format_version": "rpg_visible_response_v1",
        "narration": narration,
        "messages": messages,
        "plain_text": narration,
    }
    if messages:
        response["npc"] = {
            "speaker": messages[0]["speaker"],
            "line": messages[0]["text"],
        }
        response["plain_text"] = f'{narration}\n\n{messages[0]["speaker"]}: "{messages[0]["text"]}"'
    return response


def _canonicalize_publication(
    payload: dict[str, Any],
    *,
    player_input: str,
) -> dict[str, Any]:
    result = dict(_safe_dict(payload))
    existing = _safe_dict(
        result.get("narration_payload") or result.get("structured_narration")
    )
    if (
        existing.get("canonical_response_source") == "rpg_response_generator_v1"
        and _safe_dict(existing.get("canonical_response"))
    ):
        return result

    nested = _safe_dict(result.get("result"))
    state = _safe_dict(
        result.get("simulation_state")
        or nested.get("simulation_state")
        or _safe_dict(result.get("session")).get("simulation_state")
    )
    contract = _safe_dict(
        result.get("turn_contract")
        or nested.get("turn_contract")
        or result.get("authoritative")
    )
    narration = _clean(
        result.get("narration")
        or nested.get("narration")
        or result.get("final_narration")
        or nested.get("final_narration")
    )
    npc = _safe_dict(result.get("npc") or nested.get("npc"))
    authoritative = dict(nested or result)
    authoritative.setdefault("mechanic_resolved", bool(result.get("ok", True)))
    authoritative.setdefault("resolver_status", "resolved")
    canonical = _CANONICAL_PUBLICATION_PIPELINE.finalize_payload(
        {
            "source": "legacy_runtime_early_return",
            "legacy_visible_text": narration,
            "narration": narration,
            "npc": npc,
            "response_mode": (
                contract.get("response_mode")
                or contract.get("semantic_family")
                or contract.get("action_type")
                or authoritative.get("semantic_family")
                or authoritative.get("action_type")
                or "action"
            ),
        },
        player_input=player_input,
        authoritative_turn_result=authoritative,
        simulation_state=state,
        turn_contract=contract,
        runtime_mode="runtime_early_return",
    )
    result["narration_payload"] = canonical
    result["structured_narration"] = canonical
    result["presentation_narration_selection"] = {
        "source": "canonical_runtime_response",
        "runtime_payload_source": canonical.get("source"),
    }
    result["narration"] = canonical.get("narration") or narration
    result["npc"] = _safe_dict(canonical.get("npc") or npc)
    result["llm_called"] = canonical.get("source") == "provider_runtime_narration"
    if nested:
        nested = dict(nested)
        nested["narration_payload"] = deepcopy(canonical)
        nested["structured_narration"] = deepcopy(canonical)
        nested["presentation_narration_selection"] = deepcopy(
            result["presentation_narration_selection"]
        )
        nested["narration"] = result["narration"]
        nested["npc"] = deepcopy(result["npc"])
        nested["llm_called"] = result["llm_called"]
        result["result"] = nested
    return result


def _response_soft_truth(payload: dict[str, Any]) -> dict[str, Any]:
    narration = _safe_dict(
        payload.get("narration_payload") or payload.get("structured_narration")
    )
    return _safe_dict(
        narration.get("response_soft_truth") or payload.get("response_soft_truth")
    )


def _persist_soft_truth(payload: dict[str, Any], session_id: str) -> dict[str, Any]:
    from app.apps.rpg.session.session_runtime_store import (
        save_runtime_session as save_runtime_session,
    )

    result = dict(_safe_dict(payload))
    soft_truth = _response_soft_truth(result)
    if not soft_truth:
        return result
    simulation = dict(_safe_dict(result.get("simulation_state")))
    simulation["response_soft_truth"] = deepcopy(soft_truth)
    result["simulation_state"] = simulation
    nested = dict(_safe_dict(result.get("result")))
    if nested:
        nested["simulation_state"] = deepcopy(simulation)
        nested["response_soft_truth"] = deepcopy(soft_truth)
        result["result"] = nested
    session = dict(_safe_dict(result.get("session")))
    if not session:
        session = dict(_safe_dict(load_runtime_session(session_id)))
    if session:
        session_simulation = dict(_safe_dict(session.get("simulation_state")))
        session_simulation["response_soft_truth"] = deepcopy(soft_truth)
        session["simulation_state"] = session_simulation
        runtime_state = dict(_safe_dict(session.get("runtime_state")))
        runtime_state["response_soft_truth"] = deepcopy(soft_truth)
        session["runtime_state"] = runtime_state
        result["session"] = session
        save_runtime_session(session)
    result["response_soft_truth"] = deepcopy(soft_truth)
    return result


def apply_turn(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_apply_turn: Any = _PHASE8_PART39_BASE_APPLY_TURN,
) -> dict[str, Any]:
    payload = _base_apply_turn(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    canonical = _canonicalize_publication(payload, player_input=player_input)
    return _persist_soft_truth(canonical, session_id)


__all__ = ['_ACHIEVEMENT_VERBS', '_CANONICAL_PUBLICATION_PIPELINE', '_CLAIM_MARKERS', '_PHASE8_PART39_BASE_APPLY_TURN', '_PHASE8_PART39_BASE_APPLY_TURN_AUTHORITATIVE', '_PHASE8_PART39_SOURCE', '_SOCIAL_ACTIONS', '_TRAVEL_ACTIONS', '_apply_turn_authoritative', '_bool', '_canonicalize_publication', '_clean', '_grounded_service_visible_response', '_guard_fields', '_has_travel_mismatch', '_is_social_claim', '_iter_sources', '_norm', '_patch_service_postcondition_payload', '_patch_target', '_persist_soft_truth', '_phase8_part39_patch_social_claim_mismatch', '_response_soft_truth', '_service_postcondition_satisfied', '_source_field', '_source_text']