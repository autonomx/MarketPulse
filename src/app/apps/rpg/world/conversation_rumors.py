from __future__ import annotations

from hashlib import sha1
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MAX_SIGNAL_AGE_TICKS_DEFAULT = 10  # assume

def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_safe_str(p) for p in parts)
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"

def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _rumor_tombstones(runtime_state: Dict[str, Any]) -> Dict[str, int]:
    runtime_state = _safe_dict(runtime_state)
    tombstones = runtime_state.get("conversation_rumor_tombstones")
    if not isinstance(tombstones, dict):
        tombstones = {}
        runtime_state["conversation_rumor_tombstones"] = tombstones
    return tombstones


def _tombstone_seed_this_tick(
    runtime_state: Dict[str, Any],
    *,
    seed_id: str,
    current_tick: int,
) -> None:
    if not seed_id:
        return
    tombstones = _rumor_tombstones(runtime_state)
    tombstones[seed_id] = int(current_tick or 0)

    # Keep this bounded.
    if len(tombstones) > 64:
        for key, _tick in sorted(tombstones.items(), key=lambda item: item[1])[:-64]:
            tombstones.pop(key, None)


def expire_conversation_world_signals(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    *,
    current_tick: int,
    settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    settings = _safe_dict(settings)

    now = int(current_tick or 0)
    max_signal_age = max(1, _safe_int(settings.get("max_signal_age_ticks"), 3))

    rumor_state = _safe_dict(simulation_state.get("conversation_rumor_state"))
    thread_state = _safe_dict(simulation_state.get("conversation_thread_state"))

    expired_seed_ids: List[str] = []
    expired_signal_ids: List[str] = []

    # ── Expire rumor seeds ─────────────────────────────────────────────
    kept_seeds: List[Dict[str, Any]] = []
    for seed in _safe_list(rumor_state.get("rumor_seeds")):
        seed = _safe_dict(seed)
        seed_id = _safe_str(seed.get("seed_id"))
        expires_tick = _safe_int(seed.get("expires_tick"), 0)

        if seed_id and expires_tick and now >= expires_tick:
            expired_seed_ids.append(seed_id)
            _tombstone_seed_this_tick(runtime_state, seed_id=seed_id, current_tick=now)
            continue

        kept_seeds.append(seed)

    rumor_state["rumor_seeds"] = kept_seeds

    # ── Expire rumor-state signal mirror, if present ───────────────────
    kept_rumor_signals: List[Dict[str, Any]] = []
    for signal in _safe_list(rumor_state.get("conversation_world_signals")):
        signal = _safe_dict(signal)
        signal_id = _safe_str(signal.get("signal_id"))
        signal_tick = _safe_int(signal.get("tick"), now)
        expires_tick = _safe_int(signal.get("expires_tick"), 0)

        expired = bool(expires_tick and now >= expires_tick)
        if not expired:
            expired = now - signal_tick >= max_signal_age

        if expired:
            if signal_id:
                expired_signal_ids.append(signal_id)
            continue

        kept_rumor_signals.append(signal)

    rumor_state["conversation_world_signals"] = kept_rumor_signals

    # ── Expire canonical conversation_thread_state.world_signals ───────
    kept_thread_signals: List[Dict[str, Any]] = []
    for signal in _safe_list(thread_state.get("world_signals")):
        signal = _safe_dict(signal)
        signal_id = _safe_str(signal.get("signal_id"))
        signal_tick = _safe_int(signal.get("tick"), now)
        expires_tick = _safe_int(signal.get("expires_tick"), 0)

        expired = bool(expires_tick and now >= expires_tick)
        if not expired:
            expired = now - signal_tick >= max_signal_age

        if expired:
            if signal_id:
                expired_signal_ids.append(signal_id)
            continue

        kept_thread_signals.append(signal)

    thread_state["world_signals"] = kept_thread_signals

    debug = _safe_dict(rumor_state.get("debug"))
    debug["last_expiration"] = {
        "current_tick": now,
        "expired_seed_ids": expired_seed_ids,
        "expired_signal_ids": expired_signal_ids,
        "expired_count": len(expired_seed_ids) + len(expired_signal_ids),
        "remaining_seed_count": len(kept_seeds),
        "remaining_thread_signal_count": len(kept_thread_signals),
        "remaining_rumor_signal_count": len(kept_rumor_signals),
        "source": "deterministic_conversation_rumor_expiration",
    }
    rumor_state["debug"] = debug

    simulation_state["conversation_rumor_state"] = rumor_state
    simulation_state["conversation_thread_state"] = thread_state

    return {
        "expired_seed_ids": expired_seed_ids,
        "expired_signal_ids": expired_signal_ids,
        "expired_count": len(expired_seed_ids) + len(expired_signal_ids),
        "remaining_seed_count": len(kept_seeds),
        "remaining_thread_signal_count": len(kept_thread_signals),
        "remaining_rumor_signal_count": len(kept_rumor_signals),
        "seed_expiration": {
            "expired_seed_ids": expired_seed_ids,
            "remaining_count": len(kept_seeds),
            "current_tick": now,
        },
        "source": "deterministic_conversation_rumor_expiration",
    }


