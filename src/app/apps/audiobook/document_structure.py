"""Document-level structure interpretation and consumer policies.

Immutable SourceSpan text remains canonical. This module derives block/region roles,
provenance, recurrence, and policy projections without mutating source text.
"""
from __future__ import annotations

import logging

import json
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .hashing import text_hash
from .models import SourceRevision, SourceSpan

logger = logging.getLogger(__name__)


DOCUMENT_STRUCTURE_VERSION = "document-role-v2"
RENDER_POLICY_VERSION = "audiobook-render-policy-v1"
ANALYSIS_POLICY_VERSION = "audiobook-analysis-policy-v1"
_AI_FALLBACK_ACCEPT_THRESHOLD = 0.85

READ = "READ"
SKIP = "SKIP"
READ_ONCE = "READ_ONCE"
INCLUDE = "INCLUDE"
CONTEXT_ONLY = "CONTEXT_ONLY"
EXCLUDE = "EXCLUDE"

CONTENT_ROLES = frozenset({
    "story_text", "scene_heading", "scene_break",
    "chapter_heading", "part_heading", "prologue_heading", "epilogue_heading",
    "book_title", "subtitle", "author_name",
    "dedication", "epigraph", "foreword", "preface", "author_note",
    "acknowledgements", "footnote_body", "endnote",
    "table_of_contents", "copyright", "isbn", "publisher_metadata",
    "bibliography", "index", "page_number", "running_header",
    "running_footer", "footnote_marker", "unknown",
})

OPTIONAL_ROLES = frozenset({
    "dedication", "epigraph", "foreword", "preface", "author_note",
    "acknowledgements", "footnote_body", "endnote",
})
NON_STORY_ROLES = frozenset({
    "table_of_contents", "copyright", "isbn", "publisher_metadata",
    "bibliography", "index", "page_number", "running_header",
    "running_footer", "footnote_marker",
})
STORY_STRUCTURE_ROLES = frozenset({
    "chapter_heading", "part_heading", "prologue_heading", "epilogue_heading",
})

_CHAPTER = re.compile(r"^chapter\s+(?:\d+|[ivxlcdm]+|[a-z]+)\b.*$", re.I)
_PART = re.compile(r"^part\s+(?:\d+|[ivxlcdm]+|[a-z]+)\b.*$", re.I)
_PAGE = re.compile(r"^(?:page\s+)?\d+\s*(?:of|/)\s*\d+$|^page\s+\d+$", re.I)
_ISBN = re.compile(r"\bisbn(?:-1[03])?\b", re.I)
_COPYRIGHT = re.compile(r"(?:copyright|©|all rights reserved)", re.I)
_TOC_ENTRY = re.compile(r"(?:\.{2,}|\s)\s*\d{1,4}\s*$")
_FOOTNOTE_BODY = re.compile(r"^\s*\[(\d{1,4})\]\s+\S")
_FOOTNOTE_MARKER = re.compile(r"^\s*\[(\d{1,4})\]\s*$")
_SCENE_BREAK = re.compile(r"^\s*(?:\*\s*){3,}$|^\s*(?:#\s*){3,}$|^\s*[—–-]{3,}\s*$")


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    id: str
    chapter_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    source_span_ids: tuple[str, ...]
    original_text: str
    normalized_text: str
    content_role: str
    confidence: float
    provenance: tuple[dict[str, Any], ...]
    recurrence_group: str | None
    structure_quality: str
    page_index: int | None = None
    page_block_index: int | None = None
    reading_order: int | None = None
    distance_from_top: float | None = None
    distance_from_bottom: float | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    font_size: float | None = None
    font_weight: str | None = None
    font_style: str | None = None
    parent_block_id: str | None = None


@dataclass(frozen=True, slots=True)
class StructuralRegion:
    id: str
    chapter_id: str
    start_offset: int
    end_offset: int
    block_ids: tuple[str, ...]
    content_role: str
    confidence: float
    provenance: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DocumentStructureAnalysis:
    source_revision_id: str
    version: str
    blocks: tuple[DocumentBlock, ...]
    regions: tuple[StructuralRegion, ...] = ()
    ai_fallback_used: bool = False


def normalize_block_text(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _quality(source_format: str) -> str:
    if source_format in {"epub", "docx", "html", "htm"}:
        return "HIGH"
    if source_format == "pdf":
        return "MEDIUM"
    return "MEDIUM"


def _iter_line_blocks(revision: SourceRevision) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    ordinal = 0
    pdf_positions: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    raw_positions = revision.metadata.get("pdf_page_blocks")
    if isinstance(raw_positions, list):
        for item in sorted(
            (item for item in raw_positions if isinstance(item, dict)),
            key=lambda item: (
                int(item.get("page_index") or 0),
                int(item.get("reading_order") or item.get("block_index") or 0),
            ),
        ):
            normalized = normalize_block_text(str(item.get("original_text") or ""))
            if normalized:
                pdf_positions[normalized].append(dict(item))

    def layout_fields(normalized: str) -> dict[str, Any]:
        if not pdf_positions.get(normalized):
            return {}
        item = pdf_positions[normalized].popleft()
        bbox = item.get("bounding_box")
        clean_bbox = None
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                clean_bbox = tuple(float(value) for value in bbox)
            except (TypeError, ValueError):
                clean_bbox = None
        def number(name: str) -> float | None:
            value = item.get(name)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        def integer(name: str) -> int | None:
            value = item.get(name)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        return {
            "page_index": integer("page_index"),
            "page_block_index": integer("block_index"),
            "reading_order": integer("reading_order"),
            "distance_from_top": number("distance_from_top"),
            "distance_from_bottom": number("distance_from_bottom"),
            "bounding_box": clean_bbox,
            "font_size": number("font_size"),
            "font_weight": (
                str(item.get("font_weight")) if item.get("font_weight") else None
            ),
            "font_style": (
                str(item.get("font_style")) if item.get("font_style") else None
            ),
        }

    for chapter in revision.chapters:
        offset = 0
        for raw in chapter.canonical_text.splitlines(keepends=True):
            line = raw.rstrip("\r\n")
            line_end = offset + len(line)
            if line.strip():
                span_ids = tuple(
                    span.id for span in chapter.spans
                    if span.start_offset < line_end and span.end_offset > offset
                )
                normalized = normalize_block_text(line)
                blocks.append(DocumentBlock(
                    id=f"ab:db:{text_hash(f'{revision.id}:{chapter.id}:{offset}:{line_end}:{line}')}",
                    chapter_id=chapter.id,
                    ordinal=ordinal,
                    start_offset=offset,
                    end_offset=line_end,
                    source_span_ids=span_ids,
                    original_text=line,
                    normalized_text=normalized,
                    content_role="unknown",
                    confidence=0.0,
                    provenance=(),
                    recurrence_group=None,
                    structure_quality=_quality(revision.source_format),
                    **layout_fields(normalized),
                ))
                ordinal += 1
            offset += len(raw)
        if offset < len(chapter.canonical_text):
            line = chapter.canonical_text[offset:]
            if line.strip():
                line_end = len(chapter.canonical_text)
                span_ids = tuple(
                    span.id for span in chapter.spans
                    if span.start_offset < line_end and span.end_offset > offset
                )
                blocks.append(DocumentBlock(
                    id=f"ab:db:{text_hash(f'{revision.id}:{chapter.id}:{offset}:{line_end}:{line}')}",
                    chapter_id=chapter.id, ordinal=ordinal,
                    start_offset=offset, end_offset=line_end,
                    source_span_ids=span_ids, original_text=line,
                    normalized_text=normalize_block_text(line),
                    content_role="unknown", confidence=0.0, provenance=(),
                    recurrence_group=None, structure_quality=_quality(revision.source_format),
                    **layout_fields(normalize_block_text(line)),
                ))
                ordinal += 1
    return blocks


def _pdf_edge_evidence(metadata: Mapping[str, Any]) -> tuple[Counter[str], Counter[str]]:
    top: Counter[str] = Counter()
    bottom: Counter[str] = Counter()
    positioned = metadata.get("pdf_page_blocks")
    if isinstance(positioned, list):
        for item in positioned:
            if not isinstance(item, dict):
                continue
            normalized = normalize_block_text(str(item.get("original_text") or ""))
            if not normalized:
                continue
            try:
                distance_top = float(item.get("distance_from_top"))
                distance_bottom = float(item.get("distance_from_bottom"))
            except (TypeError, ValueError):
                continue
            if distance_top <= 0.10:
                top[normalized] += 1
            if distance_bottom <= 0.10:
                bottom[normalized] += 1
        return top, bottom

    pages = metadata.get("pdf_page_edges")
    if not isinstance(pages, list):
        return top, bottom
    for page in pages:
        if not isinstance(page, dict):
            continue
        for value in page.get("top", []) if isinstance(page.get("top"), list) else []:
            normalized = normalize_block_text(str(value))
            if normalized:
                top[normalized] += 1
        for value in page.get("bottom", []) if isinstance(page.get("bottom"), list) else []:
            normalized = normalize_block_text(str(value))
            if normalized:
                bottom[normalized] += 1
    return top, bottom


def _evidence(source: str, signal: str, value: Any) -> dict[str, Any]:
    return {"source": source, "signal": signal, "value": value}


def _with_role(
    block: DocumentBlock, role: str, confidence: float,
    *evidence: dict[str, Any],
) -> DocumentBlock:
    if role not in CONTENT_ROLES:
        role = "unknown"
    return replace(
        block, content_role=role, confidence=max(0.0, min(1.0, confidence)),
        provenance=tuple((*block.provenance, *evidence)),
    )


def _is_body_like(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) >= 100:
        return True
    words = stripped.split()
    return len(words) >= 14 and stripped[-1:] in {".", "!", "?", "”", '"', "’"}


def _all_caps_heading(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and len(text.strip()) <= 80 and all(char.isupper() for char in letters)


def _looks_like_story_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(('"', "“", "‘", "—", "–")):
        return True
    if stripped[-1:] in {".", "!", "?", "”", '"', "’"}:
        return True
    words = stripped.split()
    return len(words) >= 6 and not _all_caps_heading(stripped)


def _initial_roles(
    revision: SourceRevision, blocks: list[DocumentBlock],
) -> list[DocumentBlock]:
    counts = Counter(block.normalized_text for block in blocks if block.normalized_text)
    chapter_counts = Counter(
        (block.chapter_id, block.normalized_text)
        for block in blocks if block.normalized_text
    )
    span_kind_by_id = {
        span.id: span.structural_kind
        for chapter in revision.chapters
        for span in chapter.spans
    }
    top_edges, bottom_edges = _pdf_edge_evidence(revision.metadata)
    semantic_heading_offsets: dict[str, set[int]] = defaultdict(set)
    legacy_epub_heading_counts: Counter[tuple[str, str]] = Counter()
    raw_epub_headings = revision.metadata.get("epub_semantic_headings")
    if isinstance(raw_epub_headings, list):
        for item in raw_epub_headings:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            try:
                chapter_index = int(item.get("chapter_index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= chapter_index < len(revision.chapters):
                continue
            chapter_id = revision.chapters[chapter_index].id
            normalized_heading = normalize_block_text(str(item.get("text") or ""))
            raw_start = item.get("start_offset")
            if isinstance(raw_start, int) and not isinstance(raw_start, bool) and raw_start >= 0:
                semantic_heading_offsets[chapter_id].add(raw_start)
            elif normalized_heading:
                legacy_epub_heading_counts[(chapter_id, normalized_heading)] += 1

    legacy_html_heading_counts: Counter[str] = Counter()
    raw_html_headings = revision.metadata.get("html_semantic_headings")
    if isinstance(raw_html_headings, list):
        for item in raw_html_headings:
            if isinstance(item, dict):
                try:
                    chapter_index = int(item.get("chapter_index"))
                except (TypeError, ValueError):
                    chapter_index = -1
                raw_start = item.get("start_offset")
                if (
                    0 <= chapter_index < len(revision.chapters)
                    and isinstance(raw_start, int)
                    and not isinstance(raw_start, bool)
                    and raw_start >= 0
                ):
                    semantic_heading_offsets[
                        revision.chapters[chapter_index].id
                    ].add(raw_start)
                    continue
                normalized_heading = normalize_block_text(
                    str(item.get("text") or "")
                )
            else:
                normalized_heading = normalize_block_text(str(item))
            if normalized_heading:
                legacy_html_heading_counts[normalized_heading] += 1
    docx_styles: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    raw_docx_styles = revision.metadata.get("docx_style_blocks")
    if isinstance(raw_docx_styles, list):
        for item in sorted(
            (item for item in raw_docx_styles if isinstance(item, dict)),
            key=lambda item: int(item.get("block_index") or 0),
        ):
            normalized_item = normalize_block_text(
                str(item.get("original_text") or item.get("text") or "")
            )
            if normalized_item:
                docx_styles[normalized_item].append(dict(item))
    title = normalize_block_text(str(revision.metadata.get("title") or ""))
    chapter_titles = {
        chapter.id: normalize_block_text(chapter.title)
        for chapter in revision.chapters
        if chapter.title and chapter.title != "Opening"
    }
    creator = normalize_block_text(str(
        revision.metadata.get("creator") or revision.metadata.get("author") or ""
    ))
    seen_title = False
    optional_region: str | None = None
    toc_region = False
    result: list[DocumentBlock] = []

    optional_headings = {
        "dedication": "dedication",
        "epigraph": "epigraph",
        "foreword": "foreword",
        "preface": "preface",
        "author's note": "author_note",
        "authors note": "author_note",
        "author note": "author_note",
        "acknowledgements": "acknowledgements",
        "acknowledgments": "acknowledgements",
        "endnotes": "endnote",
        "notes": "endnote",
    }

    for index, block in enumerate(blocks):
        text = block.original_text.strip()
        normalized = block.normalized_text
        heading_text = re.sub(r"^#{1,6}\s+(?=\S)", "", text).strip()
        heading_normalized = normalize_block_text(heading_text)
        docx_style = (
            docx_styles[normalized].popleft()
            if normalized and docx_styles.get(normalized)
            else None
        )
        source_heading_evidence: dict[str, Any] | None = None
        positioned_heading = any(
            block.start_offset <= offset < block.end_offset
            for offset in semantic_heading_offsets.get(block.chapter_id, set())
        )
        legacy_epub_key = (block.chapter_id, normalized)
        if positioned_heading:
            source_heading_evidence = _evidence(
                "source_semantic", "heading_markup", True
            )
        elif (
            normalized
            and legacy_epub_heading_counts[legacy_epub_key] > 0
            and chapter_counts[legacy_epub_key] == 1
        ):
            legacy_epub_heading_counts[legacy_epub_key] -= 1
            source_heading_evidence = _evidence(
                "source_semantic", "heading_markup_legacy_unique", True
            )
        elif (
            normalized
            and legacy_html_heading_counts[normalized] > 0
            and counts[normalized] == 1
        ):
            legacy_html_heading_counts[normalized] -= 1
            source_heading_evidence = _evidence(
                "source_semantic", "heading_markup_legacy_unique", True
            )
        elif (
            docx_style is not None
            and str(docx_style.get("style") or "").casefold().startswith("heading")
        ):
            source_heading_evidence = _evidence(
                "docx_style", "style", str(docx_style.get("style") or "")
            )
        recurrence = None
        if normalized and counts[normalized] >= 2 and len(normalized) <= 120:
            recurrence = f"ab:rg:{text_hash(normalized)}"
            block = replace(block, recurrence_group=recurrence)

        chapter_changed = (
            index == 0 or blocks[index - 1].chapter_id != block.chapter_id
        )
        if chapter_changed:
            optional_region = None
            toc_region = False

        role: DocumentBlock | None = None

        # Strong source/document identity evidence. Explicit document metadata
        # and document-level styles outrank chapter boundaries derived from the
        # same heading text (for example an EPUB title page that is also a spine
        # item or a Markdown/DOCX title that extraction split into a chapter).
        if title and heading_normalized == title and not seen_title:
            seen_title = True
            role = _with_role(
                block, "book_title", 0.995,
                _evidence("source_metadata", "title_match", True),
            )
        elif (
            docx_style is not None
            and str(docx_style.get("style") or "").casefold() == "title"
        ):
            role = _with_role(
                block, "book_title", 0.985,
                _evidence("docx_style", "style", "Title"),
            )
        elif (
            docx_style is not None
            and str(docx_style.get("style") or "").casefold() == "subtitle"
        ):
            role = _with_role(
                block, "subtitle", 0.98,
                _evidence("docx_style", "style", "Subtitle"),
            )
        elif creator and heading_normalized == creator and index < 12:
            role = _with_role(
                block, "author_name", 0.99,
                _evidence("source_metadata", "creator_match", True),
            )
        elif (
            chapter_titles.get(block.chapter_id)
            and heading_normalized == chapter_titles[block.chapter_id]
            and heading_normalized not in optional_headings
            and heading_normalized not in {
                "contents", "table of contents", "bibliography", "references",
                "index", "prologue", "epilogue",
            }
            and not _PART.fullmatch(heading_text)
        ):
            role = _with_role(
                block, "chapter_heading", 0.999,
                _evidence("source_semantic", "chapter_title_match", True),
            )

        # Repetition is header/footer evidence only for an occurrence that
        # actually sits at the page edge. This prevents a repeated title phrase
        # inside body prose from being suppressed merely because identical text
        # also appears in running headers.
        top_count = top_edges[normalized]
        bottom_count = bottom_edges[normalized]
        at_top = (
            block.distance_from_top is not None
            and block.distance_from_top <= 0.10
        )
        at_bottom = (
            block.distance_from_bottom is not None
            and block.distance_from_bottom <= 0.10
        )
        has_positioned_pdf_evidence = bool(
            revision.metadata.get("pdf_page_blocks")
        )
        edge_occurrence = (
            (at_top and top_count >= 2)
            or (at_bottom and bottom_count >= 2)
            or (
                not has_positioned_pdf_evidence
                and max(top_count, bottom_count) >= 2
            )
        )
        # A repeated "Contents" heading still opens the table of contents (it
        # also tops each of its pages); the TOC rule below claims it instead.
        if (
            role is None and len(normalized) <= 120 and edge_occurrence
            and heading_normalized not in {"contents", "table of contents"}
        ):
            if _PAGE.fullmatch(text) or text.isdigit():
                role = _with_role(
                    block, "page_number", 0.999,
                    _evidence("pdf_layout", "page_edge_recurrence", max(top_count, bottom_count)),
                )
            else:
                use_top = at_top if has_positioned_pdf_evidence else top_count >= bottom_count
                edge_role = "running_header" if use_top else "running_footer"
                role = _with_role(
                    block, edge_role, 0.997,
                    _evidence("repetition", "same_normalized_text_page_count", max(top_count, bottom_count)),
                    _evidence("pdf_layout", "edge", "top" if use_top else "bottom"),
                    _evidence("pdf_layout", "page_index", block.page_index),
                )

        if role is None and _PAGE.fullmatch(text):
            role = _with_role(block, "page_number", 0.995, _evidence("pattern", "page_number", text))
        elif role is None and _ISBN.search(text):
            role = _with_role(block, "isbn", 0.999, _evidence("pattern", "isbn", True))
        elif role is None and _COPYRIGHT.search(text):
            role = _with_role(block, "copyright", 0.995, _evidence("pattern", "copyright", True))
        elif role is None and heading_normalized in {"contents", "table of contents"}:
            toc_region = True
            role = _with_role(block, "table_of_contents", 0.999, _evidence("sequence_rule", "toc_heading", True))
        elif role is None and heading_normalized in {"bibliography", "references"}:
            optional_region = "bibliography"
            role = _with_role(block, "bibliography", 0.99, _evidence("sequence_rule", "section_heading", normalized))
        elif role is None and heading_normalized == "index":
            optional_region = "index"
            role = _with_role(block, "index", 0.99, _evidence("sequence_rule", "section_heading", normalized))
        elif role is None and heading_normalized in optional_headings:
            optional_region = optional_headings[heading_normalized]
            role = _with_role(block, optional_region, 0.99, _evidence("sequence_rule", "optional_section_heading", heading_normalized))
        elif role is None and _CHAPTER.fullmatch(heading_text) and not toc_region:
            # Inside a table of contents a "Chapter N" line is an entry; the
            # chapter itself starts its own source chapter, which resets the region.
            optional_region = None
            role = _with_role(block, "chapter_heading", 0.995, _evidence("pattern", "chapter_heading", True))
        elif role is None and _PART.fullmatch(heading_text):
            toc_region = False
            optional_region = None
            role = _with_role(block, "part_heading", 0.995, _evidence("pattern", "part_heading", True))
        elif role is None and heading_normalized == "prologue":
            toc_region = False
            optional_region = None
            role = _with_role(block, "prologue_heading", 0.995, _evidence("pattern", "prologue", True))
        elif role is None and heading_normalized == "epilogue":
            toc_region = False
            optional_region = None
            role = _with_role(block, "epilogue_heading", 0.995, _evidence("pattern", "epilogue", True))
        elif role is None and _SCENE_BREAK.fullmatch(text):
            role = _with_role(block, "scene_break", 0.995, _evidence("pattern", "scene_break", text))
        elif role is None and _FOOTNOTE_MARKER.fullmatch(text):
            role = _with_role(block, "footnote_marker", 0.97, _evidence("pattern", "footnote_marker", True))
        elif role is None and _FOOTNOTE_BODY.match(text):
            role = _with_role(block, "footnote_body", 0.9, _evidence("pattern", "footnote_body", True))

        if role is None and toc_region:
            if _TOC_ENTRY.search(text) or _CHAPTER.fullmatch(heading_text) or _PART.fullmatch(heading_text):
                role = _with_role(block, "table_of_contents", 0.98, _evidence("sequence_rule", "inside_toc_region", True))
            elif _is_body_like(text):
                toc_region = False
            else:
                role = _with_role(block, "table_of_contents", 0.82, _evidence("sequence_rule", "inside_toc_region", True))

        if role is None and optional_region:
            if optional_region in {"bibliography", "index"}:
                role = _with_role(block, optional_region, 0.9, _evidence("sequence_rule", "inside_section", optional_region))
            elif not _CHAPTER.fullmatch(heading_text) and heading_normalized not in {"prologue", "epilogue"}:
                role = _with_role(block, optional_region, 0.9, _evidence("sequence_rule", "inside_optional_section", optional_region))
            else:
                optional_region = None

        if role is None and source_heading_evidence is not None:
            role = _with_role(
                block, "scene_heading", 0.94, source_heading_evidence
            )

        if role is None and any(
            span_kind_by_id.get(span_id) == "dialogue"
            for span_id in block.source_span_ids
        ):
            role = _with_role(
                block, "story_text", 0.995,
                _evidence("source_span", "dialogue_overlap", True),
            )
        elif role is None and _is_body_like(text):
            role = _with_role(block, "story_text", 0.99, _evidence("pattern", "body_prose", True))
        elif role is None and _looks_like_story_line(text):
            role = _with_role(block, "story_text", 0.94, _evidence("pattern", "story_line", True))
        elif role is None and len(text) > 80:
            role = _with_role(block, "story_text", 0.95, _evidence("pattern", "long_text", len(text)))
        elif role is None and _all_caps_heading(text) and counts[normalized] == 1:
            # Short all-caps lines may be scene/location headings. Keep them
            # analysis-visible and spoken in Standard mode; ambiguity remains explicit.
            role = _with_role(block, "scene_heading", 0.72, _evidence("pattern", "short_all_caps_heading", True))
        elif role is None:
            # Fail open. UNKNOWN is read and visible unless AI or a user override
            # supplies stronger structure evidence.
            role = _with_role(block, "unknown", 0.45, _evidence("deterministic", "insufficient_evidence", True))

        result.append(role)
    return result


def _ambiguous_regions(blocks: Sequence[DocumentBlock]) -> list[list[DocumentBlock]]:
    regions: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    previous_ordinal: int | None = None
    previous_chapter: str | None = None
    for block in blocks:
        ambiguous = block.content_role == "unknown" or block.confidence < 0.70
        contiguous = (
            previous_ordinal is not None
            and block.ordinal == previous_ordinal + 1
            and block.chapter_id == previous_chapter
        )
        if ambiguous:
            if current and not contiguous:
                regions.append(current)
                current = []
            current.append(block)
        elif current:
            regions.append(current)
            current = []
        previous_ordinal = block.ordinal
        previous_chapter = block.chapter_id
    if current:
        regions.append(current)
    return regions


def _parse_ai_result(raw: object, expected: set[str]) -> dict[str, tuple[str, float]]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
        raise ValueError("document structure classifier must return blocks")
    parsed: dict[str, tuple[str, float]] = {}
    for item in raw["blocks"]:
        if not isinstance(item, dict):
            continue
        block_id = item.get("block_id")
        role = item.get("content_role")
        confidence = item.get("confidence")
        if (
            isinstance(block_id, str) and block_id in expected
            and isinstance(role, str) and role in CONTENT_ROLES
            and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        ):
            parsed[block_id] = (role, max(0.0, min(1.0, float(confidence))))
    return parsed


def _build_structural_regions(
    blocks: Sequence[DocumentBlock],
) -> tuple[list[DocumentBlock], tuple[StructuralRegion, ...]]:
    """Build maximal contiguous same-role regions and attach their stable IDs."""
    if not blocks:
        return [], ()
    grouped: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    for block in blocks:
        contiguous = (
            current
            and current[-1].chapter_id == block.chapter_id
            and current[-1].ordinal + 1 == block.ordinal
            and current[-1].content_role == block.content_role
        )
        if current and not contiguous:
            grouped.append(current)
            current = []
        current.append(block)
    if current:
        grouped.append(current)

    regions: list[StructuralRegion] = []
    projected: list[DocumentBlock] = []
    for group in grouped:
        first, last = group[0], group[-1]
        region_key = (
            f"{first.chapter_id}:{first.start_offset}:"
            f"{last.end_offset}:{first.content_role}"
        )
        region_id = f"ab:dr:{text_hash(region_key)}"
        evidence: list[dict[str, Any]] = []
        for block in group:
            for item in block.provenance:
                if item not in evidence:
                    evidence.append(item)
        region = StructuralRegion(
            id=region_id,
            chapter_id=first.chapter_id,
            start_offset=first.start_offset,
            end_offset=last.end_offset,
            block_ids=tuple(block.id for block in group),
            content_role=first.content_role,
            confidence=min(block.confidence for block in group),
            provenance=tuple(evidence),
        )
        regions.append(region)
        projected.extend(replace(block, parent_block_id=region_id) for block in group)
    return projected, tuple(regions)


def analyze_document_structure(
    revision: SourceRevision,
    *, region_classifier: Callable[[dict[str, Any]], object] | None = None,
) -> DocumentStructureAnalysis:
    """Classify document blocks deterministically, with optional region AI fallback."""
    blocks = _initial_roles(revision, _iter_line_blocks(revision))
    ai_used = False
    if region_classifier is not None:
        by_ordinal = {block.ordinal: block for block in blocks}
        replacements: dict[str, DocumentBlock] = {}
        for region in _ambiguous_regions(blocks):
            first, last = region[0], region[-1]
            previous = by_ordinal.get(first.ordinal - 1)
            following = by_ordinal.get(last.ordinal + 1)
            payload = {
                "task": "classify_ambiguous_document_region",
                "version": DOCUMENT_STRUCTURE_VERSION,
                "source_format": revision.source_format,
                "allowed_roles": sorted(CONTENT_ROLES),
                "instruction": (
                    "Classify only the supplied ambiguous blocks. UNKNOWN is valid. "
                    "Do not rewrite source text. Return JSON with key blocks; each item "
                    "must contain block_id, content_role, confidence."
                ),
                "previous": previous.original_text if previous and previous.chapter_id == first.chapter_id else None,
                "region": [{"block_id": item.id, "text": item.original_text} for item in region],
                "next": following.original_text if following and following.chapter_id == first.chapter_id else None,
            }
            try:
                parsed = _parse_ai_result(
                    region_classifier(payload), {item.id for item in region}
                )
            except Exception:
                logger.debug("suppressed error in %s", "analyze_document_structure", exc_info=True)
                continue
            for item in region:
                result = parsed.get(item.id)
                if result is None:
                    continue
                role, confidence = result
                if role == "unknown":
                    continue
                if confidence < _AI_FALLBACK_ACCEPT_THRESHOLD:
                    replacements[item.id] = replace(
                        item,
                        provenance=tuple((
                            *item.provenance,
                            _evidence(
                                "ai_fallback",
                                "rejected_low_confidence",
                                {"role": role, "confidence": confidence},
                            ),
                        )),
                    )
                    continue
                replacements[item.id] = _with_role(
                    item, role, confidence,
                    _evidence("ai_fallback", "ambiguous_region", True),
                )
                ai_used = True
        blocks = [replacements.get(block.id, block) for block in blocks]
    blocks, regions = _build_structural_regions(blocks)
    return DocumentStructureAnalysis(
        source_revision_id=revision.id,
        version=DOCUMENT_STRUCTURE_VERSION,
        blocks=tuple(blocks),
        regions=regions,
        ai_fallback_used=ai_used,
    )


def render_policy(role: str, mode: str = "standard") -> str:
    """Derive speech behavior from role and audiobook mode."""
    mode = (mode or "standard").strip().casefold()
    if mode == "verbatim":
        return READ
    if role in NON_STORY_ROLES:
        return SKIP
    if role == "scene_break":
        return SKIP
    if mode == "story_only":
        return READ if role in {"story_text", "scene_heading", "unknown"} else SKIP
    if role in {"book_title"}:
        return READ_ONCE
    if role in STORY_STRUCTURE_ROLES or role in {"story_text", "scene_heading", "unknown"}:
        return READ
    if role in OPTIONAL_ROLES or role in {"subtitle", "author_name"}:
        return SKIP
    return READ


def analysis_policy(role: str, consumer: str) -> str:
    """Derive visibility per downstream consumer rather than persisting it."""
    consumer = (consumer or "speaker_attribution").strip().casefold()
    if role in NON_STORY_ROLES:
        return EXCLUDE
    if consumer == "chapter_summarizer":
        return INCLUDE if role != "footnote_marker" else EXCLUDE
    if consumer in {"dialogue_pronunciation", "pronunciation", "dialogue_coverage"}:
        return INCLUDE if role in {"story_text", "unknown"} else EXCLUDE
    if consumer == "speaker_attribution":
        if role in {"story_text", "unknown"}:
            return INCLUDE
        if role in STORY_STRUCTURE_ROLES or role in {"scene_heading", "scene_break"}:
            return CONTEXT_ONLY
        if role in OPTIONAL_ROLES:
            return CONTEXT_ONLY
        return EXCLUDE
    return INCLUDE


def _override_rank(scope: str) -> int:
    return {
        "BLOCK": 4, "REGION": 3, "RECURRENCE_GROUP": 3, "DOCUMENT_ROLE": 2,
    }.get(scope, 0)


def _matching_overrides(
    block: DocumentBlock, overrides: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for item in overrides:
        scope = str(item.get("scope") or "").upper()
        key = str(item.get("scope_key") or "")
        if (
            (scope == "BLOCK" and key == block.id)
            or (scope == "REGION" and block.parent_block_id and key == block.parent_block_id)
            or (scope == "RECURRENCE_GROUP" and block.recurrence_group and key == block.recurrence_group)
            or (scope == "DOCUMENT_ROLE" and key == block.content_role)
        ):
            matches.append(item)
    matches.sort(key=lambda item: (
        _override_rank(str(item.get("scope") or "").upper()),
        int(item.get("revision") or 0),
    ), reverse=True)
    return matches


def effective_role(
    block: DocumentBlock, overrides: Sequence[Mapping[str, Any]] = (),
) -> str:
    for item in _matching_overrides(block, overrides):
        role = str(item.get("role_override") or "")
        if role in CONTENT_ROLES:
            return role
    return block.content_role


def effective_render_action(
    block: DocumentBlock, *, mode: str,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> str:
    for item in _matching_overrides(block, overrides):
        action = str(item.get("action") or "DEFAULT").upper()
        if action in {READ, SKIP, READ_ONCE}:
            return action
        if action == "DEFAULT":
            break
    return render_policy(effective_role(block, overrides), mode)


def dialogue_targets_for_analysis(
    spans: Sequence[SourceSpan], blocks: Sequence[DocumentBlock], *,
    consumer: str = "speaker_attribution",
    overrides: Sequence[Mapping[str, Any]] = (),
) -> set[str]:
    """Return dialogue IDs that are actual semantic targets for one consumer."""
    targets: set[str] = set()
    for span in spans:
        if span.structural_kind != "dialogue":
            continue
        overlapping = [
            block for block in blocks
            if block.chapter_id == span.chapter_id
            and block.start_offset < span.end_offset
            and block.end_offset > span.start_offset
        ]
        if not overlapping:
            targets.add(span.id)
            continue
        visibilities = {
            analysis_policy(effective_role(block, overrides), consumer)
            for block in overlapping
        }
        if INCLUDE in visibilities:
            targets.add(span.id)
    return targets


def mask_span_for_analysis(
    span: SourceSpan, blocks: Sequence[DocumentBlock], *,
    consumer: str = "speaker_attribution",
    overrides: Sequence[Mapping[str, Any]] = (),
) -> SourceSpan:
    """Blank excluded block ranges while preserving span identity and offsets."""
    chars = list(span.source_text)
    for block in blocks:
        if block.chapter_id != span.chapter_id:
            continue
        if analysis_policy(effective_role(block, overrides), consumer) != EXCLUDE:
            continue
        start = max(span.start_offset, block.start_offset)
        end = min(span.end_offset, block.end_offset)
        if start >= end:
            continue
        for absolute in range(start, end):
            relative = absolute - span.start_offset
            if 0 <= relative < len(chars) and chars[relative] not in {"\n", "\r"}:
                chars[relative] = " "
    return replace(span, source_text="".join(chars))


def mask_span_for_render(
    span: SourceSpan, blocks: Sequence[DocumentBlock], *, mode: str = "standard",
    overrides: Sequence[Mapping[str, Any]] = (),
    read_once_block_ids: set[str] | None = None,
) -> str:
    """Return a derived speech view; canonical SourceSpan text remains untouched."""
    chars = list(span.source_text)
    for block in blocks:
        if block.chapter_id != span.chapter_id:
            continue
        action = effective_render_action(block, mode=mode, overrides=overrides)
        if action == READ_ONCE:
            if read_once_block_ids is not None and block.id not in read_once_block_ids:
                action = SKIP
        if action != SKIP:
            continue
        start = max(span.start_offset, block.start_offset)
        end = min(span.end_offset, block.end_offset)
        if start >= end:
            continue
        for absolute in range(start, end):
            relative = absolute - span.start_offset
            if 0 <= relative < len(chars) and chars[relative] not in {"\n", "\r"}:
                chars[relative] = " "
    return "".join(chars)
