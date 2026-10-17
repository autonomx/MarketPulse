"""Storyteller shared-asset save helpers for the browser gateway."""
from __future__ import annotations

from app.assets.protocol import AssetStore
from collections.abc import Iterator
from contextlib import contextmanager
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.assets import AssetRecord, AssetType, PublicAssetRecord


class SaveStoryAssetRequest(BaseModel):
    """Request body for saving the active Storyteller manuscript."""

    title: str = "Untitled story"
    content: str
    premise: str = ""
    provider_label: str = ""
    word_count: int = Field(default=0, ge=0)
    chapter_count: int = Field(default=0, ge=0)
    source_job_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedStoryAssetResponse(BaseModel):
    """Saved Storyteller shared asset plus the stored text."""

    asset: PublicAssetRecord
    content: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_story_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = normalized.strip("-")
    return normalized or "untitled-story"


@contextmanager
def _story_staging_dir(asset_store: AssetStore) -> Iterator[Path]:
    """Where the manuscript file is written before the asset is recorded.

    The legacy manifest store keeps files beside its manifest. The PostgreSQL
    store copies the file into the blob store on upsert, so a temporary
    directory suffices (and works on any host).
    """
    manifest_path = getattr(asset_store, "manifest_path", None)
    if manifest_path:
        path = Path(manifest_path).parent / "stories"
        path.mkdir(parents=True, exist_ok=True)
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="omnix-story-") as directory:
        yield Path(directory)


def save_story_asset(asset_store: AssetStore, request: SaveStoryAssetRequest) -> SavedStoryAssetResponse:
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="story_content_required")

    title = request.title.strip() or "Untitled story"
    slug = _safe_story_slug(title)
    unique = uuid.uuid4().hex[:12]
    created_at = _utcnow()
    timestamp = created_at.replace(":", "-").replace("+", "-")
    with _story_staging_dir(asset_store) as directory:
        path = directory / f"{slug}-{timestamp}-{unique}.md"
        try:
            path.write_text(content + "\n", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="story_asset_write_failed") from exc
        stored = asset_store.upsert_asset(_story_record(request, title, slug, unique, created_at, path))
    return SavedStoryAssetResponse(asset=PublicAssetRecord.model_validate(stored), content=content)


def _story_record(
    request: SaveStoryAssetRequest, title: str, slug: str, unique: str, created_at: str, path: Path,
) -> AssetRecord:
    return AssetRecord(
        id=f"story:{slug}:{unique}",
        module="storyteller",
        type=AssetType.STORY,
        mime_type="text/markdown",
        storage_path=str(path),
        metadata={
            "title": title,
            "premise": request.premise,
            "provider_label": request.provider_label,
            "word_count": request.word_count,
            "chapter_count": request.chapter_count,
            **request.metadata,
        },
        source_job_id=request.source_job_id,
        created_at=created_at,
        compat={"contract": "storyteller_saved_asset_v1"},
    )
