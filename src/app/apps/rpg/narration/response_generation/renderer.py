from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .contracts import (
    RESPONSE_WORD_BUDGETS,
    RenderedResponse,
    ResponseMode,
    SectionType,
    SemanticResponsePlan,
    SemanticSection,
    collect_claim_refs,
)


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")


class ResponseRenderer:
    """Render approved semantic sections without reassembling legacy log fields."""

    def render(
        self,
        plan: SemanticResponsePlan,
        *,
        authoritative_deltas: Mapping[str, Any] | None = None,
        truth_classes: Iterable[str] = (),
        lifetimes: Iterable[str] = (),
        repair_history: Iterable[str] = (),
        quality_report: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RenderedResponse:
        sections = self._deduplicate_sections(plan.sections)
        ordered = self._order_sections(plan.mode, sections)
        text = self._join_sections(plan.mode, ordered)
        delivery_units = self._delivery_units(text)
        return RenderedResponse(
            text=text,
            mode=plan.mode,
            approved_section_ids=tuple(section.section_id for section in ordered),
            resolved_claim_refs=collect_claim_refs(ordered),
            truth_classes=tuple(dict.fromkeys(str(value) for value in truth_classes if value)),
            lifetimes=tuple(dict.fromkeys(str(value) for value in lifetimes if value)),
            word_budget=RESPONSE_WORD_BUDGETS[plan.mode],
            repair_history=tuple(repair_history),
            delivery_units=delivery_units,
            quality_report=dict(quality_report or {}),
            authoritative_deltas=dict(authoritative_deltas or {}),
            metadata={
                "forward_strategy": plan.forward_strategy,
                "agency_effect": plan.agency_effect.value,
                "reversibility": plan.reversibility.value,
                **dict(metadata or {}),
            },
        )

    def _deduplicate_sections(
        self,
        sections: Iterable[SemanticSection],
    ) -> tuple[SemanticSection, ...]:
        kept: list[SemanticSection] = []
        normalized: list[str] = []
        for section in sections:
            text = section.normalized_text()
            if not text:
                continue
            if any(self._same_event_text(text, previous) for previous in normalized):
                continue
            kept.append(section)
            normalized.append(text)
        return tuple(kept)

    @staticmethod
    def _same_event_text(left: str, right: str) -> bool:
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) < 18:
            return False
        if shorter in longer and len(shorter) / max(1, len(longer)) >= 0.72:
            return True
        left_tokens = set(_TOKEN_PATTERN.findall(left))
        right_tokens = set(_TOKEN_PATTERN.findall(right))
        if min(len(left_tokens), len(right_tokens)) < 5:
            return False
        union = left_tokens | right_tokens
        if not union:
            return False
        return len(left_tokens & right_tokens) / len(union) >= 0.72

    @staticmethod
    def _order_sections(
        mode: ResponseMode,
        sections: tuple[SemanticSection, ...],
    ) -> tuple[SemanticSection, ...]:
        priorities: dict[ResponseMode, dict[SectionType, int]] = {
            ResponseMode.DIALOGUE: {
                SectionType.NPC_DIALOGUE: 0,
                SectionType.NARRATION: 1,
                SectionType.CHOICE: 2,
            },
            ResponseMode.TRANSACTION: {
                SectionType.NPC_DIALOGUE: 0,
                SectionType.RESULT: 1,
                SectionType.NARRATION: 2,
                SectionType.CHOICE: 3,
            },
            ResponseMode.COMBAT: {
                SectionType.ACTION: 0,
                SectionType.RESULT: 1,
                SectionType.NARRATION: 2,
                SectionType.NPC_DIALOGUE: 3,
            },
            ResponseMode.RECOVERY: {
                SectionType.CLARIFICATION: 0,
                SectionType.NPC_DIALOGUE: 1,
                SectionType.NARRATION: 2,
                SectionType.CHOICE: 3,
            },
            ResponseMode.FAILURE: {
                SectionType.RESULT: 0,
                SectionType.NPC_DIALOGUE: 1,
                SectionType.CHOICE: 2,
                SectionType.NARRATION: 3,
            },
        }
        mode_priorities = priorities.get(mode, {})
        ordered = sorted(
            enumerate(sections),
            key=lambda item: (
                mode_priorities.get(item[1].section_type, 10),
                item[0],
            ),
        )
        return tuple(item[1] for item in ordered)

    @staticmethod
    def _join_sections(
        mode: ResponseMode,
        sections: tuple[SemanticSection, ...],
    ) -> str:
        if not sections:
            return ""
        blocks: list[str] = []
        for section in sections:
            text = section.text.strip()
            if not text:
                continue
            if section.section_type is SectionType.NPC_DIALOGUE and section.speaker_id:
                if not text.startswith(("\"", "“")):
                    text = f'“{text}”'
            blocks.append(text)
        separator = "\n\n" if mode in {ResponseMode.MAJOR_BEAT, ResponseMode.DIALOGUE} else " "
        return separator.join(blocks).strip()

    @staticmethod
    def _delivery_units(text: str) -> tuple[str, ...]:
        if not text.strip():
            return ()
        units = [part.strip() for part in _SENTENCE_BOUNDARY.split(text.strip()) if part.strip()]
        return tuple(units or [text.strip()])
