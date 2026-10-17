"""Deterministic item-system objectives for autoplay and UI nudges.

This module converts the existing item action/report surfaces into small,
AI-free next-step suggestions. It does not mutate session state; callers can
feed the returned action payloads into ``item_session_actions`` or existing
loadout/session bridges.
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.item_action_summary import build_item_action_summary
from app.apps.rpg.session.item_report_sections import build_item_report_section
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_OBJECTIVES_SOURCE = "engine_item_objectives_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _enabled_item_action(summary: dict[str, Any], action_name: str) -> dict[str, Any] | None:
    for raw_action in _safe_list(summary.get("actions")):
        action = _safe_dict(raw_action)
        if action.get("action") == action_name and action.get("enabled") is True:
            return action
    return None


def _first_enabled_recipe(actions: list[Any]) -> dict[str, Any] | None:
    for raw_action in actions:
        action = _safe_dict(raw_action)
        if action.get("enabled") is True:
            return action
    return None


def build_item_objectives(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    limit: int = 6,
) -> dict[str, Any]:
    """Build deterministic next item-system objectives.

    Objectives are ordered by coverage value and use only engine-owned action
    summaries. They are suitable for autoplay planning or report recommendations
    because they are explicit payloads rather than prose-only suggestions.
    """

    state = _safe_dict(state)
    action_surface = build_item_action_summary(state, station=station, genre=genre)
    report = build_item_report_section(state, station=station, genre=genre)
    objectives: list[dict[str, Any]] = []

    recipe = _first_enabled_recipe(_safe_list(action_surface.get("recipe_actions")))
    if recipe:
        objectives.append(
            {
                "objective_id": f"craft:{recipe.get('recipe_id')}",
                "category": "crafting",
                "priority": 90,
                "label": f"Craft {recipe.get('recipe_name') or recipe.get('recipe_id')}",
                "action": {"action": "craft", "recipe_id": recipe.get("recipe_id"), "station": recipe.get("station") or station},
                "reason": "enabled_recipe",
            }
        )

    for item_summary in _safe_list(action_surface.get("inventory_actions")):
        item = _safe_dict(item_summary)
        name = _text(item.get("name") or item.get("item_id"), "item")
        if _enabled_item_action(item, "use"):
            objectives.append(
                {
                    "objective_id": f"use:{item.get('item_id') or name}",
                    "category": "item_use",
                    "priority": 80,
                    "label": f"Use {name}",
                    "action": {"action": "use", "item_name": name},
                    "reason": "usable_item",
                }
            )
        if _enabled_item_action(item, "equip"):
            objectives.append(
                {
                    "objective_id": f"equip:{item.get('item_id') or name}",
                    "category": "equipment",
                    "priority": 70,
                    "label": f"Equip {name}",
                    "action": {"action": "equip", "item_name": name},
                    "reason": "equipment_item",
                }
            )
        if _enabled_item_action(item, "salvage"):
            objectives.append(
                {
                    "objective_id": f"salvage:{item.get('item_id') or name}",
                    "category": "materials",
                    "priority": 60,
                    "label": f"Salvage {name}",
                    "action": {"action": "salvage", "item_name": name},
                    "reason": "salvageable_item",
                }
            )
        if _enabled_item_action(item, "sell"):
            objectives.append(
                {
                    "objective_id": f"sell:{item.get('item_id') or name}",
                    "category": "market",
                    "priority": 40,
                    "label": f"Sell {name}",
                    "action": {"action": "sell", "item_name": name},
                    "reason": "sellable_item",
                }
            )

    coverage = _safe_dict(report.get("coverage"))
    gaps = _safe_list(coverage.get("gaps") or coverage.get("missing") or report.get("gaps"))
    if gaps:
        objectives.append(
            {
                "objective_id": "report:item_coverage_gap",
                "category": "coverage",
                "priority": 20,
                "label": "Improve item-system coverage",
                "action": {"action": "report", "record": True},
                "reason": ",".join(str(gap) for gap in gaps[:3]),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for objective in sorted(objectives, key=lambda item: (-int(item.get("priority") or 0), str(item.get("objective_id") or ""))):
        objective_id = _text(objective.get("objective_id"))
        if not objective_id or objective_id in seen:
            continue
        seen.add(objective_id)
        deduped.append(objective)
        if len(deduped) >= max(1, int(limit or 1)):
            break

    trace = {
        "event": "item_objectives_built",
        "objective_count": len(deduped),
        "enabled_action_count": int(action_surface.get("enabled_action_count") or 0),
        "coverage_score": _safe_dict(report.get("coverage")).get("score"),
        "mechanics_source": ITEM_OBJECTIVES_SOURCE,
    }
    return {
        "objectives": deduped,
        "summary": {
            "objective_count": len(deduped),
            "enabled_action_count": int(action_surface.get("enabled_action_count") or 0),
            "coverage_score": _safe_dict(report.get("coverage")).get("score"),
        },
        "trace": trace,
        "mechanics_source": ITEM_OBJECTIVES_SOURCE,
    }
