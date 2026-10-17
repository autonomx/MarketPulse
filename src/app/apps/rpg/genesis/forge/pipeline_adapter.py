"""Genesis compiler/bootstrap and World Forge launch pipeline adapter."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .bootstrap import bootstrap_session_from_compiled_genesis
from .compiler import compile_campaign_genesis
from .contract import CampaignGenesisContract
from .legacy_adapter import (
    adapt_genesis_payload_to_new_game_payload,
    attach_genesis_to_created_session,
)
from .materialization import (
    materialize_world_forge_into_session,
    persist_campaign_genesis,
)
from .world_forge_commit import certify_world_forge_commit
from .world_forge_pipeline import CampaignWorldForgeResult, run_campaign_world_forge
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


class _TruthyZero(int):
    """Preserve explicit seed=0 through legacy truthiness checks."""

    def __new__(cls) -> "_TruthyZero":
        return int.__new__(cls, 0)

    def __bool__(self) -> bool:
        return True


def _preserve_seed_zero(request: Any) -> Any:
    if getattr(request, "seed", None) == 0 and not bool(getattr(request, "seed")):
        object.__setattr__(request, "seed", _TruthyZero())
    return request


def attach_compiled_genesis_to_session(
    result: dict[str, Any],
    compiled: dict[str, Any],
    bootstrap: dict[str, Any],
    *,
    sessions: CampaignSessions,
    compact_save: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    if result.get("ok") is not True:
        return result
    session_id = str(result.get("session_id") or "")
    if not session_id:
        return result
    session = raw_session if isinstance(raw_session := result.get("session"), dict) else None
    if session is None:
        session = sessions.load(session_id)
    if not session:
        return result
    state = _safe_dict(session.get("state"))
    metadata = _safe_dict(state.get("metadata"))
    runtime_state = _safe_dict(session.get("runtime_state"))
    setup_payload = _safe_dict(session.get("setup_payload"))
    manifest = _safe_dict(session.get("manifest"))
    state["compiled_genesis_snapshot"] = dict(compiled)
    state["bootstrap_snapshot"] = dict(bootstrap)
    state["active_goals"] = list(bootstrap.get("active_goals") or [])
    state["decision_biases"] = dict(bootstrap.get("decision_biases") or {})
    state["world_traits"] = list(bootstrap.get("world_traits") or [])
    metadata["compiler_version"] = compiled.get("compiler_version")
    state["metadata"] = metadata
    setup_payload["compiled_genesis"] = dict(compiled)
    setup_payload["bootstrap_snapshot"] = dict(bootstrap)
    runtime_state["compiled_genesis_snapshot"] = dict(compiled)
    runtime_state["bootstrap_snapshot"] = dict(bootstrap)
    manifest["compiler_version"] = compiled.get("compiler_version")
    session.update(
        {
            "state": state,
            "setup_payload": setup_payload,
            "runtime_state": runtime_state,
            "manifest": manifest,
        }
    )
    if persist:
        saved = sessions.save(session, compact=compact_save)
    else:
        saved = session
    return {
        **result,
        "session": saved,
        "game": saved.get("state", result.get("game", {})),
    }


def _result_from_unsaved_session(session: dict[str, Any]) -> dict[str, Any]:
    manifest = _safe_dict(session.get("manifest"))
    session_id = str(manifest.get("session_id") or manifest.get("id") or "")
    return {
        "ok": True,
        "session_id": session_id,
        "status": "generating_world",
        "session": session,
        "game": session.get("state", {}),
    }


def _save_prepared_result(result: dict[str, Any], *, sessions: CampaignSessions) -> dict[str, Any]:
    session = raw_session if isinstance(raw_session := result.get("session"), dict) else None
    if session is None:
        return result
    saved_result = sessions.save_created(session)
    return {**result, **saved_result}


def _attach_world_forge_progress(
    result: dict[str, Any],
    world_forge: CampaignWorldForgeResult | None,
    *,
    error: str = "",
) -> dict[str, Any]:
    session_id = str(result.get("session_id") or "")
    from app.apps.rpg.genesis.creation_progress import (
        attach_creation_metadata,
        build_creation_job,
        build_creation_progress_snapshot,
    )

    status = "completed" if not error and result.get("ok") is True else "failed"
    job = build_creation_job(session_id=session_id, status=cast(Any, status), error=error)
    progress = build_creation_progress_snapshot(
        session_id=session_id,
        status=cast(Any, status),
        error=error,
    )
    if world_forge is not None:
        jobs = [row.as_dict() for row in world_forge.generation.jobs]
        completed = sum(1 for row in jobs if row.get("status") == "completed")
        progress.update(
            {
                "stage": (
                    "launch_ready"
                    if world_forge.launch_ready
                    else "consistency_failed"
                ),
                "launch_ready": world_forge.launch_ready,
                "completed_jobs": completed,
                "total_jobs": len(jobs),
                "world_forge_jobs": jobs,
                "topic_graph": world_forge.graph.as_dict(),
                "audit": world_forge.audit.as_dict(),
                "completeness": dict(world_forge.compilation.completeness),
            }
        )
        job.update(
            {
                "kind": "campaign_world_forge",
                "depth": world_forge.graph.depth,
                "launch_ready": world_forge.launch_ready,
            }
        )
    session = result.get("session")
    if isinstance(session, dict):
        session = attach_creation_metadata(session, job, progress)
        result = {
            **result,
            "session": session,
            "game": session.get("state", result.get("game", {})),
        }
    return {**result, "creation_job": job, "creation_progress": progress}


def prepare_new_game_session_from_compiled_genesis(
    *,
    sessions: CampaignSessions,
    bootstrap: dict[str, Any],
    compiled: dict[str, Any],
    contract: CampaignGenesisContract,
    legacy: dict[str, Any],
) -> dict[str, Any]:
    """Build the deterministic blocked shell shared by sync and async launch paths."""

    legacy_request = _preserve_seed_zero(sessions.new_game_request(legacy))
    result = _result_from_unsaved_session(sessions.build_new_game(legacy_request))
    result = attach_genesis_to_created_session(result, contract, persist=False, sessions=sessions)
    return attach_compiled_genesis_to_session(
        result,
        compiled,
        bootstrap,
        persist=False,
        sessions=sessions,
    )


def create_new_game_session_from_compiled_genesis(
    *,
    sessions: CampaignSessions,
    bootstrap: dict[str, Any],
    compiled: dict[str, Any],
    contract: CampaignGenesisContract,
    legacy: dict[str, Any],
    prepared_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = prepared_result or prepare_new_game_session_from_compiled_genesis(
        bootstrap=bootstrap,
        compiled=compiled,
        contract=contract,
        legacy=legacy,
        sessions=sessions,
    )
    session = raw_session if isinstance(raw_session := result.get("session"), dict) else None
    if session is None or not contract.world_forge.enabled:
        result["status"] = "ready"
        return _save_prepared_result(_attach_world_forge_progress(result, None), sessions=sessions)
    session_id = str(result.get("session_id") or "")
    try:
        world_forge = run_campaign_world_forge(
            contract,
            campaign_id=session_id,
            compiled_genesis=compiled,
        )
        certification = certify_world_forge_commit(world_forge)
        result.update(
            {
                "world_forge": world_forge.as_dict(),
                "world_forge_commit_certification": certification.as_dict(),
            }
        )
        if not certification.passed:
            result.update(
                {
                    "ok": False,
                    "status": "blocked",
                    "error": "campaign_genesis_launch_gate_failed",
                    "detail": ",".join(certification.errors),
                    "campaign_genesis_persistence": {
                        "persisted": False,
                        "mode": "rejected_unapproved_canon",
                        "campaign_id": session_id,
                        "content_hash": certification.content_hash,
                        "errors": list(certification.errors),
                    },
                }
            )
            return _attach_world_forge_progress(
                result,
                world_forge,
                error=result["error"],
            )

        session = materialize_world_forge_into_session(
            session,
            contract,
            world_forge,
        )
        result.update(
            {
                "session": session,
                "game": session.get("state", {}),
                "status": "ready",
                "ok": True,
            }
        )
        persistence = persist_campaign_genesis(session, contract, world_forge)
        result["campaign_genesis_persistence"] = persistence
        result = _attach_world_forge_progress(result, world_forge)
        return _save_prepared_result(result, sessions=sessions)
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "failed",
                "error": "campaign_world_forge_failed",
                "detail": str(exc),
            }
        )
        return _attach_world_forge_progress(result, None, error=result["error"])


def create_new_game_from_genesis_payload(payload: dict[str, Any], *, sessions: CampaignSessions) -> dict[str, Any]:
    raw = _safe_dict(payload.get("request") or payload)
    contract_payload = _safe_dict(raw.get("genesis") or raw)
    if "world_forge" not in contract_payload:
        # Older typed Genesis clients predate World Forge. Preserve their fast,
        # single-save behavior; current wizard clients opt in explicitly.
        contract_payload["world_forge"] = {"enabled": False}
    contract = CampaignGenesisContract.model_validate(contract_payload)
    legacy = adapt_genesis_payload_to_new_game_payload(
        {"request": {"genesis": contract.model_dump(mode="json")}}
    )
    compiled = compile_campaign_genesis(contract)
    bootstrap = bootstrap_session_from_compiled_genesis(compiled)
    prepared = prepare_new_game_session_from_compiled_genesis(
        bootstrap=bootstrap,
        compiled=compiled,
        contract=contract,
        legacy=legacy,
        sessions=sessions,
    )
    if contract.world_forge.enabled:
        from .async_coordinator import (
            campaign_genesis_async_enabled,
            campaign_genesis_sync_fallback_allowed,
            enqueue_campaign_genesis,
        )

        if campaign_genesis_async_enabled():
            enqueued = enqueue_campaign_genesis(
                prepared,
                contract=contract,
                compiled=compiled,
                bootstrap=bootstrap,
                legacy=legacy,
                sessions=sessions,
            )
            if (
                enqueued.get("error") != "campaign_genesis_enqueue_failed"
                or not campaign_genesis_sync_fallback_allowed()
            ):
                return enqueued
            # Local/portable launches must remain usable when the optional durable
            # PostgreSQL queue is unavailable. Rebuild a clean in-memory shell so
            # queued/failed metadata from the attempted enqueue cannot leak into
            # the synchronous result.
            prepared = prepare_new_game_session_from_compiled_genesis(
                bootstrap=bootstrap,
                compiled=compiled,
                contract=contract,
                legacy=legacy,
                sessions=sessions,
            )
            prepared["campaign_genesis_async_fallback"] = {
                "used": True,
                "reason": str(enqueued.get("detail") or enqueued.get("error") or ""),
            }
    return create_new_game_session_from_compiled_genesis(
        bootstrap=bootstrap,
        compiled=compiled,
        contract=contract,
        legacy=legacy,
        prepared_result=prepared,
        sessions=sessions,
    )
