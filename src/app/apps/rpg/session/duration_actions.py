"""Generic deterministic duration actions backed by purchased services."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.session.environment_time import advance_environment_time
from app.apps.rpg.rules.survival import apply_survival_effect

DURATION_ACTION_VERSION = "rpg_duration_action_v1"
MINUTES_PER_DAY = 24 * 60
DEFAULT_MORNING_MINUTE = 8 * 60


def apply_duration_action(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    service_kind: str = "",
    tick: int = 0,
    policy: str = "",
) -> Dict[str, Any]:
    """Consume a matching active service and advance deterministic time."""

    state = simulation_state if isinstance(simulation_state, dict) else {}
    active = _matching_active_service(state, service_kind)
    effective_policy = _text(policy) or _text(_dict(active.get("effects")).get("duration"))
    if not active:
        return _blocked("missing_active_service", effective_policy)
    if not _requests_duration(player_input) and not policy:
        return _blocked("duration_not_requested", effective_policy)

    environment = _dict(state.get("environment"))
    if not environment:
        environment = {"absolute_minutes": DEFAULT_MORNING_MINUTE, "active_events": []}
    minutes = duration_minutes(environment, effective_policy)
    if minutes <= 0:
        return _blocked("unsupported_duration_policy", effective_policy)

    before_environment = deepcopy(environment)
    after_environment = advance_environment_time(environment, elapsed_minutes=minutes)
    state["environment"] = after_environment

    active["status"] = "consumed"
    active["consumed_tick"] = int(tick or 0)
    active["elapsed_minutes"] = minutes
    survival_result = apply_survival_effect(
        state,
        kind="rest",
        effects={"fatigue": -55},
        tick=tick,
        source="duration_action_runtime",
    )
    return {
        "schema_version": DURATION_ACTION_VERSION,
        "ok": True,
        "applied": True,
        "blocked": False,
        "blocked_reason": "",
        "policy": effective_policy,
        "elapsed_minutes": minutes,
        "active_service": deepcopy(active),
        "environment_before": before_environment,
        "environment_after": deepcopy(after_environment),
        "survival_result": survival_result,
        "source": "deterministic_duration_action_runtime",
    }


def duration_minutes(environment: Dict[str, Any], policy: str) -> int:
    """Return deterministic elapsed minutes for a registered duration policy."""

    normalized = _text(policy).casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"one_night", "until_next_morning", "overnight"}:
        current = max(0, int(_dict(environment).get("absolute_minutes") or 0)) % MINUTES_PER_DAY
        delta = (DEFAULT_MORNING_MINUTE - current) % MINUTES_PER_DAY
        return delta or MINUTES_PER_DAY
    if normalized.startswith("minutes:"):
        try:
            return max(0, int(normalized.split(":", 1)[1]))
        except (TypeError, ValueError):
            return 0
    return 0


def _matching_active_service(state: Dict[str, Any], service_kind: str) -> Dict[str, Any]:
    services = _list(state.get("active_services"))
    requested = _text(service_kind)
    for value in reversed(services):
        service = _dict(value)
        if service.get("status") != "active":
            continue
        if requested and _text(service.get("service_kind")) != requested:
            continue
        if _text(_dict(service.get("effects")).get("duration")):
            return service
    return {}


def _requests_duration(player_input: str) -> bool:
    text = _text(player_input).casefold()
    return any(term in text for term in ("sleep", "rest", "wait", "until morning", "through the night", "overnight"))


def _blocked(reason: str, policy: str) -> Dict[str, Any]:
    return {
        "schema_version": DURATION_ACTION_VERSION,
        "ok": False,
        "applied": False,
        "blocked": True,
        "blocked_reason": reason,
        "policy": policy,
        "source": "deterministic_duration_action_runtime",
    }


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


__all__ = ["DURATION_ACTION_VERSION", "apply_duration_action", "duration_minutes"]
