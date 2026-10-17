"""The asset store every module types against (kernel).

Two implementations satisfy it structurally: the PostgreSQL adapter the
runtime uses (``app.persistence.shared_asset_store``) and the JSON manifest
store kept for legacy tests and imports (``app.assets.store``). Signatures
name this protocol, never one implementation, so either can be passed.
"""
from __future__ import annotations

from typing import Any, Protocol

from .models import AssetListResponse, AssetRecord


class AssetStore(Protocol):
    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        modules: tuple[str, ...] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AssetListResponse: ...

    def get_asset(self, asset_id: str) -> AssetRecord | None: ...

    def upsert_asset(self, asset: AssetRecord) -> AssetRecord: ...

    def delete_asset(self, asset_id: str, *, delete_file: bool = True) -> dict[str, Any]: ...

    def read_asset_bytes(self, asset_id: str, *, max_bytes: int) -> bytes: ...


__all__ = ["AssetStore"]
