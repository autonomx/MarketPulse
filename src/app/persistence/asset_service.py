from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import BlobStore
from .database import PostgresDatabase
from .tenant import TenantContext
from .unit_of_work import unit_of_work


def create_asset(
    database: PostgresDatabase,
    blob_store: BlobStore,
    context: TenantContext,
    *,
    asset_id: str,
    module: str,
    asset_type: str,
    mime_type: str,
    storage_key: str,
    content: bytes,
    metadata: dict[str, Any] | None = None,
    compat: dict[str, Any] | None = None,
    generation_job_id: str | None = None,
) -> dict[str, Any]:
    blob = blob_store.put_bytes(storage_key, content)
    try:
        with unit_of_work(database) as work:
            asset = work.assets.create(
                context,
                {
                    "id": asset_id,
                    "module": module,
                    "asset_type": asset_type,
                    "mime_type": mime_type,
                    "byte_size": blob["byte_size"],
                    "checksum_sha256": blob["checksum_sha256"],
                    "storage_provider": blob["storage_provider"],
                    "storage_key": blob["storage_key"],
                    "metadata": metadata or {},
                    "compat": compat or {},
                    "generation_job_id": generation_job_id,
                },
            )
            work.audit.append(
                context,
                aggregate_type="asset",
                aggregate_id=asset_id,
                action="asset.created",
                payload={
                    "asset_type": asset_type,
                    "byte_size": blob["byte_size"],
                    "checksum_sha256": blob["checksum_sha256"],
                },
            )
            work.commit()
            return asset
    except Exception:
        if blob.get("created") is True:
            blob_store.delete(storage_key)
        raise


def open_asset_stream(
    database: PostgresDatabase,
    blob_store: BlobStore,
    context: TenantContext,
    asset_id: str,
) -> tuple[dict[str, Any], BinaryIO]:
    """Return the asset record and a checksum-verified, rewound content stream.

    Callers stream the content (bounded memory) and close the handle.
    """
    with unit_of_work(database) as work:
        asset = work.assets.get_asset(context, asset_id)
        if asset is None or asset["lifecycle_status"] == "deleted":
            raise KeyError(asset_id)
        work.rollback()
    handle = blob_store.open_verified(
        asset["storage_key"], expected_checksum=asset["checksum_sha256"]
    )
    return asset, handle


def delete_asset(
    database: PostgresDatabase,
    blob_store: BlobStore,
    context: TenantContext,
    *,
    asset_id: str,
    expected_revision: int,
    delete_blob: bool = True,
) -> dict[str, Any]:
    with unit_of_work(database) as work:
        existing = work.assets.get_asset(context, asset_id)
        if existing is None:
            raise KeyError(asset_id)
        deleted = work.assets.mark_deleted(
            context,
            asset_id=asset_id,
            expected_revision=expected_revision,
        )
        work.audit.append(
            context,
            aggregate_type="asset",
            aggregate_id=asset_id,
            action="asset.deleted",
            payload={"revision": deleted["revision"], "delete_blob": delete_blob},
        )
        work.commit()
    if delete_blob:
        blob_store.delete(existing["storage_key"])
    return deleted


def put_setting(
    database: PostgresDatabase,
    context: TenantContext,
    *,
    scope: str,
    key: str,
    value: Any,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    with unit_of_work(database) as work:
        setting = work.settings.put(
            context,
            scope=scope,
            key=key,
            value=value,
            expected_revision=expected_revision,
        )
        work.audit.append(
            context,
            aggregate_type="setting",
            aggregate_id=f"{scope}:{key}",
            action="setting.updated",
            payload={"revision": setting["revision"]},
        )
        work.commit()
        return setting


def register_secret_reference(
    database: PostgresDatabase,
    context: TenantContext,
    *,
    reference: str,
    provider: str,
    purpose: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with unit_of_work(database) as work:
        record = work.secret_references.register(
            context,
            reference=reference,
            provider=provider,
            purpose=purpose,
            metadata=metadata,
        )
        work.audit.append(
            context,
            aggregate_type="secret_reference",
            aggregate_id=reference,
            action="secret_reference.registered",
            payload={"provider": provider, "purpose": purpose},
        )
        work.commit()
        return record


def import_legacy_asset_manifest(
    database: PostgresDatabase,
    blob_store: BlobStore,
    context: TenantContext,
    manifest_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = Path(manifest_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assets = dict(raw.get("assets") or {})
    report: dict[str, Any] = {
        "source": str(path),
        "dry_run": dry_run,
        "discovered": len(assets),
        "imported": 0,
        "existing": 0,
        "missing": [],
        "errors": [],
    }
    for asset_id, payload_value in sorted(assets.items()):
        payload = dict(payload_value or {})
        source_path = Path(str(payload.get("storage_path") or ""))
        if not source_path.is_file():
            report["missing"].append({"asset_id": asset_id, "path": str(source_path)})
            continue
        with unit_of_work(database) as work:
            existing = work.assets.get_asset(context, str(asset_id))
            work.rollback()
        if existing is not None:
            report["existing"] += 1
            continue
        if dry_run:
            report["imported"] += 1
            continue
        try:
            content = source_path.read_bytes()
            safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(asset_id)).strip("-") or hashlib.sha256(
                str(asset_id).encode("utf-8")
            ).hexdigest()[:16]
            storage_key = f"legacy/{safe_id}/{source_path.name}"
            create_asset(
                database,
                blob_store,
                context,
                asset_id=str(asset_id),
                module=str(payload.get("module") or "legacy"),
                asset_type=str(payload.get("type") or payload.get("asset_type") or "other"),
                mime_type=str(payload.get("mime_type") or "application/octet-stream"),
                storage_key=storage_key,
                content=content,
                metadata=dict(payload.get("metadata") or {}),
                compat={
                    **dict(payload.get("compat") or {}),
                    "legacy_manifest": str(path),
                    "legacy_storage_path": str(source_path),
                },
            )
            report["imported"] += 1
        except Exception as exc:
            report["errors"].append(
                {
                    "asset_id": str(asset_id),
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                }
            )
    report["ok"] = not report["errors"]
    return report
