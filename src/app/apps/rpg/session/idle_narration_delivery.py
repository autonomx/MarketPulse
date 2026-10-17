from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session, save_runtime_session as save_runtime_session,
)
from app.apps.rpg.session.combat_intent import (
    _build_world_advance_recap as _build_world_advance_recap, _utc_now_iso as _utc_now_iso, ensure_ambient_runtime_state as ensure_ambient_runtime_state,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _safe_dict as _safe_dict, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.session.ambient_builder import (
    _MAX_IDLE_TICKS_PER_REQUEST as _MAX_IDLE_TICKS_PER_REQUEST, _MAX_RESUME_CATCHUP_TICKS as _MAX_RESUME_CATCHUP_TICKS,
)
from app.apps.rpg.session.idle_resume_runtime import (
    _apply_idle_tick_to_session as _apply_idle_tick_to_session, _build_resume_fallback_recap as _build_resume_fallback_recap,
    _recap_has_renderable_content as _recap_has_renderable_content,
)
from app.apps.rpg.narration.ai.world_scene_narrator_ambient import (
    narrate_ambient_update as narrate_ambient_update,
)
from app.apps.rpg.session.ambient_policy import (
    classify_ambient_delivery as classify_ambient_delivery, record_interrupt as record_interrupt,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)

from .idle_time import recorded_idle_tick_time

from app.runtime.clock import Clock as _Clock
from app.runtime.clock import SYSTEM_CLOCK as _SYSTEM_CLOCK
from app.runtime.clock import TurnContext as _TurnContext
from app.runtime.clock import bind_turn_context as _bind_turn_context
from app.apps.rpg.foundation.core.determinism import rng_seed_from_session_id as _rng_seed_from_session_id

def _make_initiative_update_from_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Convert an NPC initiative candidate into an ambient update."""
    candidate = _safe_dict(candidate)
    kind = _safe_str(candidate.get("kind") or "npc_to_player")
    speaker_name = _safe_str(candidate.get("speaker_name"))
    reason = _safe_str(candidate.get("reason"))
    action_intent = _safe_str(candidate.get("action_intent"))

    # Build default text from candidate metadata
    text = _safe_str(candidate.get("text_hint"))
    if not text:
        if kind == "quest_prompt":
            text = f"{speaker_name} has something important to share about your quest."
        elif kind == "recruitment_offer":
            text = f"{speaker_name} approaches with an offer."
        elif kind == "plea_for_help":
            text = f"{speaker_name} urgently needs your help."
        elif kind in ("taunt", "demand"):
            text = f"{speaker_name} confronts you."
        elif kind == "warning":
            text = f"{speaker_name} warns you of danger."
        elif kind == "companion_comment":
            reason = _safe_str(_safe_dict(candidate.get("structured")).get("reason") or candidate.get("reason"))
            if reason == "companion_idle_presence":
                text = f"{speaker_name} glances around, then leans closer to you."
            else:
                text = f"{speaker_name} murmurs a quick thought under their breath."
        else:
            text = f"{speaker_name} wants your attention."

    return {
        "tick": int(candidate.get("tick", 0) or 0),
        "kind": kind,
        "priority": float(candidate.get("salience", 0.0) or 0.0),
        "interrupt": bool(candidate.get("interrupt")),
        "speaker_id": _safe_str(candidate.get("speaker_id")),
        "speaker_name": speaker_name,
        "target_id": _safe_str(candidate.get("target_id")),
        "target_name": _safe_str(candidate.get("target_name")),
        "scene_id": "",
        "location_id": _safe_str(candidate.get("location_id")),
        "text": text,
        "structured": {
            "reason": reason,
            "action_intent": action_intent,
        },
        "source_event_ids": [],
        "source": "initiative",
        "created_at": _utc_now_iso(),
    }


def _make_scene_update_from_beat(beat: dict[str, Any]) -> dict[str, Any]:
    beat = _safe_dict(beat)
    return {
        "tick": 0,
        "kind": _safe_str(beat.get("kind") or "npc_to_npc"),
        "priority": float(beat.get("priority", 0.0) or 0.0),
        "interrupt": False,
        "speaker_id": _safe_str(beat.get("speaker_id")),
        "speaker_name": _safe_str(beat.get("speaker_name")),
        "target_id": _safe_str(beat.get("target_id")),
        "target_name": _safe_str(beat.get("target_name")),
        "scene_id": _safe_str(beat.get("scene_id")),
        "location_id": _safe_str(beat.get("location_id")),
        "text": _safe_str(beat.get("text_hint")),
        "structured": {
            "reason": _safe_str(beat.get("reason")),
            "scene_id": _safe_str(beat.get("scene_id")),
            "scene_kind": _safe_str(beat.get("scene_kind")),
            "beat_index": int(beat.get("beat_index", 0) or 0),
        },
        "source": "scene_weaver",
    }


def _apply_ambient_narration_and_delivery(
    *,
    session: dict[str, Any],
    updates: list[dict[str, Any]],
    after_state: dict[str, Any],
    runtime_state: dict[str, Any],
    idle_capture_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = _copy_dict(session)
    runtime_state = _copy_dict(runtime_state)
    current_scene = _safe_dict(runtime_state.get("current_scene"))
    narrated_updates: list[dict[str, Any]] = []

    llm_gateway = None
    try:
        from app.apps.rpg.foundation.provider_access import get_provider
        llm_gateway = get_provider()
    except Exception:
        llm_gateway = None

    runtime_state.setdefault("llm_records", [])
    runtime_state.setdefault("llm_records_index", {})

    # Defensive contract: this helper expects already-enqueued updates
    # so that seq/ambient_id are stable for capture and replacement.
    for idx, update in enumerate(updates):
        update = _safe_dict(update)
        if int(update.get("seq", 0) or 0) <= 0 or not _safe_str(update.get("ambient_id")):
            raise ValueError(
                f"_apply_ambient_narration_and_delivery requires enqueued updates with seq/ambient_id (index={idx})"
            )

    for idx, update in enumerate(updates):
        update = _copy_dict(update)
        narration = narrate_ambient_update(
            ambient_update=update,
            simulation_state=after_state,
            current_scene=current_scene,
            llm_gateway=llm_gateway,
        )
        update["text"] = _safe_str(narration.get("text"))
        update["speaker_turns"] = _safe_list(narration.get("speaker_turns"))
        update["narration"] = {
            "used_app_llm": bool(narration.get("used_app_llm")),
            "raw_llm_narrative": _safe_str(narration.get("raw_llm_narrative")),
            "structured": _safe_dict(narration.get("structured")),
        }
        update["delivery"] = classify_ambient_delivery(session, update, is_typing=False)

        if update["delivery"] == "interrupt":
            session = record_interrupt(session, update)
            runtime_state = _safe_dict(session.get("runtime_state"))
            runtime_state.setdefault("llm_records", [])
            runtime_state.setdefault("llm_records_index", {})

        capture_record = {
            "type": "ambient_narration",
            "idle_capture_key": idle_capture_key,
            "index": idx,
            "ambient_id": _safe_str(update.get("ambient_id")),
            "kind": _safe_str(update.get("kind")),
            "text": _safe_str(update.get("text")),
            "speaker_turns": _safe_list(update.get("speaker_turns")),
            "delivery": _safe_str(update.get("delivery")),
            "narration": _safe_dict(update.get("narration")),
        }
        runtime_state["llm_records"].append(capture_record)
        runtime_state["llm_records_index"][f"{idle_capture_key}:ambient:{idx}"] = capture_record
        narrated_updates.append(update)

    return narrated_updates, runtime_state


def apply_idle_tick(
    session_id: str,
    *,
    reason: str = "heartbeat",
    clock: _Clock = _SYSTEM_CLOCK,
) -> dict[str, Any]:
    session = load_runtime_session(session_id)
    if session is None:
        return {"ok": False, "error": "session_not_found"}

    session = _copy_dict(session)
    simulation_state = session.get("simulation_state")
    simulation_state = simulation_state if isinstance(simulation_state, dict) else {}
    session_seed = simulation_state.get("rng_seed")
    if not isinstance(session_seed, int) or isinstance(session_seed, bool):
        session_seed = _rng_seed_from_session_id(session_id)
    turn_index = simulation_state.get("tick", simulation_state.get("turn_index", 0))
    replay_now, _ = recorded_idle_tick_time(session)
    turn_context = _TurnContext.capture(
        clock,
        session_seed=session_seed if isinstance(session_seed, int) and not isinstance(session_seed, bool) else 0,
        turn_index=turn_index if isinstance(turn_index, int) and not isinstance(turn_index, bool) else 0,
        session_id=session_id,
        now=replay_now,
    )
    with _bind_turn_context(turn_context):
        result = _apply_idle_tick_to_session(session, reason=reason)
    if not result.get("ok"):
        return result

    session = save_runtime_session(_safe_dict(result.get("session")))
    runtime_state = _safe_dict(session.get("runtime_state"))

    return {
        "ok": True,
        "session": session,
        "updates": _safe_list(result.get("updates")),
        "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
        "idle_streak": int(runtime_state.get("idle_streak", 0) or 0),
        "idle_debug_trace": result.get("idle_debug_trace", {}),
        "idle_seconds": result.get("idle_seconds", 0),
        "idle_gate_open": result.get("idle_gate_open", False),
        "settings": result.get("settings", {}),
    }


def apply_idle_ticks(
    session_id: str,
    count: int,
    *,
    reason: str = "heartbeat",
    clock: _Clock = _SYSTEM_CLOCK,
) -> dict[str, Any]:
    """Apply multiple idle ticks, clamped to _MAX_IDLE_TICKS_PER_REQUEST.

    Coalesces results across ticks in memory and saves once at the end.
    """
    count = max(1, min(int(count), _MAX_IDLE_TICKS_PER_REQUEST))
    session = load_runtime_session(session_id)
    if session is None:
        return {"ok": False, "error": "session_not_found"}

    session = _copy_dict(session)
    all_updates: list[dict[str, Any]] = []
    ticks_applied = 0

    for _ in range(count):
        simulation_state = session.get("simulation_state")
        simulation_state = simulation_state if isinstance(simulation_state, dict) else {}
        session_seed = simulation_state.get("rng_seed")
        if not isinstance(session_seed, int) or isinstance(session_seed, bool):
            session_seed = _rng_seed_from_session_id(session_id)
        turn_index = simulation_state.get("tick", simulation_state.get("turn_index", 0))
        replay_now, _ = recorded_idle_tick_time(session)
        turn_context = _TurnContext.capture(
            clock,
            session_seed=session_seed if isinstance(session_seed, int) and not isinstance(session_seed, bool) else 0,
            turn_index=turn_index if isinstance(turn_index, int) and not isinstance(turn_index, bool) else 0,
            session_id=session_id,
            now=replay_now,
        )
        with _bind_turn_context(turn_context):
            result = _apply_idle_tick_to_session(session, reason=reason)
        if not result.get("ok"):
            if ticks_applied == 0:
                return result
            break
        session = _safe_dict(result.get("session"))
        all_updates.extend(_safe_list(result.get("updates")))
        ticks_applied += 1

    session = save_runtime_session(session)
    runtime_state = _safe_dict(session.get("runtime_state"))
    return {
        "ok": True,
        "session": session,
        "updates": all_updates,
        "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
        "idle_streak": int(runtime_state.get("idle_streak", 0) or 0),
        "idle_debug_trace": result.get("idle_debug_trace", {}),
        "idle_seconds": result.get("idle_seconds", 0),
        "idle_gate_open": result.get("idle_gate_open", False),
        "settings": result.get("settings", {}),
    }


def apply_resume_catchup(session_id: str, *, elapsed_seconds: int = 0) -> dict[str, Any]:
    """Apply bounded catch-up ticks on session resume.

    Converts elapsed time to capped idle ticks. If excess ticks would be
    generated, summarizes them into a single catch-up ambient update.
    """
    session = load_runtime_session(session_id)
    if session is None:
        return {"ok": False, "error": "session_not_found"}

    runtime_state = ensure_ambient_runtime_state(_safe_dict(session.get("runtime_state")))

    # Compute ticks from elapsed time (1 tick per ~5 seconds of real time)
    raw_ticks = max(0, elapsed_seconds // 5)
    capped_ticks = min(raw_ticks, _MAX_RESUME_CATCHUP_TICKS)
    excess_ticks = max(0, raw_ticks - capped_ticks)

    if capped_ticks == 0:
        return {
            "ok": True,
            "session": session,
            "updates": [],
            "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
            "ticks_applied": 0,
            "excess_summarized": 0,
        }

    # Apply the capped ticks
    result = apply_idle_ticks(session_id, capped_ticks, reason="resume_catchup")
    if not result.get("ok"):
        return result

    excess_ticks = int(result.get("excess_summarized", 0) or 0)
    ticks_applied = int(result.get("ticks_applied", 0) or 0)
    all_updates = _safe_list(result.get("updates"))
    recap = {}

    # If the world advanced at all, build a resume recap
    if ticks_applied > 0:
        session = _safe_dict(result.get("session"))
        runtime_state = ensure_ambient_runtime_state(_safe_dict(session.get("runtime_state")))

        # Preserve bounded resume metadata for the richer recap payload, but do
        # not enqueue the old one-line system_summary update. The frontend will
        # render the recap block from world_advance_recap instead.
        runtime_state["resume_advance_ticks"] = ticks_applied
        session["runtime_state"] = runtime_state
        session = save_runtime_session(session)

        # 🔥 BUILD RECAP (THIS WAS MISSING)
        simulation_state = _safe_dict(session.get("simulation_state"))

        recap = _build_world_advance_recap(
            simulation_state,
            runtime_state,
            {
                "advance_ticks": ticks_applied,
                "summary": "",
                "scene_title": _safe_str(simulation_state.get("scene_title")),
                "location_name": _safe_str(simulation_state.get("location_name")),
            }
        )

        if not _recap_has_renderable_content(recap):
            recap = _build_resume_fallback_recap(session, runtime_state, ticks_applied)

        result["world_advance_recap"] = recap

    response = {
        "ok": True,
        "session": session if ticks_applied > 0 else _safe_dict(result.get("session")),
        "updates": all_updates,
        "latest_seq": int(result.get("latest_seq", 0) or 0),
        "ticks_applied": ticks_applied,
        "excess_summarized": excess_ticks,
        "world_advance_recap": _safe_dict(recap) if ticks_applied > 0 else _safe_dict(result.get("world_advance_recap")),
    }

    return response

__all__ = ['_apply_ambient_narration_and_delivery', '_make_initiative_update_from_candidate', '_make_scene_update_from_beat', 'apply_idle_tick', 'apply_idle_ticks', 'apply_resume_catchup']
