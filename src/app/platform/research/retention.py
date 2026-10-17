"""Retention cleanup for research snapshots, extracts, manifests, and caches."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .cache import ResearchCacheStore
from .contracts import ResearchSourceSnapshot
from .policy import ResearchPolicy, research_policy_from_env
from .source_store import ResearchSourceStore, default_research_source_store


class ResearchRetentionService:
    def __init__(
        self,
        *,
        source_store: ResearchSourceStore | None = None,
        cache_store: ResearchCacheStore | None = None,
        policy: ResearchPolicy | None = None,
    ) -> None:
        self.source_store = source_store if source_store is not None else default_research_source_store()
        self.cache_store = cache_store or ResearchCacheStore()
        self.policy = policy or research_policy_from_env()

    def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        cache_counts = self.cache_store.purge_expired()
        source_counts = self._cleanup_sources(current)
        return {**cache_counts, **source_counts}

    def _cleanup_sources(self, now: datetime) -> dict[str, int]:
        snapshot_cutoff = now.timestamp() - self.policy.raw_snapshot_retention_days * 86_400
        manifest_cutoff = now.timestamp() - self.policy.source_manifest_retention_days * 86_400
        removed_snapshots = 0
        removed_manifests = 0
        removed_extracts = 0
        redacted_snapshots = 0

        with self.source_store._lock:  # noqa: SLF001 - same retention persistence boundary
            payload = self.source_store._load()  # noqa: SLF001
            for snapshot_id, raw_snapshot in list(payload["snapshots"].items()):
                retrieved_at = _timestamp(raw_snapshot.get("retrieved_at"))
                raw_expired = retrieved_at is not None and retrieved_at < snapshot_cutoff
                if not raw_expired:
                    continue
                text_ref = str(raw_snapshot.get("extracted_text_ref") or "").strip()
                if text_ref and _safe_extract_path(self.source_store.path.parent, text_ref):
                    path = Path(text_ref)
                    if path.exists():
                        path.unlink(missing_ok=True)
                        removed_extracts += 1
                snapshot = ResearchSourceSnapshot.model_validate(raw_snapshot)
                if snapshot.extracted_text_ref or snapshot.content_hash:
                    payload["snapshots"][snapshot_id] = snapshot.model_copy(
                        update={
                            "extracted_text_ref": None,
                            "content_hash": None,
                            "extraction_status": "expired",
                        }
                    ).model_dump(mode="json")
                    redacted_snapshots += 1

            referenced_snapshots: set[str] = set()
            for manifest_id, raw_manifest in list(payload["manifests"].items()):
                created_at = _timestamp(raw_manifest.get("created_at"))
                if created_at is not None and created_at < manifest_cutoff:
                    payload["manifests"].pop(manifest_id, None)
                    removed_manifests += 1
                    continue
                referenced_snapshots.update(
                    str(value) for value in raw_manifest.get("snapshot_ids", []) if value
                )

            for snapshot_id, raw_snapshot in list(payload["snapshots"].items()):
                retrieved_at = _timestamp(raw_snapshot.get("retrieved_at"))
                expired = retrieved_at is not None and retrieved_at < manifest_cutoff
                if expired and snapshot_id not in referenced_snapshots:
                    payload["snapshots"].pop(snapshot_id, None)
                    removed_snapshots += 1

            referenced_sources = {
                str(raw_snapshot.get("source_record_id") or "")
                for raw_snapshot in payload["snapshots"].values()
            }
            payload["sources"] = {
                source_id: value
                for source_id, value in payload["sources"].items()
                if source_id in referenced_sources
            }
            self.source_store._save(payload)  # noqa: SLF001

        return {
            "research_snapshots": removed_snapshots,
            "research_snapshots_redacted": redacted_snapshots,
            "research_manifests": removed_manifests,
            "research_extract_files": removed_extracts,
        }


def _timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _safe_extract_path(root: Path, value: str) -> bool:
    try:
        candidate = Path(value).resolve()
        allowed = (root / "research_extracts").resolve()
        return candidate == allowed or allowed in candidate.parents
    except OSError:
        return False
