from __future__ import annotations

from .hashing import object_hash, text_hash
from .models import CanonicalChapter, SourceRevision


class SourceIntegrityError(ValueError):
    """A canonical source revision cannot be reconstructed exactly."""


def validate_chapter(chapter: CanonicalChapter) -> None:
    if text_hash(chapter.canonical_text) != chapter.canonical_hash:
        raise SourceIntegrityError(f"chapter {chapter.id} hash mismatch")
    cursor = 0
    seen: set[str] = set()
    for ordinal, span in enumerate(chapter.spans):
        if span.id in seen or span.ordinal != ordinal:
            raise SourceIntegrityError(f"chapter {chapter.id} duplicate or reordered span")
        seen.add(span.id)
        if span.chapter_id != chapter.id or span.start_offset != cursor:
            raise SourceIntegrityError(f"chapter {chapter.id} gap or overlap at span {ordinal}")
        if span.end_offset <= cursor or span.end_offset > len(chapter.canonical_text):
            raise SourceIntegrityError(f"chapter {chapter.id} invalid span end")
        if chapter.canonical_text[cursor:span.end_offset] != span.source_text:
            raise SourceIntegrityError(f"chapter {chapter.id} span source mismatch")
        if text_hash(span.source_text) != span.source_hash:
            raise SourceIntegrityError(f"chapter {chapter.id} span hash mismatch")
        cursor = span.end_offset
    if cursor != len(chapter.canonical_text):
        raise SourceIntegrityError(f"chapter {chapter.id} trailing source is missing")


def validate_revision(revision: SourceRevision) -> None:
    if object_hash(revision.extraction_settings) != revision.extraction_settings_hash:
        raise SourceIntegrityError("extraction settings hash mismatch")
    if not revision.chapters:
        raise SourceIntegrityError("source has no chapters")
    for ordinal, chapter in enumerate(revision.chapters):
        if chapter.ordinal != ordinal:
            raise SourceIntegrityError("chapters are out of order")
        validate_chapter(chapter)
    # Canonical source identity is content/extractor/settings only. The span
    # detector version is intentionally bound into SourceRevision.id by the
    # extractor so detector upgrades create a distinct durable revision without
    # changing the canonical-text hash contract.
    expected = object_hash({
        "extractor_version": revision.extractor_version,
        "settings_hash": revision.extraction_settings_hash,
        "original_asset_hash": revision.original_asset_hash,
        "chapters": [chapter.canonical_hash for chapter in revision.chapters],
    })
    if revision.canonical_hash != expected:
        raise SourceIntegrityError("canonical revision hash mismatch")
