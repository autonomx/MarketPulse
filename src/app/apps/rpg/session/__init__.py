"""Phase 13.5 + 15.0 — Session lifecycle + persistence module."""
# Exports load on first use (PEP 562): importing a submodule of this
# package does not import every module re-exported here.
from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autoplay_certification_artifact import (
        append_saved_100_turn_certification_to_campaign_report_html,
        assert_phase7_real_autoplay_certification_artifact_ready,
        build_real_autoplay_certification_artifact,
        build_saved_100_turn_certification_payload,
        render_saved_100_turn_certification_report_html,
    )
    from .durable_store import (
        list_sessions_from_disk,
        load_session_from_disk,
        save_session_to_disk,
    )
    from .replay_checkpoint import (
        assert_phase7_replay_checkpoint_foundation_ready,
        build_replay_checkpoint_contract,
        build_session_checkpoint,
        canonical_session_json,
        compare_session_checkpoints,
        restore_session_from_checkpoint,
        session_checkpoint_digest,
    )
    from .replay_persistence_roundtrip_v2 import (
        assert_phase7_save_load_replay_roundtrip_ready,
        build_save_load_replay_roundtrip_contract,
        run_save_load_replay_persistence_roundtrip,
    )
    from .replay_turn_sequence import (
        assert_phase7_replay_turn_sequence_ready,
        build_replay_turn_sequence_contract,
        default_replay_command_handlers,
        run_replay_turn_sequence,
        validate_replay_turn_sequence,
    )
    from .saved_autoplay_digest_sources import (
        assert_phase7_saved_autoplay_digest_source_ready,
        build_saved_autoplay_digest_source_contract,
        capture_saved_autoplay_digest_sources,
    )
    from .session_store import (
        archive_session,
        ensure_session_registry,
        get_session,
        list_sessions,
        save_session,
    )
    from .turn_certification import (
        assert_phase7_full_100_turn_certification_ready,
        build_full_100_turn_certification_contract,
        build_full_100_turn_certification_result,
    )
    from .turn_readiness import (
        assert_phase7_100_turn_readiness_ready,
        build_100_turn_readiness_contract,
        build_100_turn_readiness_result,
    )
    from .turn_readiness_report import (
        append_100_turn_readiness_report_to_campaign_report_html,
        assert_phase7_100_turn_readiness_report_ready,
        build_100_turn_readiness_report_contract,
        build_100_turn_readiness_report_payload,
        render_100_turn_readiness_report_html,
    )

_EXPORTS = {
    "append_saved_100_turn_certification_to_campaign_report_html": (".autoplay_certification_artifact", "append_saved_100_turn_certification_to_campaign_report_html"),
    "assert_phase7_real_autoplay_certification_artifact_ready": (".autoplay_certification_artifact", "assert_phase7_real_autoplay_certification_artifact_ready"),
    "build_real_autoplay_certification_artifact": (".autoplay_certification_artifact", "build_real_autoplay_certification_artifact"),
    "build_saved_100_turn_certification_payload": (".autoplay_certification_artifact", "build_saved_100_turn_certification_payload"),
    "render_saved_100_turn_certification_report_html": (".autoplay_certification_artifact", "render_saved_100_turn_certification_report_html"),
    "list_sessions_from_disk": (".durable_store", "list_sessions_from_disk"),
    "load_session_from_disk": (".durable_store", "load_session_from_disk"),
    "save_session_to_disk": (".durable_store", "save_session_to_disk"),
    "assert_phase7_replay_checkpoint_foundation_ready": (".replay_checkpoint", "assert_phase7_replay_checkpoint_foundation_ready"),
    "build_replay_checkpoint_contract": (".replay_checkpoint", "build_replay_checkpoint_contract"),
    "build_session_checkpoint": (".replay_checkpoint", "build_session_checkpoint"),
    "canonical_session_json": (".replay_checkpoint", "canonical_session_json"),
    "compare_session_checkpoints": (".replay_checkpoint", "compare_session_checkpoints"),
    "restore_session_from_checkpoint": (".replay_checkpoint", "restore_session_from_checkpoint"),
    "session_checkpoint_digest": (".replay_checkpoint", "session_checkpoint_digest"),
    "assert_phase7_save_load_replay_roundtrip_ready": (".replay_persistence_roundtrip_v2", "assert_phase7_save_load_replay_roundtrip_ready"),
    "build_save_load_replay_roundtrip_contract": (".replay_persistence_roundtrip_v2", "build_save_load_replay_roundtrip_contract"),
    "run_save_load_replay_persistence_roundtrip": (".replay_persistence_roundtrip_v2", "run_save_load_replay_persistence_roundtrip"),
    "assert_phase7_replay_turn_sequence_ready": (".replay_turn_sequence", "assert_phase7_replay_turn_sequence_ready"),
    "build_replay_turn_sequence_contract": (".replay_turn_sequence", "build_replay_turn_sequence_contract"),
    "default_replay_command_handlers": (".replay_turn_sequence", "default_replay_command_handlers"),
    "run_replay_turn_sequence": (".replay_turn_sequence", "run_replay_turn_sequence"),
    "validate_replay_turn_sequence": (".replay_turn_sequence", "validate_replay_turn_sequence"),
    "assert_phase7_saved_autoplay_digest_source_ready": (".saved_autoplay_digest_sources", "assert_phase7_saved_autoplay_digest_source_ready"),
    "build_saved_autoplay_digest_source_contract": (".saved_autoplay_digest_sources", "build_saved_autoplay_digest_source_contract"),
    "capture_saved_autoplay_digest_sources": (".saved_autoplay_digest_sources", "capture_saved_autoplay_digest_sources"),
    "archive_session": (".session_store", "archive_session"),
    "ensure_session_registry": (".session_store", "ensure_session_registry"),
    "get_session": (".session_store", "get_session"),
    "list_sessions": (".session_store", "list_sessions"),
    "save_session": (".session_store", "save_session"),
    "assert_phase7_full_100_turn_certification_ready": (".turn_certification", "assert_phase7_full_100_turn_certification_ready"),
    "build_full_100_turn_certification_contract": (".turn_certification", "build_full_100_turn_certification_contract"),
    "build_full_100_turn_certification_result": (".turn_certification", "build_full_100_turn_certification_result"),
    "assert_phase7_100_turn_readiness_ready": (".turn_readiness", "assert_phase7_100_turn_readiness_ready"),
    "build_100_turn_readiness_contract": (".turn_readiness", "build_100_turn_readiness_contract"),
    "build_100_turn_readiness_result": (".turn_readiness", "build_100_turn_readiness_result"),
    "append_100_turn_readiness_report_to_campaign_report_html": (".turn_readiness_report", "append_100_turn_readiness_report_to_campaign_report_html"),
    "assert_phase7_100_turn_readiness_report_ready": (".turn_readiness_report", "assert_phase7_100_turn_readiness_report_ready"),
    "build_100_turn_readiness_report_contract": (".turn_readiness_report", "build_100_turn_readiness_report_contract"),
    "build_100_turn_readiness_report_payload": (".turn_readiness_report", "build_100_turn_readiness_report_payload"),
    "render_100_turn_readiness_report_html": (".turn_readiness_report", "render_100_turn_readiness_report_html"),
}

__all__ = ["append_saved_100_turn_certification_to_campaign_report_html", "assert_phase7_real_autoplay_certification_artifact_ready", "build_real_autoplay_certification_artifact", "build_saved_100_turn_certification_payload", "render_saved_100_turn_certification_report_html", "list_sessions_from_disk", "load_session_from_disk", "save_session_to_disk", "assert_phase7_replay_checkpoint_foundation_ready", "build_replay_checkpoint_contract", "build_session_checkpoint", "canonical_session_json", "compare_session_checkpoints", "restore_session_from_checkpoint", "session_checkpoint_digest", "assert_phase7_save_load_replay_roundtrip_ready", "build_save_load_replay_roundtrip_contract", "run_save_load_replay_persistence_roundtrip", "assert_phase7_replay_turn_sequence_ready", "build_replay_turn_sequence_contract", "default_replay_command_handlers", "run_replay_turn_sequence", "validate_replay_turn_sequence", "assert_phase7_saved_autoplay_digest_source_ready", "build_saved_autoplay_digest_source_contract", "capture_saved_autoplay_digest_sources", "archive_session", "ensure_session_registry", "get_session", "list_sessions", "save_session", "assert_phase7_full_100_turn_certification_ready", "build_full_100_turn_certification_contract", "build_full_100_turn_certification_result", "assert_phase7_100_turn_readiness_ready", "build_100_turn_readiness_contract", "build_100_turn_readiness_result", "append_100_turn_readiness_report_to_campaign_report_html", "assert_phase7_100_turn_readiness_report_ready", "build_100_turn_readiness_report_contract", "build_100_turn_readiness_report_payload", "render_100_turn_readiness_report_html"]


def __getattr__(name: str) -> Any:
    # The import system caches the module; nothing is cached here.
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(importlib.import_module(module, __name__), attribute)


def __dir__() -> list[str]:
    return sorted(set(vars(sys.modules[__name__])) | set(_EXPORTS))
