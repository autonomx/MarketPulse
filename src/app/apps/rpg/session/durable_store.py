"""Phase 15.0 — Durable session save/load to disk-backed JSON snapshots.

Provides versioned, migration-safe, disk-backed session persistence.
Never trusts raw disk payloads; normalizes input on load.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, cast

from app.apps.rpg.session.migrations import migrate_session_payload
from app.apps.rpg.session.session_store import _normalize_session
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict
from app.runtime.paths import repo_root, rpg_sessions_root
from app.apps.rpg.foundation.performance_trace import rpg_pipeline_span_if_active

logger = logging.getLogger(__name__)
_SESSION_DIR = rpg_sessions_root()
_LEGACY_SESSION_DIR = repo_root() / "data" / "rpg_sessions"
_SAVE_VERSION = "1.0"


def _migrate_legacy_sessions():
    """
    One-time migration from data/rpg_sessions → resources/data/rpg_sessions
    Safe, idempotent, deterministic.
    """
    try:
        if _LEGACY_SESSION_DIR.exists() and not _SESSION_DIR.exists():
            logger.warning(
                "[RPG][MIGRATION] Moving legacy sessions → resources/data/rpg_sessions"
            )

            _SESSION_DIR.parent.mkdir(parents=True, exist_ok=True)
            _LEGACY_SESSION_DIR.rename(_SESSION_DIR)

        elif _LEGACY_SESSION_DIR.exists() and _SESSION_DIR.exists():
            logger.warning(
                "[RPG][MIGRATION] Legacy + new both exist. Skipping auto-move."
            )
    except Exception as e:
        logger.error(f"[RPG][MIGRATION] Failed: {e}")


# Run on import (safe, deterministic)
_migrate_legacy_sessions()

# Ensure directory exists
_SESSION_DIR.mkdir(parents=True, exist_ok=True)


def ensure_session_dir(session_dir: Path | None = None) -> Path:
    """Create session directory if it doesn't exist."""
    directory = Path(session_dir) if session_dir is not None else _SESSION_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class SessionStoreError(RuntimeError):
    """Base durable-store error."""


class CorruptSessionPayloadError(SessionStoreError):
    """Raised when a persisted session payload is unreadable or invalid."""

    def __init__(self, session_id: str, path: Path, reason: str):
        self.session_id = str(session_id or "").strip()
        self.path = Path(path)
        self.reason = str(reason or "corrupt_session_payload").strip() or "corrupt_session_payload"
        super().__init__(self.reason)


def _session_path(session_id: str, *, session_dir: Path | None = None) -> Path:
    """Get the file path for a session."""
    safe_id = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in {"-", "_", ":"})
    safe_id = safe_id.replace(":", "_")  # Windows does not allow colons in filenames
    return ensure_session_dir(session_dir) / f"{safe_id}.json"


def _replace_with_retry(tmp_name: str, path: Path, *, attempts: int = 8, base_delay_s: float = 0.02) -> None:
    """Best-effort Windows-safe atomic replace with short retry/backoff."""
    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(tmp_name, path)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            time.sleep(base_delay_s * (attempt + 1))
        except OSError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            time.sleep(base_delay_s * (attempt + 1))

    # Last-resort fallback for Windows file-lock edge cases.
    try:
        text = Path(tmp_name).read_text(encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        return
    except Exception:
        logger.debug("suppressed error in %s", "_replace_with_retry", exc_info=True)
    raise cast(OSError, last_exc)


def _write_text_atomic(path: Path, text: str) -> None:
    """Write text atomically to avoid truncated/empty session files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_name, path)
    finally:
        if tmp_name:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass


def _read_text_with_retry(path: Path, *, attempts: int = 8, base_delay_s: float = 0.02) -> str:
    """Best-effort Windows-safe read with short retry/backoff.

    Idle ticks, stream turns, and panel refreshes can overlap on Windows. During
    an atomic replace or antivirus/indexer scan, Path.read_text may briefly
    raise PermissionError even though the session file is healthy. Treat that as
    transient before surfacing session_read_failed to the UI.
    """

    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            last_exc = exc
        except OSError as exc:
            last_exc = exc

        if attempt >= attempts - 1:
            break
        time.sleep(base_delay_s * (attempt + 1))

    if last_exc is not None:
        raise last_exc
    return path.read_text(encoding="utf-8")


def _quarantine_corrupt_session_file(path: Path) -> Path:
    """Move a corrupt session aside so repeated resume attempts do not crash forever."""
    quarantine_path = path.with_name(f"{path.stem}.corrupt.{int(utc_now().timestamp() * 1000)}{path.suffix}")
    try:
        os.replace(path, quarantine_path)
    except OSError:
        # Best-effort fallback if rename/replace is blocked on Windows.
        quarantine_path.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        try:
            path.unlink(missing_ok=True)
        except TypeError:
            if path.exists():
                path.unlink()
    return quarantine_path


def _read_payload_json(path: Path, session_id: str) -> dict[str, Any]:
    """Read raw JSON payload with corruption detection + quarantine."""
    try:
        text = _read_text_with_retry(path)
    except OSError as exc:
        raise SessionStoreError(f"session_read_failed:{session_id}") from exc

    if not text.strip():
        quarantined = _quarantine_corrupt_session_file(path)
        logger.error("Quarantined empty RPG session file", extra={"session_id": session_id, "path": str(quarantined)})
        raise CorruptSessionPayloadError(session_id, quarantined, "corrupt_session_payload")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        quarantined = _quarantine_corrupt_session_file(path)
        logger.exception("Quarantined invalid RPG session JSON", extra={"session_id": session_id, "path": str(quarantined)})
        raise CorruptSessionPayloadError(session_id, quarantined, "corrupt_session_payload")

    return _safe_dict(payload)


def save_session_to_disk(
    session: dict[str, Any],
    *,
    compact: bool = False,
    session_dir: Path | None = None,
) -> dict[str, Any]:
    manifest = _safe_dict(_safe_dict(session).get("manifest"))
    session_id = str(
        manifest.get("session_id")
        or manifest.get("id")
        or _safe_dict(session).get("session_id")
        or _safe_dict(session).get("id")
        or ""
    )
    with rpg_pipeline_span_if_active(
        "session.serialize_write",
        fields={"session_id": session_id, "compact": compact},
    ) as span:
        saved = _save_session_to_disk(session, compact=compact, session_dir=session_dir)
        if span is not None:
            saved_manifest = _safe_dict(saved.get("manifest"))
            runtime_state = _safe_dict(saved.get("runtime_state"))
            span["session_id"] = str(
                saved_manifest.get("session_id")
                or saved_manifest.get("id")
                or session_id
            )
            span["interaction_seq"] = runtime_state.get("interaction_seq")
            span["state_revision"] = runtime_state.get("state_revision")
        return saved


def _save_session_to_disk(
    session: dict[str, Any],
    *,
    compact: bool = False,
    session_dir: Path | None = None,
) -> dict[str, Any]:
    """Normalize, migrate, and persist session to disk-backed JSON."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime() and session_dir is None:
        from app.apps.rpg.session.persistence.session_records import save_session_to_postgres
        return save_session_to_postgres(session, compact=compact)
    session = _normalize_session(session)
    session = migrate_session_payload(session)
    manifest = _safe_dict(session.get("manifest"))
    payload = {
        "save_version": _SAVE_VERSION,
        "session": session,
    }
    path = _session_path(
        manifest.get("session_id") or manifest.get("id") or "session",
        session_dir=session_dir,
    )
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    _write_text_atomic(path, text)
    return session


def load_persisted_rng_seed(session_id: str, *, session_dir: Path | None = None) -> tuple[bool, Any]:
    """Whether the session is saved, and the ``rng_seed`` its saved simulation state holds."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime() and session_dir is None:
        from app.apps.rpg.session.persistence.session_records import load_rng_seed_from_postgres
        return load_rng_seed_from_postgres(session_id)
    previous = load_session_from_disk(session_id, session_dir=session_dir)
    if previous is None:
        return (False, None)
    return (True, _safe_dict(previous.get("simulation_state")).get("rng_seed"))


def load_session_from_disk(
    session_id: str,
    *,
    session_dir: Path | None = None,
) -> Optional[dict[str, Any]]:
    with rpg_pipeline_span_if_active(
        "session.file_read_decode_migrate",
        fields={"session_id": session_id},
    ) as span:
        session = _load_session_from_disk(session_id, session_dir=session_dir)
        if span is not None:
            runtime_state = _safe_dict(_safe_dict(session).get("runtime_state"))
            span["result_present"] = session is not None
            span["interaction_seq"] = runtime_state.get("interaction_seq")
            span["state_revision"] = runtime_state.get("state_revision")
        return session


def _load_session_from_disk(
    session_id: str,
    *,
    session_dir: Path | None = None,
) -> Optional[dict[str, Any]]:
    """Load and normalize a session from disk with migration. Returns None if not found."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime() and session_dir is None:
        from app.apps.rpg.session.persistence.session_records import load_session_from_postgres
        return load_session_from_postgres(session_id)
    path = _session_path(session_id, session_dir=session_dir)
    if not path.exists():
        return None
    raw_payload = _read_payload_json(path, session_id)
    migrated = migrate_session_payload(raw_payload)
    return _normalize_session(_safe_dict(migrated.get("session")))


def list_sessions_from_disk() -> list[dict[str, Any]]:
    """List all persisted sessions from disk with migration, normalized and sorted."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import list_sessions_from_postgres
        return list_sessions_from_postgres()
    ensure_session_dir()
    sessions: list[dict[str, Any]] = []
    for path in sorted(_SESSION_DIR.glob("*.json")):
        try:
            session_id = path.stem
            raw_payload = _read_payload_json(path, session_id)
            migrated = migrate_session_payload(raw_payload)
            session = _normalize_session(_safe_dict(migrated.get("session")))
            sessions.append(session)
        except CorruptSessionPayloadError:
            # Corrupt files are quarantined by _read_payload_json; skip them here.
            continue
        except Exception:
            logger.exception("Failed to list RPG session from disk", extra={"path": str(path)})
            continue
    return sessions


def archive_session_on_disk(session_id: str) -> dict[str, Any]:
    """Archive a session on disk by setting archived=True in manifest and persisting."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import archive_session_in_postgres
        return archive_session_in_postgres(session_id)
    session = load_session_from_disk(session_id)
    if session is None:
        return {"ok": False, "error": "session_not_found"}
    manifest = _safe_dict(session.get("manifest"))
    manifest["archived"] = True
    session["manifest"] = manifest
    save_session_to_disk(session)
    return {"ok": True, "session": session}
