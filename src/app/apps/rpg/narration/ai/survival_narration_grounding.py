"""Bundle BS — survival narration grounding contract.

Survival state is authoritative.  The narrator may describe hunger, thirst,
fatigue, meals, water, rest, lodging, and supply changes only when those claims
are backed by deterministic survival/service/merchant evidence in the turn
context.  This module is intentionally pure and JSON-safe so it can be used by
prompt builders, validators, reports, and tests without invoking an LLM.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, cast
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.ai_survival_narration_grounding.survival_narration_prompt_block', "1", "- Simulation is authoritative for hunger, thirst, fatigue, inventory, purchases, meals, water, and rest.")
_PROMPT_2 = prompt_template('rpg.ai_survival_narration_grounding.survival_narration_prompt_block_2', "1", "- Mention water/meals/rest/supplies only when backed by survival_result, service_result, merchant_result, effects, or inventory_delta.")
_PROMPT_3 = prompt_template('rpg.ai_survival_narration_grounding.survival_narration_prompt_block_3', "1", "- Do not say the player is refreshed unless fatigue decreased. Do not say thirst/hunger is relieved unless thirst/hunger decreased.")
_PROMPT_4 = prompt_template('rpg.ai_survival_narration_grounding.survival_narration_prompt_block_4', "1", "- Do not invent healing from rest or meals unless authoritative healing evidence exists.")

SURVIVAL_NARRATION_GROUNDING_SOURCE = "survival_narration_grounding_contract"
SURVIVAL_NARRATION_GROUNDING_VERSION = "survival_narration_grounding_v1"
_NEEDS = ("hunger", "thirst", "fatigue")

_WATER_TERMS = (
    "water", "waterskin", "well", "stream", "river", "spring", "drink", "drank",
    "sip", "quaff", "thirst", "thirsty", "hydrated", "quenched",
)
_FOOD_TERMS = (
    "meal", "food", "ration", "rations", "stew", "bread", "cheese", "provisions",
    "eat", "eats", "ate", "fed", "hunger", "hungry", "supper", "breakfast", "dinner",
)
_REST_TERMS = (
    "rest", "rests", "rested", "sleep", "sleeps", "slept", "lodging", "room", "bed",
    "inn", "camp", "fatigue", "tired", "weary", "refreshed", "rested",
)
_SUPPLY_TERMS = (
    "supply", "supplies", "ration pack", "provisions", "inventory", "pack", "satchel",
    "bought", "purchased", "paid", "settled", "coins", "coin", "silver", "gold", "copper",
)
_HEALING_TERMS = (
    "heal", "heals", "healed", "healing", "wound closes", "wounds close", "restored health",
    "recover health", "hp", "hit points",
)

_CATEGORY_TERMS = {
    "water": _WATER_TERMS,
    "food": _FOOD_TERMS,
    "rest": _REST_TERMS,
    "supplies": _SUPPLY_TERMS,
    "healing": _HEALING_TERMS,
}

_ACTION_CATEGORY = {
    "drink_water": "water",
    "drink_from_waterskin": "water",
    "drink_from_well": "water",
    "drink_from_stream": "water",
    "fill_waterskin": "water",
    "buy_water": "water",
    "buy_waterskin": "water",
    "eat_rations": "food",
    "eat_food": "food",
    "tavern_meal": "food",
    "buy_meal": "food",
    "buy_rations": "food",
    "ration_pack": "food",
    "rest": "rest",
    "sleep": "rest",
    "make_camp": "rest",
    "inn_lodging": "rest",
    "buy_lodging": "rest",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _safe_str(value).strip().lower().replace("_", " "))


def _norm_action_id(value: Any) -> str:
    text = _safe_str(value).strip().lower().replace("-", "_").replace(":", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    if text.startswith("survival_"):
        text = text[len("survival_"):]
    return text


def _result_is_success(result: Mapping[str, Any]) -> bool:
    result = _safe_dict(result)
    return result.get("ok") is not False and not result.get("blocked_reason")


def _walk(value: Any, *, depth: int = 0, max_depth: int = 7) -> Iterable[Dict[str, Any]]:
    if depth > max_depth:
        return
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested, depth=depth + 1, max_depth=max_depth)
    elif isinstance(value, list):
        for item in value[:200]:
            yield from _walk(item, depth=depth + 1, max_depth=max_depth)


def _first_survival_state(context: Mapping[str, Any]) -> Dict[str, Any]:
    for item in _walk(context):
        survival = _safe_dict(item.get("survival"))
        if any(key in survival for key in _NEEDS):
            return survival
    return {}


def _semantic_result_key(result: Mapping[str, Any]) -> str:
    result = _safe_dict(result)
    return "|".join(
        [
            _norm_action_id(result.get("action")),
            _safe_str(result.get("ok")),
            _safe_str(result.get("blocked_reason") or result.get("reason")),
            str(sorted(_safe_dict(result.get("effects")).items())),
            str(sorted(_safe_dict(result.get("inventory_delta")).items())),
        ]
    )


def _all_survival_results(context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk(context):
        candidate = _safe_dict(item.get("survival_result"))
        if candidate:
            key = _semantic_result_key(candidate)
            if key not in seen:
                seen.add(key)
                results.append(candidate)
        if _safe_str(item.get("action_category")) == "survival" and _safe_str(item.get("action")):
            key = _semantic_result_key(item)
            if key not in seen:
                seen.add(key)
                results.append(item)
    return results


def _has_category_evidence(category: str, evidence: Mapping[str, Any]) -> bool:
    """Return whether a narration claim category is directly authorized."""
    categories = set(_safe_list(evidence.get("backed_categories")))
    if category in categories:
        return True
    successful_actions = {_norm_action_id(action) for action in _safe_list(evidence.get("successful_actions"))}
    if any(_ACTION_CATEGORY.get(action) == category for action in successful_actions):
        return True
    effects = _safe_dict(evidence.get("effects"))
    inventory_delta = _safe_dict(evidence.get("inventory_delta"))
    if category == "water":
        return any(key.startswith("thirst") for key in effects) or any(
            key in inventory_delta for key in ("water", "waterskin", "waterskin_water_charges")
        )
    if category == "food":
        return any(key.startswith("hunger") for key in effects) or any(
            key in inventory_delta for key in ("food", "rations")
        )
    if category == "rest":
        return any(key.startswith("fatigue") for key in effects)
    if category == "supplies":
        return bool(inventory_delta or evidence.get("merchant_backed") or evidence.get("service_backed"))
    if category == "healing":
        return any("heal" in key or key in {"hp_delta", "health_delta"} for key in effects)
    return False


def _category_term_matches(lower_text: str, term: str) -> bool:
    term = _norm(term)
    if not term:
        return False
    if " " in term:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    else:
        pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, lower_text) is not None


def _category_claimed(text: str, category: str) -> bool:
    lower = _norm(text)
    return any(_category_term_matches(lower, term) for term in _CATEGORY_TERMS.get(category, ()))


def _sentence_claim_categories(sentence: str) -> List[str]:
    return [category for category in _CATEGORY_TERMS if _category_claimed(sentence, category)]


def _category_grounding_reason(category: str) -> str:
    return {
        "water": "water/thirst claim lacks authoritative drink, refill, purchase, or water-source evidence",
        "food": "food/hunger claim lacks authoritative meal, ration, purchase, or consumption evidence",
        "rest": "rest/fatigue/lodging claim lacks authoritative rest, sleep, camp, or lodging evidence",
        "supplies": "supply/purchase claim lacks authoritative inventory, merchant, or service evidence",
        "healing": "healing claim lacks authoritative healing evidence",
    }.get(category, "claim lacks authoritative survival evidence")


def build_survival_narration_evidence(context: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract bounded survival facts for narrator prompt/validation."""
    context = _safe_dict(context)
    survival = _first_survival_state(context)
    results = _all_survival_results(context)
    backed_categories: set[str] = set()
    actions: List[str] = []
    successful_actions: List[str] = []
    blocked: List[Dict[str, str]] = []
    effects: Dict[str, int] = {}
    inventory_delta: Dict[str, int] = {}
    service_types: List[str] = []
    merchant_backed = False
    service_backed = False

    for result in results:
        action = _norm_action_id(result.get("action"))
        if action:
            actions.append(action)
            category = _ACTION_CATEGORY.get(action)
            if category and _result_is_success(result):
                backed_categories.add(category)
                successful_actions.append(action)
        if not _result_is_success(result):
            blocked.append({
                "action": action,
                "reason": _safe_str(result.get("blocked_reason") or result.get("reason")),
            })
            continue
        for key, value in _safe_dict(result.get("effects")).items():
            effects[key] = effects.get(key, 0) + _safe_int(value)
        for key, value in _safe_dict(result.get("inventory_delta")).items():
            inventory_delta[key] = inventory_delta.get(key, 0) + _safe_int(value)
        service_result = _safe_dict(result.get("service_result"))
        if service_result:
            service_backed = True
            service_type = _safe_str(service_result.get("service_type"))
            if service_type:
                service_types.append(service_type)
        merchant_result = _safe_dict(result.get("merchant_result"))
        if merchant_result:
            merchant_backed = True

    if merchant_backed or service_backed or inventory_delta:
        backed_categories.add("supplies")
    if any(key.startswith("thirst") for key in effects) or any(key in inventory_delta for key in ("water", "waterskin", "waterskin_water_charges")):
        backed_categories.add("water")
    if any(key.startswith("hunger") for key in effects) or any(key in inventory_delta for key in ("food", "rations")):
        backed_categories.add("food")
    if any(key.startswith("fatigue") for key in effects):
        backed_categories.add("rest")

    return {
        "format_version": SURVIVAL_NARRATION_GROUNDING_VERSION,
        "survival": {
            need: _safe_int(survival.get(need), 0)
            for need in _NEEDS
            if need in survival
        },
        "actions": actions[-8:],
        "successful_actions": successful_actions[-8:],
        "blocked_actions": blocked[-8:],
        "effects": effects,
        "inventory_delta": inventory_delta,
        "service_types": service_types[-8:],
        "backed_categories": sorted(backed_categories),
        "merchant_backed": merchant_backed,
        "service_backed": service_backed,
        "source": SURVIVAL_NARRATION_GROUNDING_SOURCE,
    }


def survival_narration_prompt_block(context: Mapping[str, Any]) -> str:
    evidence = build_survival_narration_evidence(context)
    survival = _safe_dict(evidence.get("survival"))
    lines = [
        "Survival grounding contract:",
        _PROMPT_1.text,
        _PROMPT_2.text,
        _PROMPT_3.text,
        _PROMPT_4.text,
    ]
    if survival:
        lines.append(
            "- Current survival: " + ", ".join(f"{need}={survival.get(need)}" for need in _NEEDS if need in survival)
        )
    backed = _safe_list(evidence.get("backed_categories"))
    lines.append("- Backed survival categories this turn: " + (", ".join(backed) if backed else "none"))
    successful_actions = _safe_list(evidence.get("successful_actions"))
    if successful_actions:
        lines.append("- Successful authoritative survival actions: " + ", ".join(successful_actions))
    actions = _safe_list(evidence.get("actions"))
    if actions:
        lines.append("- Observed survival actions: " + ", ".join(actions))
    effects = _safe_dict(evidence.get("effects"))
    if effects:
        lines.append("- Authoritative survival effects: " + ", ".join(f"{k}={v}" for k, v in sorted(effects.items())))
    inventory_delta = _safe_dict(evidence.get("inventory_delta"))
    if inventory_delta:
        lines.append("- Authoritative inventory delta: " + ", ".join(f"{k}={v}" for k, v in sorted(inventory_delta.items())))
    blocked = _safe_list(evidence.get("blocked_actions"))
    if blocked:
        lines.append("- Blocked survival actions: " + ", ".join(f"{_safe_dict(row).get('action')}:{_safe_dict(row).get('reason')}" for row in blocked))
    return "\n".join(lines)


def _is_passive_state_only_claim(sentence: str, category: str) -> bool:
    lower = _norm(sentence)
    return (
        category in {"water", "food", "rest"}
        and not any(token in lower for token in (
            "quenched", "relieved", "eases", "eased", "sated", "fed", "refreshed", "rested", "restores", "restored",
            "bought", "purchased", "paid", "meal", "stew", "rations", "water", "waterskin",
            "room", "lodging", "bed", "sleep",
        ))
    )


def validate_survival_narration_text(text: str, context: Mapping[str, Any]) -> Dict[str, Any]:
    evidence = build_survival_narration_evidence(context)
    text = _safe_str(text)
    violations: List[Dict[str, str]] = []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences and text.strip():
        sentences = [text.strip()]

    for sentence in sentences:
        for category in _sentence_claim_categories(sentence):
            if _has_category_evidence(category, evidence):
                continue
            if _is_passive_state_only_claim(sentence, category):
                continue
            violations.append({
                "category": category,
                "reason": _category_grounding_reason(category),
                "sentence": sentence,
            })

    return {
        "ok": not violations,
        "violations": violations,
        "evidence": evidence,
        "source": SURVIVAL_NARRATION_GROUNDING_SOURCE,
    }


def sanitize_survival_narration_text(text: str, context: Mapping[str, Any], *, fallback: str = "") -> str:
    text = _safe_str(text).strip()
    if not text:
        return text
    evidence = build_survival_narration_evidence(context)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences:
        sentences = [text]
    kept: List[str] = []
    for sentence in sentences:
        categories = _sentence_claim_categories(sentence)
        if not categories:
            kept.append(sentence)
            continue
        rejected = False
        for category in categories:
            if _has_category_evidence(category, evidence):
                continue
            if _is_passive_state_only_claim(sentence, category):
                continue
            rejected = True
            break
        if not rejected:
            kept.append(sentence)
    if kept:
        return " ".join(kept).strip()
    fallback = _safe_str(fallback).strip()
    if fallback:
        return fallback
    return "The action resolves according to the current survival state."


def sanitize_survival_narration_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> Dict[str, Any]:
    out = deepcopy(_safe_dict(payload))
    fallback = _safe_str(_safe_dict(context).get("authoritative_fallback"))
    out["narration"] = sanitize_survival_narration_text(cast(Any, out.get("narration")), context, fallback=fallback)
    out["action"] = sanitize_survival_narration_text(cast(Any, out.get("action")), context, fallback="")
    npc = _safe_dict(out.get("npc"))
    if npc:
        npc["line"] = sanitize_survival_narration_text(cast(Any, npc.get("line")), context, fallback="")
        out["npc"] = npc
    validation = validate_survival_narration_text(" ".join([
        _safe_str(out.get("narration")),
        _safe_str(out.get("action")),
        _safe_str(_safe_dict(out.get("npc")).get("line")),
    ]), context)
    out["survival_narration_grounding"] = {
        "ok": validation.get("ok"),
        "violations": validation.get("violations"),
        "evidence": validation.get("evidence"),
        "source": SURVIVAL_NARRATION_GROUNDING_SOURCE,
    }
    return out
