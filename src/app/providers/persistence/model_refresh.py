"""PostgreSQL-backed provider refresh persistence."""
from __future__ import annotations

from app.caching.bounded_cache import bounded_lru_cache

import uuid
from typing import Any

from app.providers.cache_status import (
    ProviderModelRefreshHistory,
    ProviderModelRefreshSnapshot,
    RefreshScope,
    _payload_to_dict,
    _safe_list,
    _safe_str,
    _utcnow,
)
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema
from app.runtime.pagination import page_limit


class PostgresProviderModelRefreshStore:
    def __init__(self, db_path: Any = None) -> None:
        if db_path is not None:
            raise RuntimeError("SQLite provider refresh authority is retired")
        self.documents = PostgresDocumentStore()

    def record_snapshot(
        self,
        *,
        scope: RefreshScope,
        reason: str | None,
        provider_payload: Any,
        cache_payload: Any,
    ) -> ProviderModelRefreshSnapshot:
        provider_data = _payload_to_dict(provider_payload)
        cache_data = _payload_to_dict(cache_payload)
        provider_count = len(_safe_list(provider_data.get("providers")))
        model_count = len(_safe_list(provider_data.get("models")))
        cache_status = _safe_str(cache_data.get("status")).strip() or "unknown"
        diagnostics = [
            item
            for item in _safe_list(cache_data.get("diagnostics"))
            if isinstance(item, dict)
        ]
        snapshot = ProviderModelRefreshSnapshot(
            id=f"provider-model-refresh:{uuid.uuid4().hex}",
            scope=scope,
            reason=reason,
            status="degraded" if diagnostics or cache_status == "degraded" else "ready",
            provider_count=provider_count,
            model_count=model_count,
            cache_status=cache_status,
            diagnostics=diagnostics,
            provider_payload=provider_data,
            cache_payload=cache_data,
            created_at=_utcnow(),
        )
        self.documents.write(
            snapshot.model_dump(mode="json"),
            module="providers",
            record_type="model-refresh-snapshot",
            record_id=snapshot.id,
        )
        return snapshot

    def latest_snapshot(self) -> ProviderModelRefreshSnapshot | None:
        values = self.list_snapshots(limit=1)
        return values[0] if values else None

    def list_snapshots(self, *, limit: int = 20) -> list[ProviderModelRefreshSnapshot]:
        values = [
            ProviderModelRefreshSnapshot.model_validate(payload)
            for _, payload, _ in self.documents.list(
                module="providers",
                record_type="model-refresh-snapshot",
                limit=page_limit(limit, default=20),
            )
            if isinstance(payload, dict)
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return values[: page_limit(limit, default=20)]

    def history(self, *, limit: int = 20) -> ProviderModelRefreshHistory:
        return ProviderModelRefreshHistory(snapshots=self.list_snapshots(limit=limit))


# Document shapes (WP-5.9).
register_document_schema("providers", "model-refresh-snapshot", ProviderModelRefreshSnapshot)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def production_provider_refresh_store() -> PostgresProviderModelRefreshStore:
    return PostgresProviderModelRefreshStore()
