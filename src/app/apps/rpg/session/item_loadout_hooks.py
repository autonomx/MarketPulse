"""Route-free bridge for running item turn hooks after loadout-style actions.

The loadout route currently owns several item mutations directly.  This helper keeps
turn-boundary item maintenance/reporting reusable and easy to wire from loadout or
autoplay without changing the public request schema.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Iterator, cast

from app.apps.rpg.session.item_turn_hooks import build_item_turn_hook_plan, run_item_turn_hooks
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_loadout_hooks_v1"
TRACE_LIMIT = 20
ITEM_TRACE_LIMIT = 50
ITEM_LOADOUT_ACTIONS = frozenset(
    {
        "inspect",
        "use",
        "equip",
        "drop",
        "salvage",
        "craft",
        "modify",
    }
)
_PRESERVE_ACTION_TRACE_ORDER: ContextVar[bool] = ContextVar("loadout_item_preserve_action_trace_order", default=True)


@contextmanager
def loadout_item_trace_order(*, preserve_action_traces: bool) -> Iterator[None]:
    """Temporarily control whether action-owned item traces stay ahead of hook traces."""

    token = _PRESERVE_ACTION_TRACE_ORDER.set(bool(preserve_action_traces))
    try:
        yield
    finally:
        _PRESERVE_ACTION_TRACE_ORDER.reset(token)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _turn(state: dict[str, Any], current_turn: int | None = None) -> int:
    if current_turn is not None:
        return int(current_turn)
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _normal_action(action: Any) -> str:
    return str(action or "").strip().casefold()


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any], *, limit: int) -> None:
    mechanics[key] = [deepcopy(trace), *_safe_list(mechanics.get(key))][: max(1, int(limit or 1))]


def _has_item_trace_for_turn(mechanics: dict[str, Any], *, turn: int) -> bool:
    for value in _safe_list(mechanics.get("item_traces")):
        trace = _safe_dict(value)
        try:
            trace_turn = int(cast(Any, trace.get("turn")))
        except (TypeError, ValueError):
            continue
        if trace_turn == turn:
            return True
    return False


def _restore_existing_item_trace_order(mechanics: dict[str, Any], original: list[Any]) -> None:
    """Keep action-owned traces first when hooks run after a same-turn action."""

    if not original:
        return
    current = _safe_list(mechanics.get("item_traces"))
    if current[: len(original)] == original:
        return
    additions = [deepcopy(trace) for trace in current if trace not in original]
    mechanics["item_traces"] = [*deepcopy(original), *additions][:ITEM_TRACE_LIMIT]


def _existing_hook_trace(mechanics: dict[str, Any], *, action: str, turn: int) -> dict[str, Any] | None:
    for value in _safe_list(mechanics.get("item_loadout_hook_traces")):
        trace = _safe_dict(value)
        if (
            trace.get("event") == "item_loadout_hooks_ran"
            and trace.get("action") == action
            and int(trace.get("turn") or -1) == turn
            and trace.get("mechanics_source") == MECHANICS_SOURCE
        ):
            return deepcopy(trace)
    return None


def build_loadout_item_hook_plan(
    state: dict[str, Any],
    *,
    action: str | None,
    current_turn: int | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
) -> dict[str, Any]:
    """Build a deterministic plan for post-loadout item hooks without mutating state."""

    source = deepcopy(_safe_dict(state))
    normalized_action = _normal_action(action)
    turn = _turn(source, current_turn)
    should_run = normalized_action in ITEM_LOADOUT_ACTIONS
    hook_plan = build_item_turn_hook_plan(
        source,
        current_turn=turn,
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
    )
    return {
        "ok": True,
        "turn": turn,
        "action": normalized_action,
        "should_run": should_run,
        "reason": "item_loadout_action" if should_run else "non_item_loadout_action",
        "enabled_actions": list(hook_plan.get("enabled_actions", [])) if should_run else [],
        "hook_plan": hook_plan if should_run else None,
        "mechanics_source": MECHANICS_SOURCE,
    }


def run_loadout_item_hooks(
    state: dict[str, Any],
    *,
    action: str | None,
    current_turn: int | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
    record_trace: bool = True,
    record_hook_trace: bool = True,
) -> dict[str, Any]:
    """Run item turn hooks for item-related loadout actions against mutable state."""

    mutable_state = state if isinstance(state, dict) else {}
    plan = build_loadout_item_hook_plan(
        mutable_state,
        action=action,
        current_turn=current_turn,
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
    )
    if not plan["should_run"]:
        return {
            "ok": True,
            "skipped": True,
            "turn": plan["turn"],
            "action": plan["action"],
            "reason": plan["reason"],
            "recorded": False,
            "mechanics_source": MECHANICS_SOURCE,
        }

    mechanics = _mechanics(mutable_state)
    existing_trace = _existing_hook_trace(mechanics, action=plan["action"], turn=plan["turn"])
    if existing_trace is not None:
        return {
            "ok": True,
            "skipped": True,
            "turn": plan["turn"],
            "action": plan["action"],
            "reason": "already_ran",
            "recorded": False,
            "mechanics_trace": existing_trace,
            "mechanics_source": MECHANICS_SOURCE,
        }

    original_item_traces = deepcopy(_safe_list(mechanics.get("item_traces")))
    had_item_trace_for_turn = _has_item_trace_for_turn(mechanics, turn=plan["turn"])
    hook_result = run_item_turn_hooks(
        mutable_state,
        current_turn=plan["turn"],
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
        record_trace=record_hook_trace,
    )
    if had_item_trace_for_turn and _PRESERVE_ACTION_TRACE_ORDER.get():
        _restore_existing_item_trace_order(mechanics, original_item_traces)
    trace = {
        "event": "item_loadout_hooks_ran",
        "action": plan["action"],
        "turn": plan["turn"],
        "executed_actions": list(hook_result.get("executed_actions", [])),
        "hook_recorded": bool(hook_result.get("recorded")),
        "mechanics_source": MECHANICS_SOURCE,
        "timestamp": _utc_now(),
    }
    if record_trace:
        _prepend_trace(mechanics, "item_loadout_hook_traces", trace, limit=TRACE_LIMIT)
        if not had_item_trace_for_turn:
            _prepend_trace(mechanics, "item_traces", trace, limit=ITEM_TRACE_LIMIT)

    return {
        "ok": True,
        "skipped": False,
        "turn": plan["turn"],
        "action": plan["action"],
        "plan": plan,
        "hook_result": hook_result,
        "executed_actions": list(hook_result.get("executed_actions", [])),
        "mechanics_trace": trace,
        "recorded": bool(record_trace),
        "detail": f"Item loadout hooks ran after {plan['action']} on turn {plan['turn']}.",
        "mechanics_source": MECHANICS_SOURCE,
    }
