from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_list as _safe_list, safe_str as _safe_str

RECIPES: Dict[str, Dict[str, Any]] = {
    "recipe:torch": {
        "recipe_id": "recipe:torch",
        "name": "torch",
        "aliases": ["torch", "a torch", "torches"],
        "requires": [
            {"definition_id": "def:wooden_stick", "quantity": 1},
            {"definition_id": "def:cloth_scrap", "quantity": 1},
            {"definition_id": "def:oil_flask", "quantity": 1},
        ],
        "produces": [
            {"definition_id": "def:torch", "quantity": 1},
        ],
        "required_tool_tags": [],
        "source": "deterministic_recipe_catalog",
    },
    "recipe:arrow_bundle": {
        "recipe_id": "recipe:arrow_bundle",
        "name": "iron arrows",
        "aliases": ["iron arrows", "arrows", "arrow bundle"],
        "requires": [
            {"definition_id": "def:wooden_stick", "quantity": 2},
            {"definition_id": "def:cloth_scrap", "quantity": 1},
        ],
        "produces": [
            {"definition_id": "def:iron_arrow", "quantity": 5},
        ],
        "required_tool_tags": [],
        "source": "deterministic_recipe_catalog",
    },
}


def _norm(value: Any) -> str:
    return _safe_str(value).strip().lower()


def get_recipe(recipe_id: str) -> Dict[str, Any]:
    return deepcopy(RECIPES.get(_safe_str(recipe_id), {}))


def find_recipe_by_name(name: str) -> Dict[str, Any]:
    target = _norm(name)
    if not target:
        return {}

    for recipe in RECIPES.values():
        names = [_safe_str(recipe.get("name"))]
        names.extend(_safe_list(recipe.get("aliases")))
        if target in {_norm(item) for item in names}:
            return deepcopy(recipe)

    # Conservative fuzzy fallback.
    for recipe in RECIPES.values():
        names = [_safe_str(recipe.get("name"))]
        names.extend(_safe_list(recipe.get("aliases")))
        for item in names:
            normalized = _norm(item)
            if target and (target in normalized or normalized in target):
                return deepcopy(recipe)

    return {}
