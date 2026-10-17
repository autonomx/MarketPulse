"""Deterministic special item signal helpers for RPG sessions."""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import display_item_name, item_type
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_signal_v1"
SUPPORTED_SIGNAL_OPS = {"add_affordance", "add_scene_status", "set_world_flag", "restore_resource"}
AFFORDANCE_BUCKETS = {"dialogue", "travel", "access", "evidence", "crafting", "combat", "social"}
RESOURCES = {"hp", "mana", "stamina"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _positive_int(value: Any, fallback: int = 1, *, limit: int = 250) -> int:
    try:
        return max(1, min(limit, int(value)))
    except Exception:
        return fallback


def _item_id(item: dict[str, Any]) -> str:
    return _text(item.get("item_id") or item.get("id") or item.get("instance_id") or display_item_name(item), "item")


def _signal_id(signal: dict[str, Any], ordinal: int) -> str:
    return _text(signal.get("signal_id") or signal.get("id") or signal.get("name"), f"item_signal_{ordinal + 1}")


def is_signal_item(item: dict[str, Any]) -> bool:
    normalized_type = _norm(item_type(item))
    tags = {_norm(tag) for tag in _safe_list(item.get("tags")) if _text(tag)}
    return normalized_type in {"artifact", "quest", "quest_item", "relic", "key"} or bool(tags & {"artifact", "relic", "quest", "signal"})


def _raw_signals(item: dict[str, Any]) -> list[Any]:
    for key in ("item_signals", "artifact_powers", "quest_powers", "powers"):
        raw = item.get(key)
        if isinstance(raw, list):
            return raw
    return []


def normalize_item_signals(item: dict[str, Any]) -> dict[str, Any]:
    repairs: list[str] = []
    signals: list[dict[str, Any]] = []
    if not is_signal_item(item):
        return {"ok": False, "error": "item_has_no_signal_contract", "signals": [], "repairs": []}

    for ordinal, raw_signal in enumerate(_raw_signals(item)):
        signal = deepcopy(_safe_dict(raw_signal))
        op = _norm(signal.get("op") or signal.get("operation") or signal.get("type"))
        if op not in SUPPORTED_SIGNAL_OPS:
            repairs.append(f"ignored_unsupported_item_signal_op:{op or ordinal}")
            continue
        signal_id = _signal_id(signal, ordinal)
        normalized = {"signal_id": signal_id, "op": op, "consume": signal.get("consume") is True}
        if op == "add_affordance":
            bucket = _norm(signal.get("bucket") or signal.get("kind")) or "dialogue"
            if bucket not in AFFORDANCE_BUCKETS:
                repairs.append(f"repaired_affordance_bucket:{bucket}")
                bucket = "dialogue"
            tag = _text(signal.get("tag") or signal.get("affordance"))
            if not tag:
                repairs.append(f"ignored_item_signal_affordance_without_tag:{ordinal}")
                continue
            normalized.update({"bucket": bucket, "tag": tag[:80], "dimension": _text(signal.get("dimension"), "item")[:40]})
        elif op == "add_scene_status":
            status = _text(signal.get("status") or signal.get("tag"))
            if not status:
                repairs.append(f"ignored_item_signal_status_without_status:{ordinal}")
                continue
            normalized.update({"status": status[:80], "dimension": _text(signal.get("dimension"), "item")[:40]})
        elif op == "set_world_flag":
            flag = _text(signal.get("flag") or signal.get("key"))
            if not flag:
                repairs.append(f"ignored_item_signal_flag_without_key:{ordinal}")
                continue
            normalized.update({"flag": flag[:80], "value": signal.get("value", True)})
        elif op == "restore_resource":
            resource = _norm(signal.get("resource") or signal.get("stat"))
            if resource not in RESOURCES:
                repairs.append(f"ignored_item_signal_restore_without_supported_resource:{ordinal}")
                continue
            normalized.update({"resource": resource, "amount": _positive_int(signal.get("amount") or signal.get("delta"), 1)})
        signals.append(normalized)

    if not signals:
        return {"ok": False, "error": "no_supported_item_signals", "signals": [], "repairs": repairs}
    return {"ok": True, "signals": signals, "repairs": repairs}


def _metric(player: dict[str, Any], key: str) -> dict[str, Any]:
    resources = _safe_dict(player.get("resources"))
    player["resources"] = resources
    metric = _safe_dict(resources.get(key))
    resources[key] = metric
    metric.setdefault("current", 0)
    metric.setdefault("max", metric.get("current", 0))
    return metric


def _apply_signal(state: dict[str, Any], item_name: str, signal: dict[str, Any]) -> dict[str, Any]:
    op = signal.get("op")
    if op == "add_affordance":
        affordances = _safe_dict(state.get("narrative_affordances"))
        bucket = str(signal.get("bucket") or "dialogue")
        entries = _safe_list(affordances.get(bucket))
        entry = {"tag": signal.get("tag"), "source": item_name, "dimension": signal.get("dimension"), "created_at": _utc_now()}
        affordances[bucket] = [entry, *entries][:20]
        state["narrative_affordances"] = affordances
        return {"op": op, "bucket": bucket, "tag": signal.get("tag"), "dimension": signal.get("dimension")}
    if op == "add_scene_status":
        scene_state = _safe_dict(state.get("scene_state"))
        statuses = _safe_list(scene_state.get("statuses"))
        entry = {"status": signal.get("status"), "source": item_name, "dimension": signal.get("dimension"), "created_at": _utc_now()}
        scene_state["statuses"] = [entry, *statuses][:20]
        state["scene_state"] = scene_state
        return {"op": op, "status": signal.get("status"), "dimension": signal.get("dimension")}
    if op == "set_world_flag":
        world_flags = _safe_dict(state.get("world_flags"))
        world_flags[str(signal.get("flag"))] = signal.get("value", True)
        state["world_flags"] = world_flags
        return {"op": op, "flag": signal.get("flag"), "value": signal.get("value", True)}
    if op == "restore_resource":
        player = _safe_dict(state.get("player"))
        state["player"] = player
        resource = str(signal.get("resource"))
        metric = _metric(player, resource)
        current = int(metric.get("current") or 0)
        maximum = int(metric.get("max") or current)
        amount = int(signal.get("amount") or 1)
        metric["current"] = max(0, min(maximum, current + amount))
        return {"op": op, "resource": resource, "amount": amount, "current": metric["current"], "max": maximum}
    return {"op": "ignored"}


def apply_item_signal(state: dict[str, Any], item: dict[str, Any], signal_id: str | None = None) -> dict[str, Any]:
    normalized = normalize_item_signals(item)
    if not normalized.get("ok"):
        return {"ok": False, "error": normalized.get("error"), "effects": [], "repairs": normalized.get("repairs", [])}

    wanted = _norm(signal_id)
    signals = [signal for signal in _safe_list(normalized.get("signals")) if not wanted or _norm(signal.get("signal_id")) == wanted]
    if not signals:
        return {"ok": False, "error": "item_signal_not_found", "signal_id": signal_id, "effects": [], "repairs": normalized.get("repairs", [])}

    item_name = display_item_name(item)
    effects = [_apply_signal(state, item_name, signal) for signal in signals]
    trace = {
        "event": "item_signal_applied",
        "item_id": _item_id(item),
        "item_name": item_name,
        "signal_ids": [signal.get("signal_id") for signal in signals],
        "effects": effects,
        "repairs": normalized.get("repairs", []),
        "mechanics_source": MECHANICS_SOURCE,
    }
    return {"ok": True, "effects": effects, "trace": trace, "repairs": normalized.get("repairs", [])}
