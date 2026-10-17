from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .blob_store import LocalBlobStore


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Backups read every asset, one keyset page at a time (WP-5.5).
KEYSET_BATCH = 1000


def _keyset_rows(connection: Any, sql: str, parameters: tuple[Any, ...], *, key: str) -> list[Any]:
    """All rows of ``sql``, read in ``(workspace_id, key)`` order by keyset pages.

    ``sql`` has an ``{after}`` placeholder for the keyset condition, is ordered
    by ``workspace_id, key`` and selects those two columns last.
    """
    rows: list[Any] = []
    after: tuple[str, str] | None = None
    while True:
        condition = "" if after is None else f"AND (workspace_id, {key}) > (%s, %s)"
        batch = connection.execute(
            sql.replace("{after}", condition) + " LIMIT %s",
            parameters + (after or ()) + (KEYSET_BATCH,),
        ).fetchmany(KEYSET_BATCH)
        rows.extend(batch)
        if len(batch) < KEYSET_BATCH:
            return rows
        after = (str(batch[-1][-2]), str(batch[-1][-1]))


class CoordinatedRecoveryError(RuntimeError):
    pass


class CoordinatedRecoveryRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_generation(
        self,
        *,
        software_revision: str,
        schema_version: str,
        blob_root: str | Path,
        retention_days: int = 30,
        rpo_seconds: int = 86_400,
        rto_seconds: int = 3_600,
        encryption_required: bool = True,
        operator_note: str = "",
    ) -> str:
        if not str(software_revision).strip():
            raise CoordinatedRecoveryError("software revision is required")
        if not str(schema_version).strip():
            raise CoordinatedRecoveryError("schema version is required")
        if not str(operator_note).strip():
            raise CoordinatedRecoveryError("operator note is required")
        generation_id = f"backup:{uuid.uuid4().hex}"
        self.connection.execute(
            """
            INSERT INTO omnix_backup_generations (
                id, software_revision, schema_version, blob_root,
                retention_until, rpo_seconds, rto_seconds, encryption_required,
                metadata
            ) VALUES (
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 day'), %s, %s, %s,
                %s::jsonb
            )
            """,
            (
                generation_id,
                software_revision,
                schema_version,
                str(Path(blob_root)),
                max(1, int(retention_days)),
                max(0, int(rpo_seconds)),
                max(0, int(rto_seconds)),
                bool(encryption_required),
                _canonical({"operator_note": str(operator_note).strip()[:2000]}),
            ),
        )
        return generation_id

    def capture_manifest(self, generation_id: str, *, deletion_grace_days: int = 31) -> dict[str, Any]:
        status = self.connection.execute(
            "SELECT status FROM omnix_backup_generations WHERE id = %s FOR UPDATE",
            (generation_id,),
        ).fetchone()
        if status is None or str(status[0]) != "preparing":
            raise CoordinatedRecoveryError("backup generation is not preparing")
        rows = _keyset_rows(
            self.connection,
            """
            SELECT id, workspace_id, storage_provider, storage_key,
                   checksum_sha256, byte_size, lifecycle_status, workspace_id, id
              FROM omnix_assets
             WHERE lifecycle_status NOT IN ('deleted', 'purged') {after}
             ORDER BY workspace_id, id
            """,
            (),
            key="id",
        )
        manifest: list[dict[str, Any]] = [
            {
                "asset_id": str(row[0]),
                "workspace_id": str(row[1]),
                "storage_provider": str(row[2]),
                "storage_key": str(row[3]),
                "checksum_sha256": str(row[4]),
                "byte_size": int(row[5]),
                "lifecycle_status": str(row[6]),
            }
            for row in rows
        ]
        for item in manifest:
            self.connection.execute(
                """
                INSERT INTO omnix_backup_blob_manifest (
                    generation_id, asset_id, workspace_id, storage_provider,
                    storage_key, checksum_sha256, byte_size, lifecycle_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    generation_id,
                    item["asset_id"],
                    item["workspace_id"],
                    item["storage_provider"],
                    item["storage_key"],
                    item["checksum_sha256"],
                    item["byte_size"],
                    item["lifecycle_status"],
                ),
            )
        manifest_hash = hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()
        total_bytes = sum(item["byte_size"] for item in manifest)
        self.connection.execute(
            """
            UPDATE omnix_backup_generations
               SET status = 'manifested', manifest_hash = %s,
                   asset_count = %s, total_blob_bytes = %s,
                   manifested_at = CURRENT_TIMESTAMP
             WHERE id = %s
            """,
            (manifest_hash, len(manifest), total_bytes, generation_id),
        )
        self.connection.execute(
            """
            UPDATE omnix_assets
               SET deletion_not_before = GREATEST(
                   COALESCE(deletion_not_before, CURRENT_TIMESTAMP),
                   CURRENT_TIMESTAMP + (%s * INTERVAL '1 day')
               )
             WHERE id IN (
                 SELECT asset_id FROM omnix_backup_blob_manifest WHERE generation_id = %s
             )
            """,
            (max(1, int(deletion_grace_days)), generation_id),
        )
        return {
            "generation_id": generation_id,
            "manifest_hash": manifest_hash,
            "asset_count": len(manifest),
            "total_blob_bytes": total_bytes,
        }

    def record_database_backup(self, generation_id: str, reference: str) -> None:
        resolved_reference = str(reference).strip()
        if not resolved_reference:
            raise CoordinatedRecoveryError("PostgreSQL dump reference is required")
        parsed = urlsplit(resolved_reference)
        if (
            "://" in resolved_reference
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CoordinatedRecoveryError(
                "PostgreSQL dump reference must be a credential-free local path"
            )
        cursor = self.connection.execute(
            """
            UPDATE omnix_backup_generations
               SET status = 'database_backed_up', database_backup_reference = %s
             WHERE id = %s AND status = 'manifested'
            """,
            (resolved_reference, generation_id),
        )
        if cursor.rowcount != 1:
            raise CoordinatedRecoveryError("manifest must be captured before database backup")

    def copy_manifested_blobs(
        self,
        generation_id: str,
        *,
        source: LocalBlobStore,
        destination: LocalBlobStore,
    ) -> dict[str, Any]:
        generation = self.connection.execute(
            "SELECT status, blob_root FROM omnix_backup_generations WHERE id = %s FOR UPDATE",
            (generation_id,),
        ).fetchone()
        if generation is None or str(generation[0]) != "manifested":
            raise CoordinatedRecoveryError("manifest must be captured before copying blobs")
        expected_source = Path(str(generation[1])).resolve()
        if source.root != expected_source:
            raise CoordinatedRecoveryError(
                "source BlobStore root does not match the generation manifest"
            )
        if destination.root == source.root:
            raise CoordinatedRecoveryError("backup BlobStore root must differ from the live root")
        if any(destination.root.rglob("*")):
            raise CoordinatedRecoveryError("backup BlobStore root must be empty")
        rows = _keyset_rows(
            self.connection,
            """
            SELECT asset_id, storage_key, checksum_sha256, byte_size, workspace_id, asset_id
              FROM omnix_backup_blob_manifest
             WHERE generation_id = %s {after}
             ORDER BY workspace_id, asset_id
            """,
            (generation_id,),
            key="asset_id",
        )
        copied_bytes = 0
        for row in rows:
            asset_id, storage_key, checksum, byte_size = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3]),
            )
            # Stream each blob (bounded memory) instead of loading it whole.
            try:
                with source.open_verified(storage_key, expected_checksum=checksum) as reader:
                    copied = destination.put_stream(storage_key, reader)
            except Exception as exc:
                raise CoordinatedRecoveryError(
                    f"cannot copy manifested blob {asset_id}: {exc}"
                ) from exc
            if int(copied["byte_size"]) != byte_size:
                raise CoordinatedRecoveryError(
                    f"cannot copy manifested blob {asset_id}: expected {byte_size} bytes, "
                    f"got {copied['byte_size']}"
                )
            copied_bytes += byte_size
        self.connection.execute(
            """
            UPDATE omnix_backup_generations
               SET metadata = metadata || %s::jsonb
             WHERE id = %s
            """,
            (
                _canonical({"blob_backup_root": str(destination.root)}),
                generation_id,
            ),
        )
        return {
            "generation_id": generation_id,
            "blob_backup_root": str(destination.root),
            "asset_count": len(rows),
            "total_blob_bytes": copied_bytes,
        }

    def verify_blobs(
        self,
        generation_id: str,
        blob_store: LocalBlobStore,
        *,
        database_restore_verified: bool,
        migrations_verified: bool,
        smoke_checks_verified: bool,
    ) -> dict[str, Any]:
        generation = self.connection.execute(
            """
            SELECT status, manifest_hash, blob_root, asset_count, total_blob_bytes
              FROM omnix_backup_generations
             WHERE id = %s FOR UPDATE
            """,
            (generation_id,),
        ).fetchone()
        if generation is None or str(generation[0]) != "database_backed_up":
            raise CoordinatedRecoveryError("database backup must be recorded before verification")
        if not database_restore_verified:
            raise CoordinatedRecoveryError("disposable PostgreSQL restore must be verified")
        if not migrations_verified:
            raise CoordinatedRecoveryError("restored PostgreSQL migrations must be verified")
        if not smoke_checks_verified:
            raise CoordinatedRecoveryError("restored deterministic smoke checks must pass")
        if blob_store.root == Path(str(generation[2])).resolve():
            raise CoordinatedRecoveryError(
                "verification must use a restored BlobStore root, not the live root"
            )
        rows = _keyset_rows(
            self.connection,
            """
            SELECT asset_id, workspace_id, storage_provider, storage_key,
                   checksum_sha256, byte_size, lifecycle_status, workspace_id, asset_id
              FROM omnix_backup_blob_manifest
             WHERE generation_id = %s {after}
             ORDER BY workspace_id, asset_id
            """,
            (generation_id,),
            key="asset_id",
        )
        missing: list[str] = []
        mismatched: list[str] = []
        manifest: list[dict[str, Any]] = [
            {
                "asset_id": str(row[0]),
                "workspace_id": str(row[1]),
                "storage_provider": str(row[2]),
                "storage_key": str(row[3]),
                "checksum_sha256": str(row[4]),
                "byte_size": int(row[5]),
                "lifecycle_status": str(row[6]),
            }
            for row in rows
        ]
        manifest_hash = hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()
        expected_manifest_hash = str(generation[1])
        expected_count = int(generation[3])
        expected_bytes = int(generation[4])
        total_bytes = sum(item["byte_size"] for item in manifest)
        for row in rows:
            asset_id, storage_key, checksum, byte_size = (
                str(row[0]),
                str(row[3]),
                str(row[4]),
                int(row[5]),
            )
            error: str | None = None
            try:
                content = blob_store.read_bytes(storage_key, expected_checksum=checksum)
                if len(content) != byte_size:
                    error = f"size mismatch: expected {byte_size}, got {len(content)}"
                    mismatched.append(asset_id)
            except FileNotFoundError:
                error = "missing"
                missing.append(asset_id)
            except Exception as exc:
                error = str(exc)[:1000]
                mismatched.append(asset_id)
            self.connection.execute(
                """
                UPDATE omnix_backup_blob_manifest
                   SET verified = %s, verification_error = %s
                 WHERE generation_id = %s AND asset_id = %s
                """,
                (error is None, error, generation_id, asset_id),
            )
        expected_keys = {item["storage_key"] for item in manifest}
        restored_keys = {
            path.relative_to(blob_store.root).as_posix()
            for path in blob_store.root.rglob("*")
            if path.is_file()
        }
        unexpected = sorted(restored_keys - expected_keys)
        manifest_matches = (
            manifest_hash == expected_manifest_hash
            and len(manifest) == expected_count
            and total_bytes == expected_bytes
        )
        ok = not missing and not mismatched and manifest_matches
        verification = {
            "missing": missing,
            "mismatched": mismatched,
            "unexpected": unexpected,
            "manifest_hash": manifest_hash,
            "manifest_matches": manifest_matches,
            "asset_count": len(manifest),
            "total_blob_bytes": total_bytes,
            "restored_blob_root": str(blob_store.root),
            "database_restore_verified": True,
            "migrations_verified": True,
            "smoke_checks_verified": True,
        }
        self.connection.execute(
            """
            UPDATE omnix_backup_generations
               SET status = %s, verified_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                   failure = CASE WHEN %s THEN NULL ELSE %s::jsonb END,
                   metadata = metadata || %s::jsonb
             WHERE id = %s
            """,
            (
                "verified" if ok else "failed",
                ok,
                ok,
                _canonical(verification),
                _canonical({"restore_verification": verification}),
                generation_id,
            ),
        )
        return {"ok": ok, "checked": len(rows), **verification}

    def status(self, generation_id: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        bound = 1 if generation_id else max(1, min(int(limit), 1000))
        rows = self.connection.execute(
            """
            SELECT id, status, software_revision, schema_version, blob_root,
                   database_backup_reference, manifest_hash, asset_count,
                   total_blob_bytes, rpo_seconds, rto_seconds,
                   encryption_required, retention_until, created_at,
                   manifested_at, verified_at, failure, metadata
              FROM omnix_backup_generations
             WHERE (%s::text IS NULL OR id = %s)
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (generation_id, generation_id, bound),
        ).fetchall()
        return [
            {
                "generation_id": str(row[0]),
                "status": str(row[1]),
                "software_revision": str(row[2]),
                "schema_version": str(row[3]),
                "blob_root": str(row[4]),
                "database_backup_reference": str(row[5]) if row[5] is not None else None,
                "manifest_hash": str(row[6]) if row[6] is not None else None,
                "asset_count": int(row[7]),
                "total_blob_bytes": int(row[8]),
                "rpo_seconds": int(row[9]),
                "rto_seconds": int(row[10]),
                "encryption_required": bool(row[11]),
                "retention_until": row[12].isoformat() if row[12] is not None else None,
                "created_at": row[13].isoformat(),
                "manifested_at": row[14].isoformat() if row[14] is not None else None,
                "verified_at": row[15].isoformat() if row[15] is not None else None,
                "failure": dict(row[16]) if row[16] is not None else None,
                "metadata": dict(row[17] or {}),
            }
            for row in rows
        ]
