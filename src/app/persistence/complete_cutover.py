"""Complete legacy importer extensions for lifecycle and replay records.

Records in module-owned tables are restored by the step each module declares
(``LEGACY_IMPORTS`` in its ``declarations.py``, PA-2.2).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .cutover import PostgresLegacyImporter
from .declarations import legacy_import
from .tenant import TenantContext


class CompletePostgresLegacyImporter(PostgresLegacyImporter):
    """Restore lifecycle records nested in their owning legacy aggregates."""

    def _dispatch(
        self,
        work: Any,
        context: TenantContext,
        entity_type: str,
        stable_id: str,
        item: dict[str, Any],
    ) -> tuple[str, str, str | None]:
        if entity_type == "characters" and item.get("_migration_envelope"):
            legacy_import("chat.conversation_segments")(work, context, stable_id, item)
            return "omnix_conversation_segments", stable_id, None
        if entity_type == "memory_records" and item.get("_migration_envelope"):
            legacy_import("assistant-memory.lifecycle")(work, context, stable_id, item)
            return "omnix_memory_events", stable_id, None

        target = super()._dispatch(work, context, entity_type, stable_id, item)
        if entity_type == "characters":
            legacy_import("chat.conversation_segments")(work, context, stable_id, item)
        elif entity_type == "jobs":
            self._job_history(work, context, stable_id, item)
        elif entity_type == "rpg_campaigns":
            legacy_import("rpg.history")(work, context, stable_id, item)
        return target

    @staticmethod
    def _job_history(
        work: Any,
        context: TenantContext,
        job_id: str,
        item: dict[str, Any],
    ) -> None:
        work.jobs.import_job_history(context, job_id=job_id, item=item)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()



