"""Immutable render cache lookup with metadata and blob attestation."""
from __future__ import annotations

from typing import Any

from app.persistence.blob_store import BlobIntegrityError
from app.persistence.contracts import BlobStore
from app.persistence.asset_repository import PostgresAssetRepository
from app.persistence.tenant import TenantContext


def valid_render_blob(render: dict[str, Any], asset: dict[str, Any], blobs: BlobStore) -> bool:
    if render["audio_asset_id"] != asset["id"]:
        return False
    if asset["module"] != "audiobook" or asset["lifecycle_status"] != "active":
        return False
    if asset["checksum_sha256"] != render["audio_checksum"]:
        return False
    if asset["storage_provider"] != blobs.provider:
        return False
    try:
        with blobs.open_verified(
            asset["storage_key"], expected_checksum=render["audio_checksum"],
        ) as handle:
            return bool(handle.read(1))
    except (FileNotFoundError, BlobIntegrityError, OSError):
        return False


def find_valid_render(
    connection: Any, context: TenantContext, blobs: BlobStore, render_key: str,
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT id, audio_asset_id, audio_checksum, duration_seconds, sample_rate
          FROM omnix_audiobook_renders
         WHERE workspace_id = %s AND render_key = %s AND status = 'completed'
         ORDER BY created_at DESC, id DESC
        """, (context.workspace_id, render_key),
    ).fetchall()
    assets = PostgresAssetRepository(connection)
    for row in rows:
        stored = assets.asset_fields(context, str(row[1]))
        if stored is None:
            continue
        render = {"id": str(row[0]), "audio_asset_id": str(row[1]),
                  "audio_checksum": str(row[2]), "duration_seconds": float(row[3]),
                  "sample_rate": int(row[4]), "render_key": render_key}
        asset = {key: str(stored[key]) for key in
                 ("id", "module", "lifecycle_status", "checksum_sha256", "storage_provider", "storage_key")}
        if valid_render_blob(render, asset, blobs):
            return render
    return None
