"""Bounded AI style proposals with deterministic source-boundary verification."""
from __future__ import annotations

import json
from dataclasses import replace
from collections.abc import Callable, Sequence
from typing import Any

from .dialogue_coverage import audit_dialogue_coverage
from .extraction import resegment_revision
from .models import SourceRevision, SourceSpan
from .spans import STYLE_RULES


DISCOVERY_VERSION = "audiobook-style-discovery-v1"
_MAX_SAMPLES = 5
_SAMPLE_RADIUS = 650
_FRONT_MATTER_TITLES = {"contents", "table of contents", "copyright", "title page"}


def _all_spans(revision: SourceRevision) -> list[SourceSpan]:
    return [span for chapter in revision.chapters for span in chapter.spans]


def _samples(
    revision: SourceRevision, findings: dict[str, dict[str, Any]],
    *, probe_spans: Sequence[SourceSpan] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    probe_by_chapter: dict[str, list[SourceSpan]] = {}
    for span in probe_spans or _all_spans(revision):
        probe_by_chapter.setdefault(span.chapter_id, []).append(span)

    def chapter_view(chapter) -> str:
        spans = sorted(
            probe_by_chapter.get(chapter.id, []), key=lambda item: item.ordinal
        )
        if not spans:
            return chapter.canonical_text
        return "".join(span.source_text for span in spans)
    first = next((
        chapter for chapter in revision.chapters
        if chapter_view(chapter).strip()
        and chapter.title.strip().casefold() not in _FRONT_MATTER_TITLES
    ), None)
    if first is None:
        first = next((
            chapter for chapter in revision.chapters
            if chapter_view(chapter).strip()
        ), None)
    if first is not None:
        first_view = chapter_view(first)
        samples.append({
            "chapter": first.ordinal + 1,
            "start_offset": 0,
            "text": first_view[:1300],
        })
    candidates = [
        (chapter, span, findings[span.id])
        for chapter in revision.chapters for span in chapter.spans
        if span.id in findings
    ]
    if candidates:
        selected = sorted({0, len(candidates) // 3, 2 * len(candidates) // 3, len(candidates) - 1})
        for position in selected:
            chapter, _span, finding = candidates[position]
            offset = int(finding["source_offset"])
            view = chapter_view(chapter)
            start = max(0, offset - _SAMPLE_RADIUS)
            end = min(len(view), offset + _SAMPLE_RADIUS)
            samples.append({
                "chapter": chapter.ordinal + 1,
                "start_offset": start,
                "text": view[start:end],
            })
    for chapter in revision.chapters:
        if len(samples) >= _MAX_SAMPLES:
            break
        probe_chapter_spans = probe_by_chapter.get(chapter.id, [])
        view = chapter_view(chapter)
        has_dialogue = any(
            span.structural_kind == "dialogue" and span.source_text.strip()
            for span in probe_chapter_spans
        )
        has_finding = any(span.id in findings for span in probe_chapter_spans)
        if (
            len(chapter.canonical_text) < 1_500
            or not view.strip()
            or (has_dialogue and not has_finding)
        ):
            continue
        for offset in (len(chapter.canonical_text) // 2, max(0, len(chapter.canonical_text) - 1300)):
            if len(samples) >= _MAX_SAMPLES:
                break
            samples.append({
                "chapter": chapter.ordinal + 1,
                "start_offset": offset,
                "text": view[offset:offset + 1300],
            })
    return samples[:_MAX_SAMPLES]


def _example_is_new_dialogue(
    before: SourceRevision, after: SourceRevision, example: str,
) -> bool:
    if not 4 <= len(example) <= 300:
        return False
    for old, new in zip(before.chapters, after.chapters, strict=True):
        if example not in old.canonical_text:
            continue
        in_old_narration = any(
            example in span.source_text and span.structural_kind == "narration"
            for span in old.spans
        )
        in_new_dialogue = any(
            example in span.source_text and span.structural_kind == "dialogue"
            for span in new.spans
        )
        if in_old_narration and in_new_dialogue:
            return True
    return False


def _new_dialogue_spans(before: SourceRevision, after: SourceRevision) -> list[SourceSpan]:
    added: list[SourceSpan] = []
    for old, new in zip(before.chapters, after.chapters, strict=True):
        for span in new.spans:
            if span.structural_kind != "dialogue":
                continue
            if any(
                prior.structural_kind == "narration"
                and prior.start_offset < span.end_offset
                and span.start_offset < prior.end_offset
                for prior in old.spans
            ):
                added.append(span)
    return added


def discover_dialogue_styles(
    revision: SourceRevision,
    *, classifier: Callable[[dict[str, Any]], str | dict[str, Any]],
    classifier_details: dict[str, Any] | None = None,
    probe_spans: Sequence[SourceSpan] | None = None,
    custom_rules: str = "",
) -> SourceRevision:
    """Accept only model proposals that yield new, exact, lossless speech spans."""
    probe = list(probe_spans) if probe_spans is not None else _all_spans(revision)
    findings = audit_dialogue_coverage(probe)
    probe_by_chapter: dict[str, list[SourceSpan]] = {}
    for span in probe:
        probe_by_chapter.setdefault(span.chapter_id, []).append(span)
    needs_probe = any(
        len(chapter.canonical_text) >= 1_500
        and any(
            span.source_text.strip()
            for span in probe_by_chapter.get(chapter.id, [])
        )
        and not any(
            span.structural_kind == "dialogue"
            for span in probe_by_chapter.get(chapter.id, [])
        )
        for chapter in revision.chapters
    )
    if not findings and not needs_probe and not custom_rules:
        return revision
    samples = _samples(revision, findings, probe_spans=probe)
    response = classifier({
        "task": "discover_dialogue_style",
        "version": DISCOVERY_VERSION,
        "allowed_styles": list(STYLE_RULES),
        "samples": samples,
        **({"custom_rules": custom_rules} if custom_rules else {}),
    })
    try:
        payload = json.loads(response) if isinstance(response, str) else response
    except (TypeError, ValueError):
        return revision
    if not isinstance(payload, dict) or not isinstance(payload.get("styles"), list):
        return revision

    existing_discovery = revision.metadata.get("dialogue_style_discovery")
    existing_styles = (
        existing_discovery.get("styles")
        if isinstance(existing_discovery, dict) else []
    )
    if not isinstance(existing_styles, list):
        existing_styles = []
    selected: list[str] = [
        item for item in existing_styles
        if isinstance(item, str) and item in STYLE_RULES
    ]
    current = revision
    for proposal in payload["styles"][:len(STYLE_RULES)]:
        if not isinstance(proposal, dict):
            continue
        style = proposal.get("id")
        examples = proposal.get("examples")
        if (
            not isinstance(style, str) or style not in STYLE_RULES
            or style in selected or not isinstance(examples, list)
        ):
            continue
        exact_examples = [item for item in examples if isinstance(item, str)]
        required = 2 if style.endswith("_dash") or style == "speaker_labels" else 1
        if len(set(exact_examples)) < required:
            continue
        if not all(
            any(example in sample["text"] for sample in samples)
            for example in exact_examples[:required]
        ):
            continue
        candidate_styles = tuple(sorted((*selected, style)))
        candidate = resegment_revision(
            revision, styles=candidate_styles,
            discovery={"version": DISCOVERY_VERSION, "styles": list(candidate_styles)},
        )
        if not all(
            _example_is_new_dialogue(current, candidate, example)
            for example in exact_examples[:required]
        ):
            continue
        new_dialogue = _new_dialogue_spans(current, candidate)
        if not new_dialogue or any(len(span.source_text) > 2_000 for span in new_dialogue):
            continue
        selected.append(style)
        current = candidate
    discovery = {
        "version": DISCOVERY_VERSION,
        "styles": selected,
        "provider_id": (classifier_details or {}).get("provider_id"),
        "model": (classifier_details or {}).get("model"),
    }
    if not selected:
        # The model was consulted and deterministic verification found no extra
        # style worth enabling. Persist that negative result as its own durable
        # segmentation identity so identical re-ingests do not repeat the AI call.
        return resegment_revision(revision, styles=(), discovery=discovery)
    if tuple(sorted(selected)) == tuple(sorted(existing_styles)):
        return revision
    # Return the exact candidate whose source-boundary changes were verified above.
    # A second segmentation pass here could drift from the candidate we accepted.
    return replace(
        current,
        metadata={**current.metadata, "dialogue_style_discovery": discovery},
    )
