"""Campaign Genesis readiness checks."""
from __future__ import annotations

from typing import Any, Mapping


class CampaignLaunchBlockedError(RuntimeError):
    pass


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _world_forge_enabled(session: Mapping[str, Any]) -> bool:
    setup = _mapping(session.get("setup_payload"))
    state = _mapping(session.get("state"))
    return bool(_mapping(setup.get("world_forge"))) or bool(
        state.get("campaign_bible")
    )


def campaign_launch_readiness(session: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _mapping(session.get("runtime_state"))
    gate = _mapping(runtime.get("campaign_launch_gate"))
    enabled = _world_forge_enabled(session)
    if not enabled:
        return {
            "enabled": False,
            "ready": True,
            "reason": "not_required",
        }
    ready = gate.get("ready") is True
    return {
        "enabled": True,
        "ready": ready,
        "required_before_first_turn": gate.get(
            "required_before_first_turn",
            True,
        ),
        "missing_requirements": list(
            gate.get("missing_requirements") or ()
        ),
        "reason": (
            "ready" if ready else "campaign_genesis_incomplete"
        ),
    }


def require_campaign_launch_ready(
    session: Mapping[str, Any],
) -> dict[str, Any]:
    result = campaign_launch_readiness(session)
    if not result["ready"]:
        raise CampaignLaunchBlockedError(
            "campaign genesis is incomplete: "
            + ",".join(result.get("missing_requirements") or ())
        )
    return result


