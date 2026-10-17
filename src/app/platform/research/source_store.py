"""Durable source, retrieval snapshot, and citation-manifest storage."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from app.runtime.paths import resources_data_root

from .contracts import ResearchSource, ResearchSourceSnapshot
from .extraction import ExtractedPage

_TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchSourceManifest(BaseModel):
    manifest_id: str
    mode: Literal["quick", "deep"]
    query_id: str
    query: str
    source_record_ids: list[str] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    created_at: str


class RecordedResearchSources(BaseModel):
    manifest: ResearchSourceManifest
    sources: list[ResearchSource] = Field(default_factory=list)
    snapshots: list[ResearchSourceSnapshot] = Field(default_factory=list)
    items: list[Any] = Field(default_factory=list)


def canonicalize_source_url(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        return None
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
    ]
    query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


class ResearchSourceStore:
    """JSON-backed provenance store shared by Quick and Deep Research."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], str] = _utcnow,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else default_research_source_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._lock = threading.Lock()

    def record_quick_search(
        self,
        query: str,
        provider: str,
        items: list[Any],
    ) -> RecordedResearchSources:
        now = self.clock()
        query_id = f"query:{_digest(' '.join(query.split()).lower())[:20]}"
        manifest_id = f"manifest:{self.id_factory()}"
        sources: list[ResearchSource] = []
        snapshots: list[ResearchSourceSnapshot] = []
        recorded_items: list[Any] = []
        seen_sources: set[str] = set()

        with self._lock:
            payload = self._load()
            for item in items:
                canonical_url = canonicalize_source_url(item.url)
                source_id = stable_source_record_id(provider, item, canonical_url=canonical_url)
                if source_id in seen_sources:
                    continue
                seen_sources.add(source_id)
                stored_source = payload["sources"].get(source_id)
                source = ResearchSource.model_validate(stored_source) if stored_source else ResearchSource(
                    source_record_id=source_id,
                    provider=provider or str(item.metadata.get("provider") or "unknown"),
                    original_url=item.url,
                    canonical_url=canonical_url,
                    title=item.title,
                    first_seen_at=now,
                )
                citation_label = f"S{len(snapshots) + 1}"
                snapshot = ResearchSourceSnapshot(
                    snapshot_id=f"snapshot:{self.id_factory()}",
                    source_record_id=source_id,
                    citation_label=citation_label,
                    query_id=query_id,
                    rank=len(snapshots) + 1,
                    snippet=item.content,
                    published_at=_optional_text(item.metadata.get("published_at")),
                    retrieved_at=now,
                )
                item.metadata = {
                    **item.metadata,
                    "source_record_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "citation_label": citation_label,
                    "source_manifest_id": manifest_id,
                    "canonical_url": canonical_url,
                }
                payload["sources"][source_id] = source.model_dump(mode="json")
                payload["snapshots"][snapshot.snapshot_id] = snapshot.model_dump(mode="json")
                sources.append(source)
                snapshots.append(snapshot)
                recorded_items.append(item)

            manifest = ResearchSourceManifest(
                manifest_id=manifest_id,
                mode="quick",
                query_id=query_id,
                query=query,
                source_record_ids=[source.source_record_id for source in sources],
                snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots],
                created_at=now,
            )
            payload["manifests"][manifest_id] = manifest.model_dump(mode="json")
            self._save(payload)

        return RecordedResearchSources(
            manifest=manifest,
            sources=sources,
            snapshots=snapshots,
            items=recorded_items,
        )

    def save_extraction(self, snapshot_id: str, page: ExtractedPage) -> ResearchSourceSnapshot:
        extraction_root = self.path.parent / "research_extracts"
        extraction_root.mkdir(parents=True, exist_ok=True)
        safe_name = snapshot_id.replace(":", "_").replace("/", "_")
        text_path = extraction_root / f"{safe_name}.txt"
        text_path.write_text(page.text, encoding="utf-8")
        with self._lock:
            payload = self._load()
            raw = payload["snapshots"].get(snapshot_id)
            if raw is None:
                text_path.unlink(missing_ok=True)
                raise KeyError(f"research snapshot not found: {snapshot_id}")
            snapshot = ResearchSourceSnapshot.model_validate(raw).model_copy(
                update={
                    "published_at": page.published_at or raw.get("published_at"),
                    "extractor_version": page.extractor_version,
                    "extraction_status": "completed",
                    "content_hash": page.content_hash,
                    "extracted_text_ref": str(text_path),
                }
            )
            payload["snapshots"][snapshot_id] = snapshot.model_dump(mode="json")
            self._save(payload)
        return snapshot

    def mark_extraction_failed(self, snapshot_id: str) -> ResearchSourceSnapshot | None:
        with self._lock:
            payload = self._load()
            raw = payload["snapshots"].get(snapshot_id)
            if raw is None:
                return None
            snapshot = ResearchSourceSnapshot.model_validate(raw).model_copy(
                update={"extraction_status": "failed"}
            )
            payload["snapshots"][snapshot_id] = snapshot.model_dump(mode="json")
            self._save(payload)
        return snapshot

    def get_manifest(self, manifest_id: str) -> RecordedResearchSources | None:
        with self._lock:
            payload = self._load()
        raw_manifest = payload["manifests"].get(manifest_id)
        if not raw_manifest:
            return None
        manifest = ResearchSourceManifest.model_validate(raw_manifest)
        sources = [
            ResearchSource.model_validate(payload["sources"][source_id])
            for source_id in manifest.source_record_ids
            if source_id in payload["sources"]
        ]
        snapshots = [
            ResearchSourceSnapshot.model_validate(payload["snapshots"][snapshot_id])
            for snapshot_id in manifest.snapshot_ids
            if snapshot_id in payload["snapshots"]
        ]
        return RecordedResearchSources(manifest=manifest, sources=sources, snapshots=snapshots)

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {"sources": {}, "snapshots": {}, "manifests": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sources": {}, "snapshots": {}, "manifests": {}}
        return {
            "sources": dict(payload.get("sources") or {}),
            "snapshots": dict(payload.get("snapshots") or {}),
            "manifests": dict(payload.get("manifests") or {}),
        }

    def _save(self, payload: dict[str, dict]) -> None:
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


def stable_source_record_id(
    provider: str,
    item: Any,
    *,
    canonical_url: str | None = None,
) -> str:
    identity = canonical_url or "|".join(
        (
            str(provider or item.metadata.get("provider") or "unknown").lower(),
            " ".join(item.title.lower().split()),
            " ".join(item.content.lower().split())[:1000],
        )
    )
    return f"source:{_digest(identity)[:24]}"


def default_research_source_store_path() -> Path:
    override = _env_str("OMNIX_RESEARCH_SOURCE_STORE_PATH")
    return Path(override) if override else resources_data_root() / "research_sources.json"


def default_research_source_store() -> ResearchSourceStore:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.research.persistence.source_store import production_research_source_store
        return production_research_source_store()
    return ResearchSourceStore()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
