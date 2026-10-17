from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable, Mapping

from app.apps.rpg.session.player_personality_profile import extract_player_personality_profile
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_player_agency_contract.prompt', "1", (
    "Add player-personality flavor to RPG next-action option labels/descriptions. "
        "For an evil, cruel, or ruthless player, darker undertones are allowed, but commands must remain unchanged. "
        "Return JSON: {\"options\":[{\"id\":string,\"label\":string,\"description\":string,\"tone_tags\":[string]}]}."
))

PLAYER_AGENCY_CONTRACT_VERSION = "rpg_player_agency_contract_v1"
PLAYER_AGENCY_FLAVOR_VERSION = "rpg_player_agency_flavor_v1"
_ALLOWED_ACTION_TYPES = {
    "observe",
    "social_activity",
    "investigate",
    "exploration",
    "trade",
    "rest",
    "combat",
    "journal",
    "inventory",
}


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _l(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _clip(value: Any, limit: int = 160) -> str:
    return _s(value).strip()[:limit]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _nested_dicts(*values: Any) -> list[dict[str, Any]]:
    return [_d(value) for value in values if _d(value)]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _s(value).strip()
        if text:
            return text
    return ""


def _result_sources(result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    result = _d(result)
    nested = _d(result.get("result"))
    session = _d(result.get("session") or nested.get("session"))
    simulation = _d(result.get("simulation_state") or nested.get("simulation_state") or session.get("simulation_state"))
    runtime = _d(result.get("runtime_state") or nested.get("runtime_state") or session.get("runtime_state"))
    contract = _d(result.get("turn_contract") or nested.get("turn_contract"))
    return _nested_dicts(result, nested, contract, simulation, runtime, session)


def infer_player_personality(
    *,
    session: Mapping[str, Any] | None = None,
    simulation_state: Mapping[str, Any] | None = None,
    runtime_state: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect player-personality hints through the stable profile contract.

    Personality remains presentation context only. It must never mutate state or
    authorize actions.
    """

    profile = extract_player_personality_profile(
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        result=result,
    )
    return {
        "format_version": "rpg_player_personality_context_v2",
        "descriptor": _clip(profile.get("descriptor"), 500),
        "tone_hint": _clip(profile.get("tone_hint"), 80) or "neutral",
        "profile": deepcopy(profile),
        "raw": deepcopy(_d(profile.get("raw"))),
        "source": "player_personality_profile_contract",
        "presentation_only": True,
        "simulation_authority": False,
    }


def _known_location(result: Mapping[str, Any] | None, simulation_state: Mapping[str, Any] | None = None) -> tuple[str, str]:
    for source in (*_result_sources(result), _d(simulation_state)):
        location_id = _first_nonempty(source.get("current_location_id"), source.get("location_id"), _d(source.get("player_state")).get("current_location_id"), _d(source.get("player_state")).get("location_id"))
        location_name = _first_nonempty(source.get("current_location_name"), source.get("location_name"), source.get("scene_name"))
        if location_id or location_name:
            return location_id, location_name or location_id
    return "", ""


def _known_npc(result: Mapping[str, Any] | None) -> tuple[str, str]:
    for source in _result_sources(result):
        npc = _d(source.get("npc"))
        speaker = _first_nonempty(npc.get("speaker"), npc.get("name"), source.get("target_name"))
        target_id = _first_nonempty(npc.get("id"), source.get("target_id"))
        if speaker or target_id:
            return target_id, speaker or target_id
        diagnostics = _d(source.get("first_call_grounding_diagnostics"))
        packet = _d(diagnostics.get("turn_grounding_packet"))
        addressed = _l(_d(packet.get("npc_context")).get("addressed_npcs"))
        if addressed:
            first = _d(addressed[0])
            return _first_nonempty(first.get("id"), first.get("npc_id")), _first_nonempty(first.get("name"), first.get("id"), first.get("npc_id"))
    return "", ""


def _has_inventory(simulation_state: Mapping[str, Any] | None, result: Mapping[str, Any] | None) -> bool:
    for source in (*_result_sources(result), _d(simulation_state)):
        inventory = _d(_d(source.get("player_state")).get("inventory_state") or source.get("inventory_state"))
        if _l(inventory.get("items")) or _d(inventory.get("currency")):
            return True
    return False


def _has_service_or_currency(simulation_state: Mapping[str, Any] | None, result: Mapping[str, Any] | None) -> bool:
    for source in (*_result_sources(result), _d(simulation_state)):
        if _d(source.get("service_offer")) or _l(source.get("service_offers")) or _d(source.get("shop_state")):
            return True
        inventory = _d(_d(source.get("player_state")).get("inventory_state") or source.get("inventory_state"))
        if _d(inventory.get("currency")):
            return True
    return False


def _combat_active(runtime_state: Mapping[str, Any] | None, result: Mapping[str, Any] | None) -> bool:
    for source in (*_result_sources(result), _d(runtime_state)):
        combat = _d(source.get("combat_state"))
        if _truthy(combat.get("active")) or _s(combat.get("phase")).lower() in {"active", "combat", "enemy_turn", "player_turn"}:
            return True
        if _s(source.get("mode")).lower() == "combat" or _s(source.get("action_type")).lower() in {"combat", "attack_melee", "attack_ranged", "attack_unarmed"}:
            return True
    return False


def _quest_hint(result: Mapping[str, Any] | None, runtime_state: Mapping[str, Any] | None = None) -> str:
    for source in (*_result_sources(result), _d(runtime_state)):
        for key in ("current_objective", "objective", "quest_objective", "active_quest", "journal_objective"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                text = _first_nonempty(value.get("title"), value.get("summary"), value.get("description"), value.get("id"))
                if text:
                    return text
        quests = _l(source.get("quests") or source.get("active_quests") or _d(source.get("journal")).get("quests"))
        if quests:
            first = _d(quests[0])
            text = _first_nonempty(first.get("title"), first.get("summary"), first.get("description"), first.get("id"))
            if text:
                return text
    return ""


def _option(option_id: str, action_type: str, command: str, label: str, description: str, *, source_facts: list[str] | None = None, target_id: str = "", target_name: str = "") -> dict[str, Any]:
    return {
        "id": option_id,
        "action_type": action_type if action_type in _ALLOWED_ACTION_TYPES else "observe",
        "command": _clip(command, 220),
        "label": _clip(label, 90),
        "description": _clip(description, 240),
        "target_id": _clip(target_id, 80),
        "target_name": _clip(target_name, 80),
        "source_facts": list(source_facts or [])[:8],
        "validation_required": True,
        "authoritative": False,
        "presentation_only": True,
        "tone_tags": [],
    }


def build_authoritative_next_action_candidates(
    *,
    player_input: str = "",
    result: Mapping[str, Any] | None = None,
    session: Mapping[str, Any] | None = None,
    simulation_state: Mapping[str, Any] | None = None,
    runtime_state: Mapping[str, Any] | None = None,
    max_options: int = 5,
) -> list[dict[str, Any]]:
    """Build safe next-action candidates from known state only.

    These are affordances, not accepted actions. The command still has to go back
    through normal runtime validation when a user clicks or types it.
    """

    session = _d(session)
    simulation_state = _d(simulation_state or session.get("simulation_state"))
    runtime_state = _d(runtime_state or session.get("runtime_state"))
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(option: dict[str, Any]) -> None:
        option_id = _s(option.get("id"))
        command = _s(option.get("command")).strip().lower()
        if not option_id or not command or option_id in seen:
            return
        seen.add(option_id)
        options.append(option)

    npc_id, npc_name = _known_npc(result)
    location_id, location_name = _known_location(result, simulation_state)
    quest = _quest_hint(result, runtime_state)

    if _combat_active(runtime_state, result):
        add(_option("combat-assess", "combat", "I assess the threat before choosing my next combat move.", "Assess the threat", "Review the active threat before committing to attack, defend, flee, or use an item.", source_facts=["combat_state_active"], target_id=npc_id, target_name=npc_name))
        add(_option("combat-defend", "combat", "I defend myself and look for a safe opening.", "Defend and watch", "Take a safer combat posture while looking for an opening.", source_facts=["combat_state_active"], target_id=npc_id, target_name=npc_name))

    if npc_name:
        add(_option("talk-current-npc", "social_activity", f"I ask {npc_name} what they think I should do next.", f"Ask {npc_name}", "Get advice from the NPC currently involved in the scene.", source_facts=["current_or_addressed_npc"], target_id=npc_id, target_name=npc_name))

    if location_name:
        add(_option("inspect-location", "investigate", f"I inspect {location_name} for clues, exits, or changes.", "Inspect the area", "Look for useful clues, available routes, or changed local conditions.", source_facts=["known_current_location"], target_id=location_id, target_name=location_name))
        add(_option("travel-routes", "exploration", "I check what routes or landmarks I can travel to from here.", "Check routes", "Review available travel paths before moving on.", source_facts=["known_current_location"], target_id=location_id, target_name=location_name))

    if _has_service_or_currency(simulation_state, result):
        add(_option("service-or-supplies", "trade", "I ask what services, supplies, or prices are available here.", "Check services", "Review available services, supplies, prices, or affordability before spending coin.", source_facts=["service_or_currency_context"]))

    if _has_inventory(simulation_state, result):
        add(_option("check-inventory", "inventory", "I check my pack, coin, gear, and supplies.", "Check inventory", "Review current possessions and resources before deciding what to do next.", source_facts=["player_inventory_context"]))

    if quest:
        add(_option("continue-objective", "journal", f"I focus on the current objective: {quest}.", "Follow the objective", f"Continue from the current quest or objective: {quest}", source_facts=["active_objective_or_journal"]))

    add(_option("summarize-state", "observe", "I summarize where I am, who is with me, and what changed last turn.", "Summarize state", "Recap location, companions, risks, and recent consequences before acting.", source_facts=["safe_default_affordance"]))

    return options[: max(1, min(8, int(max_options or 5)))]


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = _s(value).strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return dict(payload) if isinstance(payload, Mapping) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                return dict(payload) if isinstance(payload, Mapping) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _provider_payload(provider: Any, prompt: str, context: Mapping[str, Any]) -> dict[str, Any]:
    if provider is None:
        return {}
    from app.apps.rpg.foundation.llm_gateway_adapter import adapt_base_provider

    provider = adapt_base_provider(provider)
    if hasattr(provider, "generate"):
        return _json_obj(provider.generate(prompt, context=dict(context), timeout_s=20.0))
    if hasattr(provider, "complete_json"):
        return _json_obj(provider.complete_json(prompt + "\nCONTEXT:\n" + json.dumps(dict(context), sort_keys=True, default=str)))
    if hasattr(provider, "complete"):
        return _json_obj(provider.complete(prompt + "\nCONTEXT:\n" + json.dumps(dict(context), sort_keys=True, default=str)))
    return {}


def flavor_player_agency_options(
    *,
    options: list[dict[str, Any]],
    player_personality: Mapping[str, Any] | None = None,
    provider: Any = None,
    flavor_func: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply presentation-only personality flavor to options.

    The provider/function may only alter label, description, and tone_tags for
    existing option ids. Commands/action types/targets are restored from the
    authoritative candidate list after parsing.
    """

    base = [dict(option) for option in options]
    personality = _d(player_personality)
    diagnostics = {
        "format_version": PLAYER_AGENCY_FLAVOR_VERSION,
        "requested": bool(provider or flavor_func),
        "applied": False,
        "provider_called": False,
        "valid": False,
        "error": "",
        "player_personality": personality,
    }
    if not base or not (provider or flavor_func):
        return base, diagnostics
    context = {
        "format_version": "rpg_player_agency_flavor_context_v1",
        "player_personality": personality,
        "options": [
            {"id": option["id"], "label": option["label"], "description": option["description"], "command": option["command"], "action_type": option["action_type"]}
            for option in base
        ],
        "rules": [
            "Return JSON only.",
            "Only rewrite label, description, and tone_tags for existing ids.",
            "Do not change command, action_type, target_id, target_name, or option availability.",
            "Personality tone is presentation only; do not imply the option already succeeded.",
        ],
    }
    prompt = (
        _PROMPT_1.text
    )
    try:
        diagnostics["provider_called"] = bool(provider)
        raw = flavor_func(context=context, prompt=prompt) if flavor_func is not None else _provider_payload(provider, prompt, context)
        payload = _json_obj(raw)
        by_id = {option["id"]: option for option in base}
        changed = 0
        for item in _l(payload.get("options")):
            item = _d(item)
            option_id = _s(item.get("id"))
            if option_id not in by_id:
                continue
            option = by_id[option_id]
            label = _clip(item.get("label"), 90)
            description = _clip(item.get("description"), 240)
            tone_tags = [_clip(tag, 32).lower().replace(" ", "_") for tag in _l(item.get("tone_tags"))[:6] if _clip(tag, 32)]
            if label:
                option["label"] = label
                changed += 1
            if description:
                option["description"] = description
                changed += 1
            option["tone_tags"] = tone_tags
            option["flavored"] = bool(label or description or tone_tags)
            option["presentation_only"] = True
            option["validation_required"] = True
        diagnostics["valid"] = bool(payload)
        diagnostics["applied"] = changed > 0
        if not payload:
            diagnostics["error"] = "empty_or_invalid_flavor_payload"
    except Exception as exc:
        diagnostics["error"] = f"{type(exc).__name__}:{exc}"
    return base, diagnostics


def build_player_agency_contract(
    *,
    player_input: str = "",
    result: Mapping[str, Any] | None = None,
    session: Mapping[str, Any] | None = None,
    simulation_state: Mapping[str, Any] | None = None,
    runtime_state: Mapping[str, Any] | None = None,
    provider: Any = None,
    flavor_func: Callable[..., Any] | None = None,
    max_options: int = 5,
) -> dict[str, Any]:
    personality = infer_player_personality(session=session, simulation_state=simulation_state, runtime_state=runtime_state, result=result)
    candidates = build_authoritative_next_action_candidates(
        player_input=player_input,
        result=result,
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        max_options=max_options,
    )
    options, flavor = flavor_player_agency_options(
        options=candidates,
        player_personality=personality,
        provider=provider,
        flavor_func=flavor_func,
    )
    return {
        "format_version": PLAYER_AGENCY_CONTRACT_VERSION,
        "source": "authoritative_state_candidates_with_presentation_only_personality_flavor",
        "player_input": _clip(player_input, 500),
        "personality": personality,
        "options": options,
        "option_count": len(options),
        "flavor_diagnostics": flavor,
        "safety": {
            "commands_are_suggestions_only": True,
            "runtime_validation_required": True,
            "llm_may_not_add_or_remove_options": True,
            "llm_may_not_change_commands_or_action_types": True,
            "presentation_only": True,
        },
    }


def attach_player_agency_contract(
    result: dict[str, Any],
    *,
    player_input: str = "",
    session: Mapping[str, Any] | None = None,
    simulation_state: Mapping[str, Any] | None = None,
    runtime_state: Mapping[str, Any] | None = None,
    provider: Any = None,
    flavor_func: Callable[..., Any] | None = None,
    max_options: int = 5,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    contract = build_player_agency_contract(
        player_input=player_input,
        result=result,
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        provider=provider,
        flavor_func=flavor_func,
        max_options=max_options,
    )
    result["next_actions"] = deepcopy(contract)
    result["player_agency_contract"] = deepcopy(contract)
    nested = _d(result.get("result"))
    if nested:
        nested["next_actions"] = deepcopy(contract)
        nested["player_agency_contract"] = deepcopy(contract)
        result["result"] = nested
    return result
