"""Read-only RPG ability details with presentation-only LLM prose."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway
from app.apps.rpg.session.item_detail import _session_genre, _setting_context
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_ability_detail.prompt', "1", (
    "Write a lore-grounded tooltip description for this RPG ability in exactly two sentences, roughly 30 to 55 words. "
        "Explain how the ability appears or feels in this campaign and its tactical purpose. Stay strictly within the supplied "
        "ability facts: do not invent additional damage, statuses, targets, costs, cooldowns, powers, provenance, or mechanics. "
        "Do not mention UI, hotbars, data fields, or game systems. Return immersive prose only."
))

ABILITY_DETAIL_SOURCE = "rpg_ability_detail_v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _find_ability(state: dict[str, Any], requested: str) -> dict[str, Any] | None:
    wanted = requested.casefold().strip()
    abilities = _list(_dict(state.get("ability_tree")).get("abilities"))
    for value in abilities:
        ability = _dict(value)
        if wanted in {
            _text(ability.get("ability_id") or ability.get("id")).casefold(),
            _text(ability.get("name") or ability.get("label")).casefold(),
        }:
            return ability
    return None


def _rank(state: dict[str, Any], ability: dict[str, Any]) -> int:
    ability_id = _text(ability.get("ability_id") or ability.get("id"))
    ranks = _dict(_dict(state.get("ability_state")).get("ranks"))
    return int(ranks.get(ability_id) or ability.get("rank") or 1)


def _detail(
    ability: dict[str, Any] | None,
    requested: str,
    *,
    state: dict[str, Any],
    summary: str,
    source: str,
) -> dict[str, Any]:
    if ability is None:
        return {
            "ability_id": requested,
            "name": requested,
            "summary": summary,
            "source": source,
        }
    return {
        "ability_id": _text(ability.get("ability_id") or ability.get("id")),
        "name": _text(ability.get("name") or ability.get("label")) or requested,
        "summary": summary,
        "kind": _text(ability.get("kind")) or "active",
        "rank": _rank(state, ability),
        "max_rank": int(ability.get("max_rank") or 1),
        "resource_cost": deepcopy(_dict(ability.get("resource_cost"))),
        "cooldown_turns": int(ability.get("cooldown_turns") or 0),
        "source": source,
    }


def generate_ability_detail(
    state: dict[str, Any],
    ability_name: str,
    *,
    genre: str | None = None,
    llm_gateway: Any | None = None,
) -> dict[str, Any]:
    """Describe an ability without changing authoritative ability state."""

    state = _dict(state)
    ability = _find_ability(state, ability_name)
    if ability is None:
        return {
            "ok": False,
            "error": "ability_not_found",
            "ability_detail": _detail(
                None,
                ability_name,
                state=state,
                summary="This ability is not present in the selected session.",
                source="unavailable",
            ),
            "mechanics_source": ABILITY_DETAIL_SOURCE,
        }

    resolved_genre = _session_genre(state, genre)
    setting = _setting_context(state, resolved_genre)
    facts = {
        "name": _text(ability.get("name")),
        "kind": _text(ability.get("kind")),
        "existing_description": _text(ability.get("description")),
        "capability": _text(ability.get("capability")),
        "power_source": _text(ability.get("power_source")),
        "purpose": _text(ability.get("purpose")),
        "dimensions": [_text(value) for value in _list(ability.get("dimensions")) if _text(value)],
        "rank": _rank(state, ability),
        "max_rank": int(ability.get("max_rank") or 1),
        "resource_cost": deepcopy(_dict(ability.get("resource_cost"))),
        "cooldown_turns": int(ability.get("cooldown_turns") or 0),
        "effect_ops": deepcopy(_list(ability.get("effect_ops")))[:8],
    }
    gateway = llm_gateway or build_app_llm_gateway()
    if gateway is None:
        fallback = facts["existing_description"] or "An LLM provider is not available to describe this ability."
        return {
            "ok": False,
            "error": "ability_detail_llm_unavailable",
            "ability_detail": _detail(
                ability,
                ability_name,
                state=state,
                summary=cast(str, fallback),
                source="unavailable",
            ),
            "mechanics_source": ABILITY_DETAIL_SOURCE,
        }

    prompt = (
        _PROMPT_1.text
    )
    try:
        summary = _text(gateway.generate(prompt, context={"ability": facts, "setting": setting}, timeout_s=20.0))
    except Exception:
        summary = ""
    if not summary:
        fallback = facts["existing_description"] or "The configured LLM did not return an ability description."
        return {
            "ok": False,
            "error": "ability_detail_llm_failed",
            "ability_detail": _detail(
                ability,
                ability_name,
                state=state,
                summary=cast(str, fallback),
                source="unavailable",
            ),
            "mechanics_source": ABILITY_DETAIL_SOURCE,
        }

    return {
        "ok": True,
        "ability_detail": _detail(
            ability,
            ability_name,
            state=state,
            summary=summary,
            source="llm",
        ),
        "mechanics_source": ABILITY_DETAIL_SOURCE,
    }
