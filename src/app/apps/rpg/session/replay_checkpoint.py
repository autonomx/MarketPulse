from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_replay_checkpoint_foundation"
CHECKPOINT_SCHEMA_VERSION = 1
VOLATILE_RUNTIME_KEYS = {
    "last_saved_at",
    "wall_time_ms",
    "elapsed_ms",
    "provider_latency_ms",
    "narration_trace",
    "turn_perf_trace",
}


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _safe_str(value)


def _strip_volatile_runtime_state(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}
    for key, value in _safe_dict(runtime_state).items():
        if str(key) in VOLATILE_RUNTIME_KEYS:
            continue
        clean[str(key)] = value
    return clean


def _normalized_session(session: Dict[str, Any]) -> Dict[str, Any]:
    session = _safe_dict(session)
    manifest = _safe_dict(session.get("manifest"))
    session_id = _safe_str(manifest.get("session_id") or manifest.get("id") or "session:unknown")
    normalized_manifest = {
        **deepcopy(manifest),
        "id": _safe_str(manifest.get("id") or session_id),
        "session_id": session_id,
    }
    return _clean_json(
        {
            "manifest": normalized_manifest,
            "installed_packs": sorted(_safe_list(session.get("installed_packs")), key=_safe_str),
            "simulation_state": deepcopy(_safe_dict(session.get("simulation_state"))),
            "runtime_state": _strip_volatile_runtime_state(_safe_dict(session.get("runtime_state"))),
        }
    )


def canonical_session_json(session: Dict[str, Any]) -> str:
    return json.dumps(_normalized_session(session), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def session_checkpoint_digest(session: Dict[str, Any]) -> str:
    payload = canonical_session_json(session).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_session_checkpoint(
    session: Dict[str, Any],
    *,
    label: str = "",
    turn_index: int = 0,
) -> Dict[str, Any]:
    normalized = _normalized_session(session)
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = _safe_dict(normalized.get("manifest"))
    return {
        "ok": True,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "label": _safe_str(label),
        "turn_index": int(turn_index or 0),
        "session_id": _safe_str(manifest.get("session_id") or manifest.get("id")),
        "digest": digest,
        "session": normalized,
        "source": SOURCE,
    }


def restore_session_from_checkpoint(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    checkpoint = _safe_dict(checkpoint)
    session = deepcopy(_safe_dict(checkpoint.get("session")))
    if not session:
        return {"ok": False, "reason": "missing_checkpoint_session", "source": SOURCE}
    expected = _safe_str(checkpoint.get("digest"))
    actual = session_checkpoint_digest(session)
    if expected and actual != expected:
        return {
            "ok": False,
            "reason": "checkpoint_digest_mismatch",
            "expected_digest": expected,
            "actual_digest": actual,
            "source": SOURCE,
        }
    return {"ok": True, "session": session, "digest": actual, "source": SOURCE}


def compare_session_checkpoints(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before = _safe_dict(before)
    after = _safe_dict(after)
    before_digest = _safe_str(before.get("digest"))
    after_digest = _safe_str(after.get("digest"))
    changed_sections = []
    before_session = _safe_dict(before.get("session"))
    after_session = _safe_dict(after.get("session"))
    for section in ("manifest", "installed_packs", "simulation_state", "runtime_state"):
        if before_session.get(section) != after_session.get(section):
            changed_sections.append(section)
    return {
        "ok": bool(before_digest and after_digest),
        "deterministic_match": before_digest == after_digest and bool(before_digest),
        "before_digest": before_digest,
        "after_digest": after_digest,
        "changed_sections": changed_sections,
        "source": SOURCE,
    }


def build_replay_checkpoint_contract(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    checkpoint = _safe_dict(checkpoint)
    return {
        "source": SOURCE,
        "allowed_checkpoint_claims": [
            f"Checkpoint digest: {_safe_str(checkpoint.get('digest'))}",
            f"Session id: {_safe_str(checkpoint.get('session_id'))}",
            f"Turn index: {int(checkpoint.get('turn_index') or 0)}",
        ],
        "forbidden_checkpoint_claims": [
            "Do not call providers or LLMs to build, restore, or compare replay checkpoints.",
            "Do not mutate gameplay state while building or comparing checkpoints.",
            "Do not ignore digest mismatches during restore or replay comparison.",
            "Do not treat volatile timing/provider diagnostics as authoritative replay state.",
        ],
    }


def assert_phase7_replay_checkpoint_foundation_ready() -> Dict[str, Any]:
    session = {
        "manifest": {"id": "phase7:test", "session_id": "phase7:test"},
        "installed_packs": ["base"],
        "simulation_state": {
            "player_state": {"inventory_state": {"items": [{"item_id": "ration", "qty": 1}]}},
            "travel_state": {"current_location_id": "location:rusty_flagon"},
        },
        "runtime_state": {"tick": 2, "elapsed_ms": 999},
    }
    first = build_session_checkpoint(session, label="first", turn_index=2)
    second = build_session_checkpoint(deepcopy(session), label="second", turn_index=2)
    restored = restore_session_from_checkpoint(first)
    comparison = compare_session_checkpoints(first, second)
    contract = build_replay_checkpoint_contract(first)
    blockers = []
    if first.get("digest") != second.get("digest"):
        blockers.append({"kind": "checkpoint_digest_not_deterministic", "source": SOURCE})
    if restored.get("ok") is not True:
        blockers.append({"kind": "checkpoint_restore_failed", "source": SOURCE})
    if comparison.get("deterministic_match") is not True:
        blockers.append({"kind": "checkpoint_comparison_not_matching", "source": SOURCE})
    if not contract.get("forbidden_checkpoint_claims"):
        blockers.append({"kind": "missing_checkpoint_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_replay_checkpoint_foundation_ready" if not blockers else "phase7_replay_checkpoint_foundation_not_ready",
        "first": first,
        "restored": restored,
        "comparison": comparison,
        "blockers": blockers,
        "source": SOURCE,
    }
