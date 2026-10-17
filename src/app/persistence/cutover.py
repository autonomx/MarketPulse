from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .authority import AuthorityOperation
from .blob_store import default_blob_store
from .contracts import BlobStore
from .declarations import legacy_import
from .database import PostgresDatabase
from .errors import PersistenceError
from .tenant import TenantContext
from .unit_of_work import unit_of_work


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


LEGACY_BUNDLE_FORMAT = "omnix_legacy_bundle_v1"
_IMPORT_ORDER = (
    "assets",
    "characters",
    "memory_records",
    "chat_sessions",
    "jobs",
    "rpg_campaigns",
    "settings",
    "providers",
    "prompts",
    "research_records",
    "reports",
    "module_records",
)
_SECRET_KEYS = {
    "api_key",
    "password",
    "access_token",
    "refresh_token",
    "secret_value",
    "credential_value",
}


class LegacyBundleError(ValueError):
    pass


class LegacySourceChanged(PersistenceError):
    pass


class CutoverNotReady(PersistenceError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def bundle_hash(bundle: dict[str, Any]) -> str:
    normalized = dict(bundle)
    normalized.pop("source_hash", None)
    return _sha256(normalized)


def _entity_id(entity_type: str, item: dict[str, Any]) -> str:
    if entity_type == "module_records":
        module = str(item.get("module") or "").strip()
        record_type = str(item.get("record_type") or "").strip()
        record_id = str(item.get("record_id") or item.get("id") or "").strip()
        if module and record_type and record_id:
            return f"{module}:{record_type}:{record_id}"
    for key in ("id", "record_id", "key"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    if entity_type == "settings":
        scope = str(item.get("scope") or "").strip()
        key = str(item.get("key") or "").strip()
        if scope and key:
            return f"{scope}:{key}"
    raise LegacyBundleError(f"{entity_type} item is missing a stable id")


def _scan_secrets(value: Any, path: str = "bundle") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            child_path = f"{path}.{key}"
            if key_text in _SECRET_KEYS and child not in (None, "", False):
                findings.append(child_path)
            findings.extend(_scan_secrets(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_scan_secrets(child, f"{path}[{index}]"))
    return findings


def preflight_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if bundle.get("format_version") != LEGACY_BUNDLE_FORMAT:
        errors.append(f"format_version must be {LEGACY_BUNDLE_FORMAT}")
    source_id = str(bundle.get("source_id") or "").strip()
    if not source_id:
        errors.append("source_id is required")
    entities = bundle.get("entities")
    if not isinstance(entities, dict):
        errors.append("entities must be an object")
        entities = {}

    counts: dict[str, int] = {}
    item_hashes: dict[str, dict[str, str]] = {}
    for entity_type, raw_items in sorted(entities.items()):
        if entity_type not in _IMPORT_ORDER:
            errors.append(f"unsupported entity type: {entity_type}")
            continue
        if not isinstance(raw_items, list):
            errors.append(f"entities.{entity_type} must be a list")
            continue
        counts[entity_type] = len(raw_items)
        seen: set[str] = set()
        hashes: dict[str, str] = {}
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                errors.append(f"entities.{entity_type}[{index}] must be an object")
                continue
            try:
                stable_id = _entity_id(entity_type, raw_item)
            except LegacyBundleError as exc:
                errors.append(str(exc))
                continue
            if stable_id in seen:
                errors.append(f"duplicate {entity_type} id: {stable_id}")
            seen.add(stable_id)
            hashes[stable_id] = _sha256(raw_item)
            if entity_type == "assets":
                source_path = Path(str(raw_item.get("source_path") or ""))
                if not source_path.is_file():
                    errors.append(
                        f"asset {stable_id} source file is missing: {source_path}"
                    )
        item_hashes[entity_type] = hashes

    for source in bundle.get("source_inventory") or []:
        if not isinstance(source, dict):
            continue
        if str(source.get("path") or "").strip() and not source.get("exists"):
            errors.append(f"legacy source is missing: {source.get('name')}")

    secret_paths = _scan_secrets(entities)
    if secret_paths:
        errors.append(
            "bundle contains secret-bearing values; migrate references only: "
            + ", ".join(secret_paths[:20])
        )

    calculated_hash = bundle_hash(bundle)
    declared_hash = str(bundle.get("source_hash") or "").strip()
    if declared_hash and declared_hash != calculated_hash:
        errors.append("declared source_hash does not match canonical bundle hash")

    return {
        "ok": not errors,
        "format_version": bundle.get("format_version"),
        "source_id": source_id,
        "source_hash": calculated_hash,
        "counts": counts,
        "item_hashes": item_hashes,
        "errors": errors,
    }


class PostgresLegacyImporter:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        blob_store: BlobStore | None = None,
    ) -> None:
        self.database = database
        self.blob_store = blob_store or default_blob_store()

    def import_bundle(
        self,
        context: TenantContext,
        bundle: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        preflight = preflight_bundle(bundle)
        if not preflight["ok"]:
            raise LegacyBundleError("; ".join(preflight["errors"]))
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "already_completed": False,
                "preflight": preflight,
            }

        existing = self._existing_run(preflight["source_id"])
        if existing is not None:
            if existing["source_hash"] != preflight["source_hash"]:
                raise LegacySourceChanged(
                    f"source {preflight['source_id']} was already imported with a different hash"
                )
            if existing["status"] == "completed":
                return {
                    "ok": True,
                    "dry_run": False,
                    "already_completed": True,
                    "run": existing,
                    "verification": self.verify_run(existing["id"]),
                }
            run_id = existing["id"]
        else:
            run_id = f"legacy-import:{uuid.uuid4().hex}"
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO omnix_legacy_import_runs (
                        id, source_id, source_hash, format_version,
                        discovered_counts, status
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, 'running')
                    """,
                    (
                        run_id,
                        preflight["source_id"],
                        preflight["source_hash"],
                        LEGACY_BUNDLE_FORMAT,
                        _canonical(preflight["counts"]),
                    ),
                )

        errors: list[dict[str, str]] = []
        entities = dict(bundle.get("entities") or {})
        for entity_type in _IMPORT_ORDER:
            for item in list(entities.get(entity_type) or []):
                stable_id = _entity_id(entity_type, item)
                item_hash = preflight["item_hashes"][entity_type][stable_id]
                if self._item_completed(run_id, entity_type, stable_id, item_hash):
                    continue
                try:
                    self._import_item(
                        context,
                        run_id=run_id,
                        entity_type=entity_type,
                        stable_id=stable_id,
                        item_hash=item_hash,
                        item=item,
                    )
                except Exception as exc:
                    self._record_failed_item(
                        run_id,
                        entity_type,
                        stable_id,
                        item_hash,
                        f"{exc.__class__.__name__}: {exc}",
                    )
                    errors.append(
                        {
                            "entity_type": entity_type,
                            "source_id": stable_id,
                            "error": exc.__class__.__name__,
                            "message": str(exc),
                        }
                    )

        verification = self.verify_run(run_id)
        completed = not errors and verification["ok"]
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE omnix_legacy_import_runs
                   SET status = %s,
                       imported_counts = %s::jsonb,
                       verification = %s::jsonb,
                       error_summary = %s::jsonb,
                       completed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s
                """,
                (
                    "completed" if completed else "failed",
                    _canonical(verification["imported_counts"]),
                    _canonical(verification),
                    _canonical(errors),
                    completed,
                    run_id,
                ),
            )
        result = self._run_by_id(run_id)
        return {
            "ok": completed,
            "dry_run": False,
            "already_completed": False,
            "run": result,
            "verification": verification,
            "errors": errors,
        }

    def verify_run(self, run_id: str) -> dict[str, Any]:
        run = self._run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT entity_type, status, COUNT(*)
                  FROM omnix_legacy_import_items
                 WHERE import_run_id = %s
                 GROUP BY entity_type, status
                 LIMIT 1000
                """,
                (run_id,),
            ).fetchall()
        imported_counts: Counter[str] = Counter()
        failed_counts: Counter[str] = Counter()
        for entity_type, status, count in rows:
            if status == "imported":
                imported_counts[str(entity_type)] += int(count)
            elif status == "failed":
                failed_counts[str(entity_type)] += int(count)
        expected = {str(k): int(v) for k, v in run["discovered_counts"].items()}
        imported = {key: int(imported_counts.get(key, 0)) for key in expected}
        failed = {key: int(failed_counts.get(key, 0)) for key in expected if failed_counts.get(key)}
        mismatches = {
            key: {"expected": count, "imported": imported.get(key, 0)}
            for key, count in expected.items()
            if imported.get(key, 0) != count
        }
        return {
            "ok": not mismatches and not failed,
            "run_id": run_id,
            "source_id": run["source_id"],
            "source_hash": run["source_hash"],
            "expected_counts": expected,
            "imported_counts": imported,
            "failed_counts": failed,
            "mismatches": mismatches,
        }

    def activate_cutover(
        self,
        *,
        run_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del run_id, metadata
        raise CutoverNotReady(
            "one-step activation is retired; use python -m app.persistence cutover "
            "mark-imported-unverified, mark-imported-verified, activate-frozen, and open-writes"
        )

    def record_rollback(
        self,
        *,
        run_id: str,
        reason: str,
    ) -> dict[str, Any]:
        del run_id, reason
        raise CutoverNotReady(
            "legacy rollback recording is retired; use python -m app.persistence "
            "cutover record-rollback with the required acknowledgements"
        )

    def cutover_status(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT mode, import_run_id, source_hash, activated_at,
                       rollback_recorded_at, updated_at, metadata
                  FROM omnix_persistence_cutover WHERE singleton = TRUE
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("persistence cutover singleton is missing")
        return self._cutover_record(row)

    def _import_item(
        self,
        context: TenantContext,
        *,
        run_id: str,
        entity_type: str,
        stable_id: str,
        item_hash: str,
        item: dict[str, Any],
    ) -> None:
        cleanup_key: str | None = None
        try:
            with unit_of_work(
                self.database,
                authority_operation=AuthorityOperation.LEGACY_IMPORT,
            ) as work:
                assert work.connection is not None
                target_table, target_id, cleanup_key = self._dispatch(
                    work, context, entity_type, stable_id, item
                )
                work.connection.execute(
                    """
                    INSERT INTO omnix_legacy_import_items (
                        import_run_id, entity_type, source_id, source_hash,
                        target_table, target_id, status, imported_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'imported', CURRENT_TIMESTAMP)
                    ON CONFLICT (import_run_id, entity_type, source_id) DO UPDATE SET
                        source_hash = EXCLUDED.source_hash,
                        target_table = EXCLUDED.target_table,
                        target_id = EXCLUDED.target_id,
                        status = 'imported', error = NULL,
                        imported_at = CURRENT_TIMESTAMP
                    """,
                    (
                        run_id,
                        entity_type,
                        stable_id,
                        item_hash,
                        target_table,
                        target_id,
                    ),
                )
                work.commit()
        except Exception:
            if cleanup_key is not None:
                self.blob_store.delete(cleanup_key)
            raise

    def _dispatch(
        self,
        work: Any,
        context: TenantContext,
        entity_type: str,
        stable_id: str,
        item: dict[str, Any],
    ) -> tuple[str, str, str | None]:
        if entity_type == "assets":
            source_path = Path(str(item["source_path"]))
            content = source_path.read_bytes()
            storage_key = str(
                item.get("storage_key")
                or f"legacy/{hashlib.sha256(stable_id.encode('utf-8')).hexdigest()}/{source_path.name}"
            )
            blob = self.blob_store.put_bytes(storage_key, content)
            work.assets.create(
                context,
                {
                    "id": stable_id,
                    "module": item.get("module", "legacy"),
                    "asset_type": item.get("asset_type", "other"),
                    "mime_type": item.get("mime_type", "application/octet-stream"),
                    "byte_size": blob["byte_size"],
                    "checksum_sha256": blob["checksum_sha256"],
                    "storage_provider": blob["storage_provider"],
                    "storage_key": blob["storage_key"],
                    "metadata": item.get("metadata") or {},
                    "compat": {
                        **dict(item.get("compat") or {}),
                        "legacy_source_path": str(source_path),
                    },
                },
            )
            return "omnix_assets", stable_id, storage_key if blob["created"] else None
        if entity_type == "characters":
            versions = sorted(
                list(item.get("versions") or []), key=lambda value: int(value.get("version", 0))
            )
            first_profile = (
                dict(versions[0]["profile"])
                if versions
                else dict(item.get("profile") or {})
            )
            created = work.characters.create(
                context,
                character_id=stable_id,
                profile=first_profile,
                visibility=item.get("visibility", "private"),
                enabled=bool(item.get("enabled", True)),
            )
            current_version = created["active_version"]
            for version in versions[1:]:
                created = work.characters.update(
                    context,
                    character_id=stable_id,
                    profile=dict(version["profile"]),
                    expected_version=current_version,
                )
                current_version = created["active_version"]
            final_profile = dict(item.get("profile") or {})
            if final_profile and final_profile != created["profile"]:
                created = work.characters.update(
                    context,
                    character_id=stable_id,
                    profile=final_profile,
                    expected_version=current_version,
                )
            return "omnix_characters", stable_id, None
        if entity_type == "memory_records":
            # Curated memory lives in Memory v2 (WP-8.5); the record is not part of this transaction.
            legacy_import("assistant-memory.record")(work, context, stable_id, item)
            return "omnix_memory_v2_observations", stable_id, None
        if entity_type == "chat_sessions":
            messages = list(item.get("messages") or [])
            payload = dict(item)
            payload["id"] = stable_id
            payload.pop("messages", None)
            work.chats.create_session(context, payload)
            for message in messages:
                work.chats.append_message(context, stable_id, dict(message))
            return "omnix_chat_sessions", stable_id, None
        if entity_type == "jobs":
            work.jobs.import_job(context, job_id=stable_id, item=item)
            return "omnix_jobs", stable_id, None
        if entity_type == "rpg_campaigns":
            legacy_import("rpg.campaign")(work, context, stable_id, item)
            return "omnix_rpg_campaigns", stable_id, None
        if entity_type == "settings":
            work.settings.put(
                context,
                scope=str(item["scope"]),
                key=str(item["key"]),
                value=item.get("value"),
            )
            return "omnix_settings", stable_id, None
        if entity_type == "providers":
            secret_reference = item.get("secret_reference")
            if secret_reference:
                work.secret_references.register(
                    context,
                    reference=str(secret_reference),
                    provider=stable_id,
                    purpose="provider-api-key",
                    metadata={"legacy_import": True},
                )
            work.providers.create(
                context,
                provider_id=stable_id,
                provider_type=item.get("provider_type", "legacy"),
                display_name=item.get("display_name", stable_id),
                config=dict(item.get("config") or {}),
                secret_reference=secret_reference,
                enabled=bool(item.get("enabled", True)),
            )
            return "omnix_provider_configs", stable_id, None
        if entity_type == "prompts":
            work.prompts.create(
                context,
                prompt_id=stable_id,
                name=item.get("name", stable_id),
                template_type=item.get("template_type", "system"),
                content=item.get("content", ""),
                variables=list(item.get("variables") or []),
            )
            return "omnix_prompt_templates", stable_id, None
        if entity_type == "research_records":
            work.research_reports.put_research(
                context,
                record_id=stable_id,
                research_type=item.get("research_type", "legacy"),
                query_text=item.get("query_text"),
                result=dict(item.get("result") or {}),
                source_fingerprint=item.get("source_fingerprint"),
                expires_at=item.get("expires_at"),
            )
            return "omnix_research_records", stable_id, None
        if entity_type == "reports":
            work.research_reports.create_report(
                context,
                report_id=stable_id,
                report_type=item.get("report_type", "legacy"),
                title=item.get("title", stable_id),
                summary=dict(item.get("summary") or {}),
                blob_asset_id=item.get("blob_asset_id"),
                generated_by_job_id=item.get("generated_by_job_id"),
            )
            return "omnix_reports", stable_id, None
        if entity_type == "module_records":
            record_id = str(item.get("record_id") or item.get("id") or stable_id)
            work.module_records.put(
                context,
                module=item["module"],
                record_type=item["record_type"],
                record_id=record_id,
                payload=dict(item.get("payload") or {}),
                status=item.get("status", "active"),
                expires_at=item.get("expires_at"),
            )
            return "omnix_module_records", stable_id, None
        raise LegacyBundleError(f"unsupported entity type: {entity_type}")

    def _existing_run(self, source_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, source_id, source_hash, format_version, status,
                       discovered_counts, imported_counts, verification,
                       error_summary, started_at, completed_at, updated_at
                  FROM omnix_legacy_import_runs WHERE source_id = %s
                """,
                (source_id,),
            ).fetchone()
        return self._run_record(row) if row is not None else None

    def _run_by_id(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, source_id, source_hash, format_version, status,
                       discovered_counts, imported_counts, verification,
                       error_summary, started_at, completed_at, updated_at
                  FROM omnix_legacy_import_runs WHERE id = %s
                """,
                (run_id,),
            ).fetchone()
        return self._run_record(row) if row is not None else None

    @staticmethod
    def _run_record(row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "source_id": str(row[1]),
            "source_hash": str(row[2]),
            "format_version": str(row[3]),
            "status": str(row[4]),
            "discovered_counts": dict(row[5]),
            "imported_counts": dict(row[6]),
            "verification": dict(row[7]),
            "errors": list(row[8]),
            "started_at": row[9].isoformat(),
            "completed_at": row[10].isoformat() if row[10] is not None else None,
            "updated_at": row[11].isoformat(),
        }

    def _item_completed(
        self, run_id: str, entity_type: str, stable_id: str, item_hash: str
    ) -> bool:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT source_hash, status FROM omnix_legacy_import_items
                 WHERE import_run_id = %s AND entity_type = %s AND source_id = %s
                """,
                (run_id, entity_type, stable_id),
            ).fetchone()
        if row is None:
            return False
        if str(row[0]) != item_hash:
            raise LegacySourceChanged(
                f"legacy item changed during resumable import: {entity_type}/{stable_id}"
            )
        return str(row[1]) == "imported"

    def _record_failed_item(
        self,
        run_id: str,
        entity_type: str,
        stable_id: str,
        item_hash: str,
        error: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO omnix_legacy_import_items (
                    import_run_id, entity_type, source_id, source_hash,
                    status, error
                ) VALUES (%s, %s, %s, %s, 'failed', %s)
                ON CONFLICT (import_run_id, entity_type, source_id) DO UPDATE SET
                    source_hash = EXCLUDED.source_hash,
                    status = 'failed', error = EXCLUDED.error,
                    imported_at = NULL
                """,
                (run_id, entity_type, stable_id, item_hash, error[:4000]),
            )

    @staticmethod
    def _cutover_record(row: Any) -> dict[str, Any]:
        return {
            "mode": str(row[0]),
            "import_run_id": str(row[1]) if row[1] is not None else None,
            "source_hash": str(row[2]) if row[2] is not None else None,
            "activated_at": row[3].isoformat() if row[3] is not None else None,
            "rollback_recorded_at": row[4].isoformat() if row[4] is not None else None,
            "updated_at": row[5].isoformat(),
            "metadata": dict(row[6]),
        }
