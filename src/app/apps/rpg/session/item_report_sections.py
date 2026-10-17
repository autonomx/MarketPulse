"""Deterministic item-system report sections for RPG sessions."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.item_action_summary import build_item_action_summary
from app.apps.rpg.session.item_metrics import build_item_metrics_snapshot
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_report_section_v1"
COVERAGE_LABELS = {
    "has_inventory": "inventory",
    "has_materials": "materials",
    "has_known_recipes": "known_recipes",
    "has_item_use": "item_use",
    "has_salvage": "salvage",
    "has_crafting": "crafting",
    "has_scene_pickups": "scene_transfers",
    "has_market_quotes": "market",
    "has_item_signals": "special_item_signals",
    "has_modifications": "modifications",
}


def _action_counts(item_actions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in item_actions:
        for action in _safe_list(item.get("actions")):
            name = str(_safe_dict(action).get("action") or "").strip()
            if not name or not _safe_dict(action).get("enabled"):
                continue
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _disabled_reasons(item_actions: list[dict[str, Any]]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for item in item_actions:
        for action in _safe_list(item.get("actions")):
            current = _safe_dict(action)
            if current.get("enabled"):
                continue
            reason = str(current.get("reason") or current.get("error") or "disabled").strip() or "disabled"
            reasons[reason] = reasons.get(reason, 0) + 1
    return dict(sorted(reasons.items()))


def _recipe_summary(recipe_actions: list[dict[str, Any]]) -> dict[str, Any]:
    craftable = [recipe for recipe in recipe_actions if _safe_dict(recipe).get("enabled")]
    blocked = [recipe for recipe in recipe_actions if not _safe_dict(recipe).get("enabled")]
    blocked_reasons: dict[str, int] = {}
    for recipe in blocked:
        current = _safe_dict(recipe)
        reason = str(current.get("error") or "blocked").strip() or "blocked"
        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
    return {
        "known_or_available_count": len(recipe_actions),
        "craftable_count": len(craftable),
        "blocked_count": len(blocked),
        "blocked_reasons": dict(sorted(blocked_reasons.items())),
        "craftable_recipes": [str(_safe_dict(recipe).get("recipe_id") or "") for recipe in craftable if _safe_dict(recipe).get("recipe_id")],
    }


def _coverage(feature_flags: dict[str, Any]) -> dict[str, Any]:
    present: list[str] = []
    missing: list[str] = []
    for flag, label in COVERAGE_LABELS.items():
        if feature_flags.get(flag):
            present.append(label)
        else:
            missing.append(label)
    total = len(COVERAGE_LABELS)
    score = round(len(present) / total, 3) if total else 1.0
    return {"present": present, "missing": missing, "score": score}


def build_item_report_section(state: dict[str, Any], *, station: str | None = None, genre: str = "classic_fantasy") -> dict[str, Any]:
    """Build a compact AI-free item-system report section for autoplay and run reports."""
    metrics = build_item_metrics_snapshot(state)
    actions = build_item_action_summary(state, station=station, genre=genre)
    inventory = _safe_dict(metrics.get("inventory"))
    traces = _safe_dict(metrics.get("traces"))
    feature_flags = _safe_dict(metrics.get("feature_flags"))
    inventory_actions = [_safe_dict(item) for item in _safe_list(actions.get("inventory_actions"))]
    recipe_actions = [_safe_dict(item) for item in _safe_list(actions.get("recipe_actions"))]
    enabled_action_counts = _action_counts(inventory_actions)
    disabled = _disabled_reasons(inventory_actions)
    recipe = _recipe_summary(recipe_actions)
    coverage = _coverage(feature_flags)
    trace_counts = _safe_dict(traces.get("counts"))
    trace_total = sum(int(value or 0) for value in trace_counts.values() if isinstance(value, int))
    summary = {
        "item_count": inventory.get("item_count", 0),
        "total_quantity": inventory.get("total_quantity", 0),
        "known_recipe_count": len(_safe_list(metrics.get("known_recipes"))),
        "enabled_action_count": actions.get("enabled_action_count", 0),
        "trace_total": trace_total,
        "coverage_score": coverage["score"],
    }
    trace = {
        "event": "item_report_section_built",
        "item_count": summary["item_count"],
        "enabled_action_count": summary["enabled_action_count"],
        "coverage_score": coverage["score"],
        "mechanics_source": MECHANICS_SOURCE,
    }
    return {
        "ok": True,
        "title": "Item System Coverage",
        "summary": summary,
        "inventory": {
            "by_type": inventory.get("by_type", {}),
            "material_ids": inventory.get("material_ids", []),
            "sample_names": inventory.get("sample_names", []),
            "protected_count": inventory.get("protected_count", 0),
            "stackable_count": inventory.get("stackable_count", 0),
        },
        "actions": {
            "enabled_by_action": enabled_action_counts,
            "disabled_reasons": disabled,
            "recipe_summary": recipe,
        },
        "coverage": coverage,
        "traces": {"counts": trace_counts, "present": traces.get("present", []), "missing": traces.get("missing", [])},
        "trace": trace,
        "mechanics_source": MECHANICS_SOURCE,
    }


