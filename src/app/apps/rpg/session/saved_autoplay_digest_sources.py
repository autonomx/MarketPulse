from __future__ import annotations

from typing import Any, Dict, List, Tuple
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

SOURCE = "deterministic_phase7_saved_autoplay_digest_source_gate"

_DIGEST_PATHS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "final_checkpoint_digest": (
        ("final_checkpoint_digest",),
        ("checkpoint", "final_checkpoint_digest"),
        ("checkpoint", "digest"),
        ("checkpoint", "final", "digest"),
        ("checkpoint", "final", "checkpoint_digest"),
        ("final_checkpoint", "digest"),
        ("final_checkpoint", "checkpoint_digest"),
        ("artifacts", "checkpoint", "final_checkpoint_digest"),
    ),
    "loaded_checkpoint_digest": (
        ("loaded_checkpoint_digest",),
        ("checkpoint", "loaded_checkpoint_digest"),
        ("checkpoint", "loaded", "digest"),
        ("checkpoint", "loaded", "checkpoint_digest"),
        ("loaded_checkpoint", "digest"),
        ("loaded_checkpoint", "checkpoint_digest"),
        ("load", "loaded_checkpoint_digest"),
        ("replay", "loaded_checkpoint_digest"),
    ),
    "expected_final_checkpoint_digest": (
        ("expected_final_checkpoint_digest",),
        ("checkpoint", "expected_final_checkpoint_digest"),
        ("checkpoint", "expected", "digest"),
        ("checkpoint", "expected", "checkpoint_digest"),
        ("expected_checkpoint", "digest"),
        ("expected_final_checkpoint", "digest"),
        ("expected_final_checkpoint", "checkpoint_digest"),
    ),
    "final_state_digest": (
        ("final_state_digest",),
        ("state", "final_state_digest"),
        ("state", "digest"),
        ("state", "final", "digest"),
        ("state", "final", "state_digest"),
        ("final_state", "digest"),
        ("final_state", "state_digest"),
    ),
    "loaded_state_digest": (
        ("loaded_state_digest",),
        ("state", "loaded_state_digest"),
        ("state", "loaded", "digest"),
        ("state", "loaded", "state_digest"),
        ("loaded_state", "digest"),
        ("loaded_state", "state_digest"),
        ("load", "loaded_state_digest"),
        ("replay", "loaded_state_digest"),
    ),
    "expected_final_state_digest": (
        ("expected_final_state_digest",),
        ("state", "expected_final_state_digest"),
        ("state", "expected", "digest"),
        ("state", "expected", "state_digest"),
        ("expected_state", "digest"),
        ("expected_final_state", "digest"),
        ("expected_final_state", "state_digest"),
    ),
}


def _path_value(mapping: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        current = _safe_dict(current).get(key)
        if current is None:
            return None
    return current


def _source_path(path: Tuple[str, ...]) -> str:
    return ".".join(path)


def capture_saved_autoplay_digest_sources(saved_artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Capture source-backed checkpoint/state digests from saved manual/autoplay artifacts."""

    saved_artifacts = _safe_dict(saved_artifacts)
    digests: Dict[str, str] = {}
    metadata: List[Dict[str, Any]] = []
    for digest_key, paths in _DIGEST_PATHS.items():
        for path in paths:
            digest = _safe_str(_path_value(saved_artifacts, path))
            if digest:
                digests[digest_key] = digest
                metadata.append(
                    {
                        "kind": digest_key,
                        "digest": digest,
                        "source_path": _source_path(path),
                        "source": SOURCE,
                    }
                )
                break
    return {
        "digests": digests,
        "metadata": metadata,
        "state_diff_source": SOURCE if metadata else "",
        "source": SOURCE,
    }


def build_saved_autoplay_digest_source_contract(capture: Dict[str, Any]) -> Dict[str, Any]:
    capture = _safe_dict(capture)
    digests = _safe_dict(capture.get("digests"))
    return {
        "source": SOURCE,
        "captured_digest_keys": sorted(digests.keys()),
        "allowed_claims": [
            "Digest metadata identifies where saved checkpoint/state digests were captured.",
            "Digest comparison is deterministic and provider-free.",
            "Digest mismatch blockers must remain source-backed for saved certification payloads.",
        ],
        "forbidden_claims": [
            "Do not invent missing checkpoint or state digests.",
            "Do not call providers, LLMs, subprocesses, HTTP clients, or live autoplay.",
            "Do not treat missing digest metadata as a passing save/load comparison.",
            "Do not hide final/loaded/expected checkpoint or state digest mismatches.",
        ],
    }


def assert_phase7_saved_autoplay_digest_source_ready() -> Dict[str, Any]:
    saved = {
        "checkpoint": {
            "final": {"digest": "digest:checkpoint:final"},
            "loaded": {"digest": "digest:checkpoint:final"},
            "expected": {"digest": "digest:checkpoint:final"},
        },
        "state": {
            "final": {"digest": "digest:state:final"},
            "loaded": {"digest": "digest:state:final"},
            "expected": {"digest": "digest:state:final"},
        },
    }
    capture = capture_saved_autoplay_digest_sources(saved)
    contract = build_saved_autoplay_digest_source_contract(capture)
    digests = _safe_dict(capture.get("digests"))
    blockers: List[Dict[str, Any]] = []
    required = set(_DIGEST_PATHS.keys())
    missing = sorted(required - set(digests.keys()))
    if missing:
        blockers.append({"kind": "missing_digest_capture", "missing": missing, "source": SOURCE})
    if len(capture.get("metadata", [])) != len(required):
        blockers.append({"kind": "missing_digest_source_metadata", "source": SOURCE})
    if not contract.get("forbidden_claims"):
        blockers.append({"kind": "missing_digest_source_contract_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_saved_autoplay_digest_source_gate_ready"
        if not blockers
        else "phase7_saved_autoplay_digest_source_gate_not_ready",
        "capture": capture,
        "contract": contract,
        "blockers": blockers,
        "source": SOURCE,
    }
