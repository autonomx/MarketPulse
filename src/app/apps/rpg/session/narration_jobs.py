from __future__ import annotations

# RPG session runtime responsibility module.
import time as _time
import logging
from app.apps.rpg.narration.ai.world_scene_narrator import (
    narrate_scene as narrate_scene,
)
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session, save_runtime_session as save_runtime_session,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _normalize_final_narration_text as _normalize_final_narration_text, _safe_dict as _safe_dict, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _has_narration_artifact_for_turn as _has_narration_artifact_for_turn,
    _store_narration_artifact as _store_narration_artifact,
)
from app.apps.rpg.session.narration_worker import publish_narration_event as publish_narration_event
from app.apps.rpg.session.combat_intent import _utc_now_iso as _utc_now_iso
from app.apps.rpg.narration.ai.grounding_soft_audit import (
    run_grounding_soft_audit as run_grounding_soft_audit,
)
from app.apps.rpg.foundation.llm_app_gateway import (
    build_app_llm_gateway as build_app_llm_gateway,
)
from app.apps.rpg.narration.ai.grounding_settings import (
    normalize_grounding_settings as normalize_grounding_settings,
)
from typing import (
    Any as Any, Callable as Callable, Dict as Dict, List as List, Optional as Optional,
)

logger = logging.getLogger(__name__)

from .state_normalization import _merge_stepped_simulation_state  # noqa: F401


def _ambient_conversation_artifact_line(artifact: dict[str, Any]) -> str:
    artifact = _safe_dict(artifact)
    return _safe_str(artifact.get("line") or artifact.get("narration") or artifact.get("text"))


def _generate_turn_narration_artifact(
    session_id: str,
    narration_request: dict[str, Any],
    on_chunk: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    logger.debug("_generate_turn_narration_artifact called", extra={"session_id": session_id, "turn_id": narration_request.get("turn_id")})
    t0 = _time.monotonic()
    logger.info("[RPG NARRATION ARTIFACT] start session=%s turn_id=%s tick=%s", session_id, narration_request.get("turn_id"), narration_request.get("tick"))
    narration_request = _safe_dict(narration_request)
    turn_id = _safe_str(narration_request.get("turn_id")).strip()
    tick = int(narration_request.get("tick", 0) or 0)
    scene = _safe_dict(narration_request.get("scene"))
    narration_context = _safe_dict(narration_request.get("narration_context"))
    perf = _safe_dict(narration_request.get("performance"))

    streamed_chunks: list[str] = []

    def _emit_chunk(piece: str) -> None:
        piece = _safe_str(piece)
        if not piece:
            return
        streamed_chunks.append(piece)
        if on_chunk:
            try:
                on_chunk(piece)
            except Exception:
                logger.exception("Failed to emit narration chunk")

    llm_enabled = bool(perf.get("enable_live_narration_llm", True))
    retry_on_invalid = bool(perf.get("enable_narration_retry", False))
    llm_gateway = build_app_llm_gateway() if llm_enabled else None
    logger.debug("LLM gateway status", extra={"session_id": session_id, "turn_id": turn_id, "llm_enabled": llm_enabled, "llm_gateway": llm_gateway is not None})

    logger.debug("Calling narrate_scene", extra={"session_id": session_id, "turn_id": turn_id})
    t_narrate0 = _time.monotonic()
    narration_result = narrate_scene(
        scene,
        narration_context,
        llm_gateway=llm_gateway,
        tone="dramatic",
        retry_on_invalid=retry_on_invalid,
        debug_logging=False,
        on_chunk=_emit_chunk,
    )
    logger.info("[RPG NARRATION ARTIFACT] narrate_scene_done session=%s turn_id=%s dt=%.3fs used_llm=%s",
        session_id, turn_id, _time.monotonic() - t_narrate0, bool(_safe_dict(narration_result).get("used_llm")))
    logger.debug("narrate_scene returned", extra={"session_id": session_id, "turn_id": turn_id, "result_keys": list(narration_result.keys()) if isinstance(narration_result, dict) else type(narration_result)})

    narration_result = _safe_dict(narration_result)
    if not _safe_str(narration_result.get("narration")).strip() and streamed_chunks:
        narration_result["narration"] = "".join(streamed_chunks).strip()
    if not _safe_str(narration_result.get("raw_llm_narrative")).strip() and streamed_chunks:
        narration_result["raw_llm_narrative"] = "".join(streamed_chunks).strip()

    final_narration = _normalize_final_narration_text(
        _safe_str(narration_result.get("narration") or narration_result.get("narrative") or "")
    )
    narration_json = _safe_dict(narration_result.get("narration_json"))

    artifact = {
        "turn_id": turn_id,
        "tick": tick,
        "narration": final_narration,
        "narration_json": narration_json,
        "authoritative_action": _safe_str(narration_json.get("action")).strip(),
        "authoritative_reward": _safe_str(narration_json.get("reward")).strip(),
        "authoritative_npc": _safe_dict(narration_json.get("npc")),
        "used_llm": bool(narration_result.get("used_llm")),
        "raw_llm_narrative": _safe_str(narration_result.get("raw_llm_narrative")),
        "narration_context": _safe_dict(narration_request.get("narration_context")),
        "grounding_validation": _safe_dict(
            narration_result.get("grounding_validation")
            or narration_json.get("grounding_validation")
        ),
        "grounding_fallback": bool(
            narration_result.get("grounding_fallback")
            or narration_json.get("grounding_fallback")
        ),
        "speaker_presentation": _safe_dict(narration_result.get("speaker_presentation")),
        "format_warning": bool(narration_result.get("format_warning")),
        "created_at": _utc_now_iso(),
        "artifact_type": "turn_narration",
    }

    session = load_runtime_session(session_id)
    if session is None:
        return {"ok": True, "session": None, "artifact": artifact}

    runtime_state = _copy_dict(session.get("runtime_state"))
    current_tick = int(runtime_state.get("tick", 0) or 0)

    # SAFETY: do not attach narration to a future-overwritten turn
    if tick < current_tick - 1:
        return {
            "ok": False,
            "error": "stale_narration_artifact",
            "artifact": artifact,
        }

    updated_runtime = _store_narration_artifact(runtime_state, artifact)

    # Only merge narration artifact fields back, so a late narration result
    # cannot overwrite newer runtime state from a later committed turn.
    session_runtime = _copy_dict(session.get("runtime_state"))
    session_runtime["narration_artifacts"] = _safe_list(updated_runtime.get("narration_artifacts"))
    session_runtime["narration_artifacts_by_turn"] = _safe_dict(updated_runtime.get("narration_artifacts_by_turn"))
    session["runtime_state"] = session_runtime

    t_save0 = _time.monotonic()
    session = save_runtime_session(session)
    logger.info(
        "[RPG NARRATION ARTIFACT] save_done session=%s turn_id=%s dt=%.3fs total=%.3fs",
        session_id,
        turn_id,
        _time.monotonic() - t_save0,
        _time.monotonic() - t0,
    )

    return {"ok": True, "session": session, "artifact": artifact}


def publish_narration_failure(session_id: str, turn_id: str, error: str, attempt: int, max_attempts: int) -> None:
    try:
        publish_narration_event(
            session_id,
            {
                "type": "narration_job",
                "turn_id": turn_id,
                "status": "failed",
                "retry_count": attempt,
                "max_retries": max_attempts,
                "error": error or "narration_failed",
            },
        )
    except Exception:
        logger.exception("Failed to publish narration failure event")


def run_narration_request(
    session_id: str,
    turn_id: str,
    narration_request: dict[str, Any],
    *,
    job_kind: str = "player_turn",
    tick: int = 0,
    attempt: int = 1,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Narrate one deferred request (an ``rpg.narration`` job, WP-5.6).

    Returns the outcome (``completed``, ``stale``, or ``failed`` for a request
    no attempt can satisfy); raises ``NarrationGenerationError`` when another
    attempt may succeed. A turn already narrated completes without a model
    call, so a retried or duplicate job is harmless.
    """
    from app.apps.rpg.session.jobs.narration_job import NarrationGenerationError, enqueue_rpg_narration

    session = load_runtime_session(session_id)
    if session is None:
        return {"ok": False, "status": "failed", "error": "session_not_found", "turn_id": turn_id}
    runtime_state = _copy_dict(session.get("runtime_state"))
    if _has_narration_artifact_for_turn(runtime_state, turn_id):
        return {"ok": True, "status": "completed", "turn_id": turn_id, "deduped": True}
    current_tick = int(runtime_state.get("tick", 0) or 0)
    # Only ambient narration may be dropped as stale; a player turn's narration must complete.
    if job_kind == "ambient_conversation" and tick < current_tick - 1:
        publish_narration_event(
            session_id,
            {"type": "narration_job", "session_id": session_id, "turn_id": turn_id,
             "tick": tick, "status": "stale", "error": "stale_narration_job"},
        )
        return {"ok": True, "status": "stale", "turn_id": turn_id}
    if not narration_request or not narration_request.get("turn_id"):
        publish_narration_failure(session_id, turn_id, "missing_narration_request", attempt, max_attempts)
        return {"ok": False, "status": "failed", "turn_id": turn_id, "error": "missing_narration_request"}

    try:
        publish_narration_event(
            session_id,
            {"type": "narration_job", "turn_id": turn_id, "status": "processing",
             "retry_count": attempt - 1, "max_retries": max_attempts},
        )
    except Exception:
        logger.exception("Failed to publish narration processing event")

    if job_kind == "grounding_soft_audit":
        audit_result = run_grounding_soft_audit(
            displayed_payload=_safe_dict(narration_request.get("displayed_payload")),
            turn_contract=_safe_dict(narration_request.get("turn_contract")),
            state_snapshot=_safe_dict(narration_request.get("state_snapshot")),
            llm_gateway=build_app_llm_gateway(),
            grounding_settings=_safe_dict(narration_request.get("grounding_settings")),
        )
        correction = _safe_dict(audit_result.get("correction"))
        if audit_result.get("ok") and audit_result.get("correction_needed") and correction:
            publish_narration_event(
                session_id,
                {
                    "type": "grounding_soft_correction",
                    "session_id": session_id,
                    "turn_id": _safe_str(narration_request.get("source_turn_id")),
                    "audit_turn_id": turn_id,
                    "role": "grounding_soft_correction",
                    "append_only": True,
                    "final": True,
                    "version": 1,
                    "text": _safe_str(
                        _safe_dict(correction.get("npc")).get("line")
                        or correction.get("narration")
                        or correction.get("action")
                    ),
                    "correction": correction,
                    "audit": _safe_dict(audit_result.get("audit")),
                },
            )
        return {"ok": True, "status": "completed", "turn_id": turn_id, "soft_audit": audit_result}

    def _on_chunk(piece: str) -> None:
        publish_narration_event(session_id, {"type": "narration_chunk", "turn_id": turn_id, "chunk": piece})

    t_gen = _time.monotonic()
    result = _generate_turn_narration_artifact(session_id, narration_request, on_chunk=_on_chunk)
    logger.info(
        "[RPG NARRATION JOB] generation_end session=%s turn_id=%s ok=%s dt=%.3fs error=%s",
        session_id, turn_id, result.get("ok"), _time.monotonic() - t_gen, result.get("error"),
    )
    if not result.get("ok"):
        if result.get("error") == "stale_narration_artifact":
            return {"ok": True, "status": "stale", "turn_id": turn_id}
        raise NarrationGenerationError(_safe_str(result.get("error")) or "narration_failed")

    artifact = _safe_dict(result.get("artifact"))
    session = _safe_dict(result.get("session")) or session
    publish_narration_event(session_id, {"type": "narration_complete", "turn_id": turn_id, "artifact": artifact})
    if job_kind == "ambient_conversation":
        _publish_ambient_artifact(session_id, turn_id, artifact)
        return {"ok": True, "status": "completed", "turn_id": turn_id, "artifact": artifact}

    narration_event = dict(artifact)
    narration_event.update(
        {
            "type": "narration_artifact",
            "session_id": session_id,
            "turn_id": turn_id,
            "role": "turn_narration",
            "final": True,
            "version": int(artifact.get("version") or 1),
            "tick": artifact.get("tick"),
            "text": _safe_str(artifact.get("narration")),
            "used_llm": bool(artifact.get("used_llm")),
        }
    )
    publish_narration_event(session_id, narration_event)
    try:
        audit_request = _soft_audit_request(session, turn_id, tick, artifact, narration_request)
        if audit_request is not None:
            enqueue_rpg_narration(
                session_id, audit_request["turn_id"], audit_request,
                tick=int(audit_request.get("tick") or 0), job_kind="grounding_soft_audit",
            )
    except Exception:
        logger.exception("Failed to queue the grounding soft audit for session %s turn %s", session_id, turn_id)
    return {"ok": True, "status": "completed", "turn_id": turn_id, "artifact": artifact,
            "attempts": attempt, "max_attempts": max_attempts}


def _publish_ambient_artifact(session_id: str, turn_id: str, artifact: dict[str, Any]) -> None:
    line = _ambient_conversation_artifact_line(artifact)
    speaker = _safe_str(artifact.get("speaker"))
    target = _safe_str(artifact.get("target"))
    if not (speaker or target):
        publish_narration_event(
            session_id,
            {"type": "ambient_conversation_artifact", "session_id": session_id, "turn_id": turn_id,
             "role": "ambient_narration", "tick": artifact.get("tick"),
             "text": _safe_str(artifact.get("narration") or line), "used_llm": bool(artifact.get("used_llm"))},
        )
        return
    conversation_id = _safe_str(artifact.get("conversation_id"))
    if not conversation_id:
        def _norm(value: Any) -> str:
            return _safe_str(value).strip().lower().replace(" ", "_")

        speaker_key = _norm(speaker) or "unknown"
        target_key = _norm(target) or "unknown"
        conversation_id = f"conv_{turn_id}_{speaker_key}_{target_key}"
    publish_narration_event(
        session_id,
        {"type": "npc_conversation_artifact", "session_id": session_id, "turn_id": turn_id,
         "role": "npc_conversation", "conversation_id": conversation_id, "tick": artifact.get("tick"),
         "speaker": speaker, "target": target, "line": line, "text": line,
         "used_llm": bool(artifact.get("used_llm"))},
    )


def _soft_audit_request(
    session: dict[str, Any],
    turn_id: str,
    tick: int,
    artifact: dict[str, Any],
    narration_request: dict[str, Any],
) -> dict[str, Any] | None:
    """The background grounding audit of a turn's narration, when the settings ask for one."""
    runtime_settings = _safe_dict(_safe_dict(session.get("runtime_state")).get("runtime_settings"))
    grounding_settings = normalize_grounding_settings(_safe_dict(runtime_settings.get("grounding")))
    audit_turn_contract = _safe_dict(
        _safe_dict(artifact.get("narration_context")).get("turn_contract")
        or _safe_dict(narration_request.get("narration_context")).get("turn_contract")
        or narration_request.get("turn_contract")
    )
    if not (bool(grounding_settings.get("background_soft_audit", True)) and audit_turn_contract):
        return None
    return {
        "turn_id": f"{turn_id}:grounding_soft_audit",
        "source_turn_id": turn_id,
        "tick": artifact.get("tick") or tick,
        "displayed_payload": _safe_dict(artifact.get("narration_json")) or {
            "format_version": "rpg_narration_v2",
            "narration": _safe_str(artifact.get("narration")),
            "action": "",
            "npc": None,
            "reward": None,
            "followup_hooks": [],
        },
        "turn_contract": audit_turn_contract,
        "state_snapshot": _safe_dict(
            _safe_dict(narration_request.get("narration_context")).get("simulation_state")
            or narration_request.get("simulation_state")
        ),
        "grounding_settings": grounding_settings,
    }


__all__ = ["publish_narration_failure", "run_narration_request"]
