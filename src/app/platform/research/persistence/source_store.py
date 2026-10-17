"""PostgreSQL-backed research source persistence."""
from __future__ import annotations

from app.caching.bounded_cache import bounded_lru_cache

from pathlib import Path
from typing import Any, Callable

from app.platform.research.source_store import ResearchSourceStore
from app.persistence.document_store import PostgresDocumentStore
from pydantic import BaseModel, ConfigDict, Field

from app.platform.research.contracts import ResearchSource, ResearchSourceSnapshot
from app.platform.research.source_store import ResearchSourceManifest
from app.persistence.document_schemas import register_document_schema


class PostgresResearchSourceStore(ResearchSourceStore):
    """Keep citation/provenance logic while persisting metadata in PostgreSQL."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if path is not None:
            raise RuntimeError(
                "file-backed research source authority is retired; use the legacy importer"
            )
        from app.platform.research.source_store import _utcnow

        self.path = Path("postgresql://research-sources")
        self.clock = clock or _utcnow
        self.id_factory = id_factory or (lambda: __import__("uuid").uuid4().hex)
        self._documents = PostgresDocumentStore()
        # Serializes read-modify-write across processes (WP-5.9).
        self._lock = self._documents.lock(module="research", record_type="source-provenance-store")

    def _load(self) -> dict[str, dict]:
        payload = (
            self._lock.read(default=None)
            if self._lock.held
            else self._documents.read(module="research", record_type="source-provenance-store", default=None)
        )
        if not isinstance(payload, dict):
            return {"sources": {}, "snapshots": {}, "manifests": {}}
        return {
            "sources": dict(payload.get("sources") or {}),
            "snapshots": dict(payload.get("snapshots") or {}),
            "manifests": dict(payload.get("manifests") or {}),
        }

    def _save(self, payload: dict[str, dict]) -> None:
        if self._lock.held:
            self._lock.write(payload)
            return
        self._documents.write(payload, module="research", record_type="source-provenance-store")

    def save_extraction(self, snapshot_id: str, page: Any):
        """Store extracted content as an asset reference, not a mutable side file."""

        from app.assets.models import AssetRecord, AssetType
        from app.persistence.shared_asset_store import PostgresSharedAssetStoreAdapter
        from app.platform.research.contracts import ResearchSourceSnapshot

        with self._lock:
            payload = self._load()
            raw = payload["snapshots"].get(snapshot_id)
            if raw is None:
                raise KeyError(f"research snapshot not found: {snapshot_id}")
            source_path = self._temporary_extraction_file(snapshot_id, str(page.text))
            asset_id = f"asset:research-extract:{snapshot_id}"
            try:
                asset = PostgresSharedAssetStoreAdapter().upsert_asset(
                    AssetRecord(
                        id=asset_id,
                        module="research",
                        type=AssetType.TRANSCRIPT,
                        mime_type="text/plain",
                        storage_path=str(source_path),
                        metadata={
                            "snapshot_id": snapshot_id,
                            "extractor_version": page.extractor_version,
                            "content_hash": page.content_hash,
                        },
                    )
                )
            finally:
                source_path.unlink(missing_ok=True)
            snapshot = ResearchSourceSnapshot.model_validate(raw).model_copy(
                update={
                    "published_at": page.published_at or raw.get("published_at"),
                    "extractor_version": page.extractor_version,
                    "extraction_status": "completed",
                    "content_hash": page.content_hash,
                    "extracted_text_ref": asset.id,
                }
            )
            payload["snapshots"][snapshot_id] = snapshot.model_dump(mode="json")
            self._save(payload)
        return snapshot

    @staticmethod
    def _temporary_extraction_file(snapshot_id: str, content: str) -> Path:
        import tempfile

        safe = snapshot_id.replace(":", "_").replace("/", "_")
        root = Path(tempfile.mkdtemp(prefix="omnix-research-extract-"))
        path = root / f"{safe}.txt"
        path.write_text(content, encoding="utf-8")
        return path


class ResearchProvenanceDocument(BaseModel):
    """The ``research/source-provenance-store`` document."""

    model_config = ConfigDict(extra="allow")

    sources: dict[str, ResearchSource] = Field(default_factory=dict)
    snapshots: dict[str, ResearchSourceSnapshot] = Field(default_factory=dict)
    manifests: dict[str, ResearchSourceManifest] = Field(default_factory=dict)


# Document shapes (WP-5.9).
register_document_schema("research", "source-provenance-store", ResearchProvenanceDocument)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def production_research_source_store() -> PostgresResearchSourceStore:
    from app.persistence.repository_registry import register_feature_repositories

    register_feature_repositories("research")
    return PostgresResearchSourceStore()
