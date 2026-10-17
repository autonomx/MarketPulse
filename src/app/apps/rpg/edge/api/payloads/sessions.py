"""RPG session compatibility helpers for gateway bridge routes."""
from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_mutable_session(session_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    from app.apps.rpg.session.service import load_session

    session = load_session(session_id)
    if not session:
        return None, {}
    state = _safe_dict(session.get("state"))
    session["state"] = state
    return session, state


def _item_action_request_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(
        payload.get("item_action")
        or payload.get("item_session_action")
        or payload.get("request")
    )
    if request:
        return dict(request)

    action_kind = (
        payload.get("item_action_kind")
        or payload.get("item_kind")
        or payload.get("session_action")
        or payload.get("kind")
    )
    request = {
        key: value
        for key, value in payload.items()
        if key not in {"action", "session_id"}
    }
    request["action"] = _safe_str(action_kind).strip()
    return request


def list_rpg_sessions_payload() -> dict[str, Any]:
    """Return the legacy RPG session list envelope plus launch presets."""
    from app.apps.rpg.session.new_game import list_rpg_presets
    from app.apps.rpg.session.service import list_sessions

    presets_payload = list_rpg_presets()
    return {
        "ok": True,
        "sessions": list_sessions() or [],
        "presets": presets_payload.get("presets", []),
    }


def get_rpg_session_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Return a session envelope or perform a synchronous RPG action.

    This gateway-compat route is used by the web app while typed RPG endpoints
    are being promoted. Supported actions are synchronous and do not call
    LLM/image/TTS services:

    - {"action": "new_game", ...}
    - {"action": "start_preset", "preset_id": "demo_glimmerdeep_pass_lvl14"}
    - {"action": "continue", "session_id": "..."}
    - {"action": "rename", "session_id": "...", "name": "..."}
    - {"action": "delete", "session_id": "..."}
    - {"action": "loadout_action", "session_id": "...", "loadout": {...}}
    - {"action": "item_action", "session_id": "...", "item_action": {...}}
    - {"action": "item_command", "session_id": "...", "command": "item report"}
    - {"action": "item_detail", "session_id": "...", "item_name": "Torch"}
    - {"action": "ability_detail", "session_id": "...", "ability_name": "Frost Arrow"}
    - {"action": "item_resolve", "session_id": "...", "input": {...}}
    - {"action": "item_diagnostics", "session_id": "...", "record": true}
    - {"action": "item_maintenance", "session_id": "...", "dry_run": true}
    - {"action": "item_objectives", "session_id": "..."}
    - {"action": "item_scenario", "session_id": "...", "run": true}
    """
    payload = _safe_dict(data)
    action = _safe_str(payload.get("action")).strip()

    if action == "new_game":
        from app.apps.rpg.session.new_game import RpgNewGameRequest, create_new_game_session

        request = RpgNewGameRequest.model_validate(payload.get("request") or payload)
        return create_new_game_session(request)

    if action == "start_preset":
        from app.apps.rpg.session.new_game import start_rpg_preset

        preset_id = _safe_str(payload.get("preset_id")).strip()
        return start_rpg_preset(preset_id)

    if action == "continue":
        from app.apps.rpg.session.new_game import continue_rpg_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        return continue_rpg_session(session_id)

    if action == "rename":
        from app.apps.rpg.session.new_game import rename_rpg_session

        session_id = _safe_str(payload.get("session_id")).strip()
        name = _safe_str(payload.get("name")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        if not name:
            return {"ok": False, "error": "missing_session_name", "session_id": session_id}
        return rename_rpg_session(session_id, name)

    if action == "delete":
        from app.apps.rpg.session.new_game import delete_rpg_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        return delete_rpg_session(session_id)

    if action == "loadout_action":
        from app.apps.rpg.session.loadout import RpgLoadoutActionRequest
        from app.apps.rpg.session.loadout_with_hooks import apply_loadout_action_with_item_hooks

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        loadout_request = RpgLoadoutActionRequest.model_validate(payload.get("loadout") or payload)
        return apply_loadout_action_with_item_hooks(
            session_id,
            loadout_request,
            diagnostics_interval=_safe_int(payload.get("diagnostics_interval"), default=10),
            maintenance_interval=_safe_int(payload.get("maintenance_interval"), default=25),
            report_interval=_safe_int(payload.get("report_interval"), default=20),
            objective_limit=_safe_int(payload.get("objective_limit"), default=5),
            record_trace=_safe_bool(payload.get("record_trace"), default=True),
            record_hook_trace=_safe_bool(payload.get("record_hook_trace"), default=True),
        )

    if action == "item_action":
        from app.apps.rpg.session.item_session_with_hooks import apply_item_session_action_with_hooks
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}

        item_request = _item_action_request_from_payload(payload)
        result = apply_item_session_action_with_hooks(
            state,
            item_request,
            station=_safe_str(payload.get("station")).strip() or None,
            genre=_safe_str(payload.get("genre")).strip() or "classic_fantasy",
            diagnostics_interval=_safe_int(payload.get("diagnostics_interval"), default=10),
            maintenance_interval=_safe_int(payload.get("maintenance_interval"), default=25),
            report_interval=_safe_int(payload.get("report_interval"), default=20),
            objective_limit=_safe_int(payload.get("objective_limit"), default=5),
            record_trace=_safe_bool(payload.get("record_trace"), default=True),
            record_hook_trace=_safe_bool(payload.get("record_hook_trace"), default=True),
        )
        if result.get("ok") is not True:
            return {"session_id": session_id, **result}

        saved = save_session(session, compact=False)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "session": saved,
            "game": saved.get("state", {}),
            **result,
        }

    if action == "item_command":
        from app.apps.rpg.session.item_session_with_hooks import apply_item_command_with_hooks
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        command = (
            payload.get("command")
            if "command" in payload
            else payload.get("item_command") or payload.get("request")
        )
        result = apply_item_command_with_hooks(
            state,
            command,
            station=_safe_str(payload.get("station")).strip() or None,
            genre=_safe_str(payload.get("genre")).strip() or "classic_fantasy",
            diagnostics_interval=_safe_int(payload.get("diagnostics_interval"), default=10),
            maintenance_interval=_safe_int(payload.get("maintenance_interval"), default=25),
            report_interval=_safe_int(payload.get("report_interval"), default=20),
            objective_limit=_safe_int(payload.get("objective_limit"), default=5),
            record_trace=_safe_bool(payload.get("record_trace"), default=True),
            record_hook_trace=_safe_bool(payload.get("record_hook_trace"), default=True),
        )
        if result.get("ok") is not True:
            return {"session_id": session_id, **result}
        saved = save_session(session, compact=False)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "session": saved,
            "game": saved.get("state", {}),
            **result,
        }

    if action == "item_detail":
        from app.apps.rpg.session.item_detail import generate_item_detail

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        _session, state = _load_mutable_session(session_id)
        if not _session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        item_name = _safe_str(payload.get("item_name") or payload.get("item_id")).strip()
        if not item_name:
            return {"ok": False, "error": "missing_item_name", "session_id": session_id}
        result = generate_item_detail(
            state,
            item_name,
            genre=_safe_str(payload.get("genre")).strip() or None,
        )
        return {"session_id": session_id, **result}

    if action == "ability_detail":
        from app.apps.rpg.session.ability_detail import generate_ability_detail

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        ability_name = _safe_str(payload.get("ability_name") or payload.get("ability_id")).strip()
        if not ability_name:
            return {"ok": False, "error": "missing_ability_name", "session_id": session_id}
        result = generate_ability_detail(
            state,
            ability_name,
            genre=_safe_str(payload.get("genre")).strip() or None,
        )
        return {"session_id": session_id, **result}

    if action == "item_resolve":
        from app.apps.rpg.session.item_action_resolution import apply_item_action_input
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        item_input = payload.get("input") if "input" in payload else payload.get("request")
        if item_input is None:
            item_input = payload.get("command") or payload.get("item_command") or payload
        result = apply_item_action_input(
            state,
            item_input,
            current_turn=_safe_int(payload.get("current_turn"), default=0) or None,
            station=_safe_str(payload.get("station")).strip() or None,
            genre=_safe_str(payload.get("genre")).strip() or "classic_fantasy",
            diagnostics_interval=_safe_int(payload.get("diagnostics_interval"), default=10),
            maintenance_interval=_safe_int(payload.get("maintenance_interval"), default=25),
            report_interval=_safe_int(payload.get("report_interval"), default=20),
            objective_limit=_safe_int(payload.get("objective_limit"), default=5),
            record_trace=_safe_bool(payload.get("record_trace"), default=True),
            record_hook_trace=_safe_bool(payload.get("record_hook_trace"), default=True),
        )
        if result.get("ok") is not True:
            return {"session_id": session_id, **result}
        if result.get("handled") is not True:
            return {
                "ok": True,
                "session_id": session_id,
                "status": "ready",
                "game": state,
                **result,
            }
        saved = save_session(session, compact=False)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "session": saved,
            "game": saved.get("state", {}),
            **result,
        }

    if action == "item_diagnostics":
        from app.apps.rpg.session.item_diagnostics import (
            build_item_diagnostics,
            record_item_diagnostics,
        )
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        station = _safe_str(payload.get("station")).strip() or None
        genre = _safe_str(payload.get("genre")).strip() or "classic_fantasy"
        scenario_limit = _safe_int(payload.get("scenario_limit"), default=8)
        objective_limit = _safe_int(payload.get("objective_limit"), default=8)
        if _safe_bool(payload.get("record") or payload.get("record_trace")):
            diagnostics = record_item_diagnostics(
                state,
                station=station,
                genre=genre,
                scenario_limit=scenario_limit,
                objective_limit=objective_limit,
            )
            saved = save_session(session, compact=False)
            return {
                "ok": True,
                "session_id": session_id,
                "status": "ready",
                "session": saved,
                "game": saved.get("state", {}),
                "diagnostics": diagnostics,
            }
        diagnostics = build_item_diagnostics(
            state,
            station=station,
            genre=genre,
            scenario_limit=scenario_limit,
            objective_limit=objective_limit,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "game": state,
            "diagnostics": diagnostics,
        }

    if action == "item_maintenance":
        from app.apps.rpg.session.item_state_maintenance import (
            build_item_state_maintenance_plan,
            run_item_state_maintenance,
        )
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        bucket_limit = _safe_int(payload.get("bucket_limit"), default=50)
        compaction_threshold = _safe_int(payload.get("compaction_threshold"), default=bucket_limit)
        record_report = _safe_bool(payload.get("record_report"), default=False)
        if _safe_bool(payload.get("dry_run"), default=False):
            maintenance = build_item_state_maintenance_plan(
                state,
                bucket_limit=bucket_limit,
                compaction_threshold=compaction_threshold,
                include_report=record_report,
            )
            return {
                "ok": True,
                "session_id": session_id,
                "status": "ready",
                "game": state,
                "maintenance": maintenance,
            }
        maintenance = run_item_state_maintenance(
            state,
            bucket_limit=bucket_limit,
            compaction_threshold=compaction_threshold,
            record_report=record_report,
        )
        saved = save_session(session, compact=False)
        return {
            "ok": maintenance.get("ok") is True,
            "session_id": session_id,
            "status": "ready",
            "session": saved,
            "game": saved.get("state", {}),
            "maintenance": maintenance,
        }

    if action == "item_objectives":
        from app.apps.rpg.session.item_objectives import build_item_objectives

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        station = _safe_str(payload.get("station")).strip() or None
        genre = _safe_str(payload.get("genre")).strip() or "classic_fantasy"
        limit = _safe_int(payload.get("limit") or payload.get("objective_limit"), default=6)
        objectives = build_item_objectives(state, station=station, genre=genre, limit=limit)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "game": state,
            "objectives": objectives,
        }

    if action == "item_scenario":
        from app.apps.rpg.session.item_scenarios import build_item_scenario_plan, run_item_scenario
        from app.apps.rpg.session.service import save_session

        session_id = _safe_str(payload.get("session_id")).strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        session, state = _load_mutable_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id}
        station = _safe_str(payload.get("station")).strip() or None
        genre = _safe_str(payload.get("genre")).strip() or "classic_fantasy"
        limit = _safe_int(payload.get("limit") or payload.get("scenario_limit"), default=8)
        include_status_steps = _safe_bool(payload.get("include_status_steps"), default=True)
        if _safe_bool(payload.get("run"), default=False):
            steps = _safe_list(payload.get("steps")) or None
            source = _safe_str(payload.get("source")).strip() or "item_scenario_compat"
            scenario = run_item_scenario(
                state,
                steps=steps,
                station=station,
                genre=genre,
                limit=limit,
                source=source,
            )
            saved = save_session(session, compact=False)
            return {
                "ok": scenario.get("ok") is True,
                "session_id": session_id,
                "status": "ready",
                "session": saved,
                "game": saved.get("state", {}),
                "scenario": scenario,
            }
        scenario = build_item_scenario_plan(
            state,
            station=station,
            genre=genre,
            limit=limit,
            include_status_steps=include_status_steps,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ready",
            "game": state,
            "scenario": scenario,
        }

    session_id = _safe_str(payload.get("session_id")).strip()
    if not session_id:
        return {"ok": False, "error": "missing_session_id"}

    from app.apps.rpg.session.action_execution import (
        build_frontend_bootstrap_payload as build_frontend_bootstrap_payload,
    )
    from app.apps.rpg.session.session_runtime_store import (
        load_runtime_session as load_runtime_session,
    )

    session = load_runtime_session(session_id)
    if not session:
        return {"ok": False, "error": "session_not_found", "session_id": session_id}
    game = _safe_dict(build_frontend_bootstrap_payload(session))
    game["session_id"] = session_id
    return {"ok": True, "game": game}
