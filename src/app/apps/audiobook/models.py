from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSpan:
    id: str
    chapter_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    source_text: str
    source_hash: str
    structural_kind: str
    detector_version: str


@dataclass(frozen=True, slots=True)
class CanonicalChapter:
    id: str
    ordinal: int
    title: str
    canonical_text: str
    canonical_hash: str
    spans: tuple[SourceSpan, ...]
    structure: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceRevision:
    id: str
    project_id: str
    original_asset_hash: str
    source_format: str
    extractor_version: str
    extraction_settings: dict[str, Any]
    extraction_settings_hash: str
    canonical_hash: str
    chapters: tuple[CanonicalChapter, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
