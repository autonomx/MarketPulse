"""Combined Deep Research manifest helpers over the shared provenance store."""
from __future__ import annotations

import hashlib

from .contracts import ResearchSource, ResearchSourceSnapshot
from .source_store import ResearchSourceManifest, ResearchSourceStore


def create_deep_research_manifest(
    store: ResearchSourceStore,
    objective: str,
    sources: list[ResearchSource],
    snapshots: list[ResearchSourceSnapshot],
) -> ResearchSourceManifest:
    """Persist one manifest spanning every bounded research query."""

    now = store.clock()
    manifest = ResearchSourceManifest(
        manifest_id=f"manifest:{store.id_factory()}",
        mode="deep",
        query_id=f"research:{_digest(objective)[:20]}",
        query=objective,
        source_record_ids=[source.source_record_id for source in sources],
        snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots],
        created_at=now,
    )
    with store._lock:  # noqa: SLF001 - same provenance persistence boundary
        payload = store._load()  # noqa: SLF001
        for source in sources:
            payload["sources"][source.source_record_id] = source.model_dump(mode="json")
        for snapshot in snapshots:
            payload["snapshots"][snapshot.snapshot_id] = snapshot.model_dump(mode="json")
        payload["manifests"][manifest.manifest_id] = manifest.model_dump(mode="json")
        store._save(payload)  # noqa: SLF001
    return manifest


def update_snapshot_citation_label(
    store: ResearchSourceStore,
    snapshot: ResearchSourceSnapshot,
    citation_label: str,
) -> ResearchSourceSnapshot:
    """Bind a durable snapshot to its stable job-level display label."""

    updated = snapshot.model_copy(update={"citation_label": citation_label})
    with store._lock:  # noqa: SLF001 - same provenance persistence boundary
        payload = store._load()  # noqa: SLF001
        if snapshot.snapshot_id in payload["snapshots"]:
            payload["snapshots"][snapshot.snapshot_id] = updated.model_dump(mode="json")
            store._save(payload)  # noqa: SLF001
    return updated


def _digest(value: str) -> str:
    return hashlib.sha256(" ".join(value.lower().split()).encode("utf-8")).hexdigest()
