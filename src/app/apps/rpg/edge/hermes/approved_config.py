from __future__ import annotations

from app.config.env import environment

from collections.abc import Mapping
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}
FEATURE_FLAG = "HERMES_RPG_APPROVED_FLOW_ENABLED"


def hermes_rpg_approved_flow_feature_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = environment() if environ is None else environ
    return str(source.get(FEATURE_FLAG, "")).strip().lower() in _TRUE_VALUES


def hermes_rpg_approved_flow_config_payload(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    enabled = hermes_rpg_approved_flow_feature_enabled(environ)
    return {
        "ok": True,
        "source": "hermes_rpg_approved_flow_config",
        "feature_flag": FEATURE_FLAG,
        "default_enabled": False,
        "enabled": enabled,
        "requires_payload_enabled": True,
        "simulation_owned": True,
    }
