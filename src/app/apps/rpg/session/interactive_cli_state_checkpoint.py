"""Checkpoint helpers for interactive CLI state bundles.

This layer is intentionally pure and deterministic. It creates a stable snapshot
envelope around an existing ``interactive_cli_state_bundle`` so save/load phases
can persist and replay one validated payload without allowing presentation text
to become authoritative state.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

INTERACTIVE_CLI_STATE_CHECKPOINT_VERSION = "interactive_cli_state_checkpoint_v1"
INTERACTIVE_CLI_STATE_CHECKPOINT_PATCH = "phase_13_66_interactive_state_checkpoint_v1"
INTERACTIVE_CLI_STATE_CHECKPOINT_SOURCE = "interactive_cli_state_checkpoint"


class InteractiveCliStateCheckpointError(ValueError):
    """Raised when an interactive CLI state checkpoint cannot be restored."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _durable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def interactive_cli_state_bundle_checksum(bundle: Mapping[str, Any]) -> str:
    """Return a deterministic checksum for a state bundle payload."""

    canonical = _canonical_json(_safe_dict(bundle))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_interactive_cli_state_checkpoint(
    bundle: Mapping[str, Any],
    *,
    checkpoint_id: str | None = None,
    turn_index: int | None = None,
) -> dict[str, Any]:
    """Wrap a state bundle in a deterministic checkpoint envelope."""

    bundle_copy = deepcopy(_safe_dict(bundle))
    bundle_turn_index = bundle_copy.get("turn_index")
    resolved_turn_index = int(turn_index if turn_index is not None else bundle_turn_index or 0)
    resolved_checkpoint_id = _safe_str(checkpoint_id or f"interactive-cli-turn-{resolved_turn_index}")
    return {
        "version": INTERACTIVE_CLI_STATE_CHECKPOINT_VERSION,
        "patch": INTERACTIVE_CLI_STATE_CHECKPOINT_PATCH,
        "source": INTERACTIVE_CLI_STATE_CHECKPOINT_SOURCE,
        "checkpoint_id": resolved_checkpoint_id,
        "turn_index": resolved_turn_index,
        "bundle_checksum": interactive_cli_state_bundle_checksum(bundle_copy),
        "bundle": bundle_copy,
        "state_versions": deepcopy(_safe_dict(bundle_copy.get("state_versions"))),
    }


def restore_interactive_cli_state_bundle_from_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    verify_checksum: bool = True,
) -> dict[str, Any]:
    """Restore and verify the bundle stored in a checkpoint envelope."""

    checkpoint_dict = _safe_dict(checkpoint)
    if checkpoint_dict.get("version") != INTERACTIVE_CLI_STATE_CHECKPOINT_VERSION:
        raise InteractiveCliStateCheckpointError("unsupported interactive CLI state checkpoint version")
    bundle = deepcopy(_safe_dict(checkpoint_dict.get("bundle")))
    if not bundle:
        raise InteractiveCliStateCheckpointError("interactive CLI state checkpoint is missing a bundle")
    if verify_checksum:
        expected = _safe_str(checkpoint_dict.get("bundle_checksum"))
        actual = interactive_cli_state_bundle_checksum(bundle)
        if not expected or expected != actual:
            raise InteractiveCliStateCheckpointError("interactive CLI state checkpoint checksum mismatch")
    return bundle


def serialize_interactive_cli_state_checkpoint(checkpoint: Mapping[str, Any]) -> str:
    """Serialize a checkpoint envelope into stable durable JSON text."""

    checkpoint_dict = deepcopy(_safe_dict(checkpoint))
    restore_interactive_cli_state_bundle_from_checkpoint(checkpoint_dict)
    return _durable_json(checkpoint_dict)


def deserialize_interactive_cli_state_checkpoint(payload: str) -> dict[str, Any]:
    """Deserialize stable durable JSON text and verify its stored bundle."""

    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InteractiveCliStateCheckpointError("invalid interactive CLI state checkpoint JSON") from exc
    if not isinstance(loaded, dict):
        raise InteractiveCliStateCheckpointError("interactive CLI state checkpoint JSON must be an object")
    restore_interactive_cli_state_bundle_from_checkpoint(loaded)
    return loaded


def save_interactive_cli_state_checkpoint_file(checkpoint: Mapping[str, Any], path: str | Path) -> Path:
    """Write a verified checkpoint envelope to a durable JSON file."""

    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(serialize_interactive_cli_state_checkpoint(checkpoint), encoding="utf-8")
    return checkpoint_path


def load_interactive_cli_state_checkpoint_file(path: str | Path) -> dict[str, Any]:
    """Read a durable JSON checkpoint file and verify its stored bundle."""

    checkpoint_path = Path(path)
    try:
        payload = checkpoint_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InteractiveCliStateCheckpointError("unable to read interactive CLI state checkpoint file") from exc
    return deserialize_interactive_cli_state_checkpoint(payload)


