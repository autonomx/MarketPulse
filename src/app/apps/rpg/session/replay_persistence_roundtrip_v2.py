from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from app.apps.rpg.session.package_bridge import package_to_session, session_to_package
from app.apps.rpg.session.replay_checkpoint import build_session_checkpoint, compare_session_checkpoints
from app.apps.rpg.session.replay_turn_sequence import validate_replay_turn_sequence
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_save_load_replay_roundtrip_gate"
SaveSession = Callable[[dict[str, Any]], dict[str, Any]]
LoadSession = Callable[[str], dict[str, Any] | None]


def _session_id(session: dict[str, Any]) -> str:
    manifest = _safe_dict(session.get("manifest"))
    return _safe_str(manifest.get("session_id") or manifest.get("id") or "session:unknown")


def _default_save(session: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.session.durable_store import save_session_to_disk

    return save_session_to_disk(session, compact=True)


def _default_load(session_id: str) -> dict[str, Any] | None:
    from app.apps.rpg.session.durable_store import load_session_from_disk

    return load_session_from_disk(session_id)


def _checkpoint(session: dict[str, Any], label: str) -> dict[str, Any]:
    return build_session_checkpoint(session, label=label, turn_index=0)


def _compare(label: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {"label": label, **compare_session_checkpoints(before, after), "source": SOURCE}


def _package_session(session: dict[str, Any]) -> dict[str, Any]:
    result = package_to_session(session_to_package(session))
    if result.get("ok") is not True:
        return {}
    return _safe_dict(result.get("session"))


def run_save_load_replay_persistence_roundtrip(
    session: dict[str, Any],
    commands: list[dict[str, Any]],
    *,
    save_session: SaveSession | None = None,
    load_session: LoadSession | None = None,
    label: str = "phase7.3",
) -> dict[str, Any]:
    session = deepcopy(_safe_dict(session))
    save_fn = save_session or _default_save
    load_fn = load_session or _default_load

    package_baseline = _package_session(session)
    package_loaded = _package_session(package_baseline)
    if not package_baseline or not package_loaded:
        return {"ok": False, "reason": "package_roundtrip_failed", "source": SOURCE}
    package_comparison = _compare(
        "package_roundtrip",
        _checkpoint(package_baseline, f"{label}:package:baseline"),
        _checkpoint(package_loaded, f"{label}:package:loaded"),
    )

    saved_session = save_fn(deepcopy(session))
    loaded_session = load_fn(_session_id(saved_session))
    if not isinstance(loaded_session, dict):
        return {"ok": False, "reason": "disk_roundtrip_missing_session", "source": SOURCE}
    saved_checkpoint = _checkpoint(saved_session, f"{label}:disk:saved")
    loaded_checkpoint = _checkpoint(loaded_session, f"{label}:disk:loaded")
    disk_comparison = _compare("disk_roundtrip", saved_checkpoint, loaded_checkpoint)

    replay_validation = validate_replay_turn_sequence(loaded_checkpoint, _safe_list(commands), label=f"{label}:loaded")
    expected_final = _safe_dict(_safe_dict(replay_validation.get("first")).get("final_checkpoint"))
    expected_validation = validate_replay_turn_sequence(
        saved_checkpoint,
        _safe_list(commands),
        expected_final_checkpoint=expected_final,
        label=f"{label}:saved",
    )

    blockers = []
    for comparison in (package_comparison, disk_comparison):
        if comparison.get("deterministic_match") is not True:
            blockers.append(
                {
                    "kind": f"{comparison.get('label')}_digest_drift",
                    "changed_sections": _safe_list(comparison.get("changed_sections")),
                    "before_digest": _safe_str(comparison.get("before_digest")),
                    "after_digest": _safe_str(comparison.get("after_digest")),
                    "source": SOURCE,
                }
            )
    if replay_validation.get("ok") is not True:
        blockers.append({"kind": "loaded_checkpoint_replay_failed", "source": SOURCE})
    if expected_validation.get("ok") is not True:
        blockers.append({"kind": "saved_vs_loaded_replay_drift", "source": SOURCE})

    return {
        "ok": not blockers,
        "reason": "save_load_replay_persistence_roundtrip_validated" if not blockers else "save_load_replay_persistence_roundtrip_drift_detected",
        "session_id": _session_id(session),
        "package_comparison": package_comparison,
        "disk_comparison": disk_comparison,
        "replay_validation": replay_validation,
        "expected_validation": expected_validation,
        "blockers": blockers,
        "source": SOURCE,
    }


def build_save_load_replay_roundtrip_contract(result: dict[str, Any]) -> dict[str, Any]:
    result = _safe_dict(result)
    return {
        "source": SOURCE,
        "allowed_roundtrip_claims": [
            f"Roundtrip result: {_safe_str(result.get('reason'))}",
            f"Session id: {_safe_str(result.get('session_id'))}",
            f"Blocker count: {len(_safe_list(result.get('blockers')))}",
        ],
        "forbidden_roundtrip_claims": [
            "Provider and LLM calls are outside deterministic persistence roundtrip helpers.",
            "Use durable store or package bridge paths instead of a replacement persistence layer.",
            "Package, disk, checkpoint, and replay digest drift must be surfaced.",
            "Volatile timing/provider diagnostics are not authoritative persistence state.",
        ],
    }


def assert_phase7_save_load_replay_roundtrip_ready() -> dict[str, Any]:
    import tempfile

    from app.apps.rpg.world.locations.discovery import discover_location, discover_route, unblock_route
    from app.apps.rpg.session import durable_store

    session = {
        "manifest": {"id": "phase7:roundtrip", "session_id": "phase7:roundtrip", "title": "Phase 7.3 Roundtrip"},
        "installed_packs": ["base"],
        "simulation_state": {
            "player_state": {
                "inventory_state": {"items": [{"item_id": "ration", "qty": 2}, {"item_id": "water_skin", "qty": 3}]},
                "survival_state": {"hunger": 10, "thirst": 10},
                "currency": {"silver": 5},
            },
            "quest_state": {"active_quests": [{"quest_id": "quest:old_mill", "status": "active"}]},
        },
        "runtime_state": {"elapsed_ms": 999, "provider_latency_ms": 100},
    }
    state = _safe_dict(session["simulation_state"])
    discover_location(state, location_id="location:old_mill", reason="phase7_roundtrip_seed", turn_index=0)
    discover_route(state, edge_id="route:old_road:old_mill", reason="phase7_roundtrip_seed", turn_index=0)
    unblock_route(state, edge_id="route:old_road:old_mill", reason="phase7_roundtrip_seed", turn_index=0)
    commands = [
        {"type": "travel", "command_text": "go to the old road", "roll_encounter": False},
        {"type": "travel", "command_text": "go to the old mill", "roll_encounter": False},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        result = run_save_load_replay_persistence_roundtrip(
            session,
            commands,
            save_session=lambda value: durable_store.save_session_to_disk(
                value,
                compact=True,
                session_dir=session_dir,
            ),
            load_session=lambda session_id: durable_store.load_session_from_disk(
                session_id,
                session_dir=session_dir,
            ),
            label="phase7.3",
        )

    contract = build_save_load_replay_roundtrip_contract(result)
    blockers = list(_safe_list(result.get("blockers")))
    if result.get("ok") is not True:
        blockers.append({"kind": "roundtrip_not_validated", "source": SOURCE})
    if not contract.get("forbidden_roundtrip_claims"):
        blockers.append({"kind": "missing_roundtrip_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_save_load_replay_roundtrip_ready" if not blockers else "phase7_save_load_replay_roundtrip_not_ready",
        "result": result,
        "contract": contract,
        "blockers": blockers,
        "source": SOURCE,
    }
