"""RPG replay/persistence adapter for shared platform contracts."""
from __future__ import annotations

from typing import Any

from .models import CheckpointEnvelope, PersistenceInventory, ReplayPrimitive, ReplayPrimitiveList, StateHashResponse


RPG_REPLAY_ADAPTER_VERSION = "rpg_replay_persistence_adapter_v1"


class RpgReplayPersistenceAdapter:
    """Delegate to RPG-owned replay and persistence helpers."""

    def list_primitives(self) -> ReplayPrimitiveList:
        return ReplayPrimitiveList(
            primitives=[
                ReplayPrimitive(
                    kind="provider_recording",
                    source="src/app/apps/rpg/foundation/core/llm_recording.py",
                    owner_module="rpg",
                    behavior="Records provider outputs by stable prompt/context/config keys and refuses missing replay records.",
                    compatibility_policy="Expose as platform metadata only; do not change recording keys without replay tests.",
                ),
                ReplayPrimitive(
                    kind="state_hash",
                    source="src/app/apps/rpg/rules/validation/state_hash.py",
                    owner_module="rpg",
                    behavior="Computes deterministic state fingerprints with stable serialization.",
                    compatibility_policy="Use as the first shared hash delegate; RPG keeps hash semantics.",
                ),
                ReplayPrimitive(
                    kind="checkpoint",
                    source="src/app/apps/rpg/session/interactive_cli_state_checkpoint.py",
                    owner_module="rpg",
                    behavior="Creates checksum-backed interactive CLI checkpoint envelopes and verifies restore.",
                    compatibility_policy="Shared wrapper must preserve version and checksum behavior.",
                ),
                ReplayPrimitive(
                    kind="session_persistence",
                    source="src/app/apps/rpg/session/durable_store.py",
                    owner_module="rpg",
                    behavior="Reads, migrates, normalizes, atomically writes, and quarantines corrupt RPG sessions.",
                    compatibility_policy="Delegate first; do not move saved sessions or alter quarantine behavior.",
                ),
                ReplayPrimitive(
                    kind="migration",
                    source="src/app/apps/rpg/foundation/persistence/migration_manager.py",
                    owner_module="rpg",
                    behavior="Versioned save-package migration with explicit version advancement.",
                    compatibility_policy="Use migration reports as diagnostics before any storage move.",
                ),
            ]
        )

    def state_hash(self, state: dict[str, Any]) -> StateHashResponse:
        from app.apps.rpg.rules.validation.state_hash import stable_serialize
        import hashlib
        import json

        stable = stable_serialize(state)
        digest = hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return StateHashResponse(
            hash=digest,
            source="app.apps.rpg.rules.validation.state_hash.stable_serialize",
            format_version=RPG_REPLAY_ADAPTER_VERSION,
        )

    def create_checkpoint(self, bundle: dict[str, Any], *, checkpoint_id: str | None = None) -> CheckpointEnvelope:
        from app.apps.rpg.session.interactive_cli_state_checkpoint import create_interactive_cli_state_checkpoint

        checkpoint = create_interactive_cli_state_checkpoint(bundle, checkpoint_id=checkpoint_id)
        return self._checkpoint_to_envelope(checkpoint)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        from app.apps.rpg.session.interactive_cli_state_checkpoint import restore_interactive_cli_state_bundle_from_checkpoint

        return restore_interactive_cli_state_bundle_from_checkpoint(checkpoint)

    def save_checkpoint_file(self, checkpoint: dict[str, Any], path: str) -> str:
        from app.apps.rpg.session.interactive_cli_state_checkpoint import save_interactive_cli_state_checkpoint_file

        return str(save_interactive_cli_state_checkpoint_file(checkpoint, path))

    def load_checkpoint_file(self, path: str) -> CheckpointEnvelope:
        from app.apps.rpg.session.interactive_cli_state_checkpoint import load_interactive_cli_state_checkpoint_file

        return self._checkpoint_to_envelope(load_interactive_cli_state_checkpoint_file(path))

    def list_sessions(self) -> PersistenceInventory:
        from app.apps.rpg.session.durable_store import list_sessions_from_disk

        sessions: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for session in list_sessions_from_disk():
            if not isinstance(session, dict):
                diagnostics.append({"kind": "invalid_session", "source": "rpg_session"})
                continue

            manifest = raw_manifest if isinstance(raw_manifest := session.get("manifest"), dict) else {}
            state = raw_state if isinstance(raw_state := session.get("state"), dict) else {}
            metadata = raw_metadata if isinstance(raw_metadata := state.get("metadata"), dict) else {}
            session_id = manifest.get("session_id") or manifest.get("id") or state.get("session_id") or ""
            if not session_id:
                diagnostics.append({"kind": "missing_manifest", "source": "rpg_session"})
                continue

            sessions.append(
                {
                    "session_id": session_id,
                    "id": session_id,
                    "title": manifest.get("title") or state.get("title") or session_id,
                    "archived": bool(manifest.get("archived")),
                    "updated_at": manifest.get("updated_at") or state.get("updated_at"),
                    "created_at": manifest.get("created_at"),
                    "location": state.get("location") or state.get("current_location") or metadata.get("location"),
                    "summary": state.get("summary"),
                    "turn_count": state.get("turn_count") or state.get("current_turn"),
                    "checkpoint": manifest.get("checkpoint") or manifest.get("checkpoint_id") or "Autosave session",
                    "metadata": {**metadata, "manifest": manifest},
                    "state": state,
                    "payload": raw_setup_payload if isinstance(raw_setup_payload := session.get("setup_payload"), dict) else {},
                }
            )
        return PersistenceInventory(sessions=sessions, diagnostics=diagnostics)

    def _checkpoint_to_envelope(self, checkpoint: dict[str, Any]) -> CheckpointEnvelope:
        return CheckpointEnvelope(
            checkpoint_id=str(checkpoint.get("checkpoint_id") or ""),
            version=str(checkpoint.get("version") or ""),
            source=str(checkpoint.get("source") or ""),
            checksum=str(checkpoint.get("bundle_checksum") or ""),
            payload=dict(checkpoint.get("bundle") or {}),
            metadata={
                "patch": checkpoint.get("patch") or "",
                "turn_index": checkpoint.get("turn_index"),
                "state_versions": dict(checkpoint.get("state_versions") or {}),
            },
        )


def default_rpg_replay_adapter() -> RpgReplayPersistenceAdapter:
    return RpgReplayPersistenceAdapter()
