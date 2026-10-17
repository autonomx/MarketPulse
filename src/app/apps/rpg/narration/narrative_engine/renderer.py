"""Ordered rendering for approved canonical narrative blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .authority import BeatKind
from .contracts import CanonicalNarrativeResponse, NarrativeBlock, ordered_blocks

_TOKEN_PATTERN = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class RenderedNarrative:
    response_id: str
    text: str
    blocks: tuple[NarrativeBlock, ...]
    block_ids: tuple[str, ...]


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _same_event(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 18 and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.72:
        return True
    left_tokens = set(_TOKEN_PATTERN.findall(left))
    right_tokens = set(_TOKEN_PATTERN.findall(right))
    if min(len(left_tokens), len(right_tokens)) < 5:
        return False
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens)) >= 0.78


def duplicate_block_pairs(blocks: Iterable[NarrativeBlock]) -> tuple[tuple[str, str], ...]:
    """Return likely unintended duplicate pairs without mutating canonical order."""

    ordered = ordered_blocks(tuple(blocks))
    pairs: list[tuple[str, str]] = []
    normalized: list[tuple[str, str]] = []
    for block in ordered:
        text = _normalized(block.text)
        if not text:
            continue
        for previous_id, previous_text in normalized:
            if _same_event(text, previous_text):
                pairs.append((previous_id, block.block_id))
                break
        normalized.append((block.block_id, text))
    return tuple(pairs)


def deduplicate_blocks(blocks: Iterable[NarrativeBlock]) -> tuple[NarrativeBlock, ...]:
    """Legacy diagnostic helper.

    Canonical renderers and projections must never call this function. Duplicate
    prose is rejected before response approval instead of silently deleting blocks.
    """

    duplicate_ids = {right for _, right in duplicate_block_pairs(blocks)}
    return tuple(
        block
        for block in ordered_blocks(tuple(blocks))
        if block.block_id not in duplicate_ids
    )


def render_block_text(block: NarrativeBlock) -> str:
    text = block.text.strip()
    if not text:
        return ""
    if block.kind is BeatKind.DIALOGUE and not text.startswith(("\"", "“")):
        return f"“{text}”"
    return text


def render_plain_text(blocks: Iterable[NarrativeBlock]) -> str:
    rendered = [render_block_text(block) for block in ordered_blocks(tuple(blocks))]
    return "\n\n".join(text for text in rendered if text).strip()


class CanonicalNarrativeRenderer:
    """Render canonical blocks without changing planner sequence or membership."""

    def render(self, response: CanonicalNarrativeResponse) -> RenderedNarrative:
        blocks = ordered_blocks(response.blocks)
        return RenderedNarrative(
            response_id=response.response_id,
            text=render_plain_text(blocks),
            blocks=blocks,
            block_ids=tuple(block.block_id for block in blocks),
        )
