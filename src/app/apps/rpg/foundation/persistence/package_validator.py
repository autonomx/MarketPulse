from __future__ import annotations

from typing import Any, Dict, List

from .save_schema import PACKAGE_TYPE
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def validate_save_package(package: Dict[str, Any]) -> List[Dict[str, str]]:
    package = dict(package or {})
    errors: List[Dict[str, str]] = []

    if package.get("package_type") != PACKAGE_TYPE:
        errors.append({"field": "package_type", "error": "invalid package type"})

    if not isinstance(package.get("schema_version"), int):
        errors.append({"field": "schema_version", "error": "missing or invalid schema_version"})

    adventure = _safe_dict(package.get("adventure"))
    state = _safe_dict(package.get("state"))

    if not isinstance(adventure.get("setup_payload"), dict):
        errors.append({"field": "adventure.setup_payload", "error": "missing or invalid setup_payload"})

    if not isinstance(state.get("simulation_state"), dict):
        errors.append({"field": "state.simulation_state", "error": "missing or invalid simulation_state"})

    return errors