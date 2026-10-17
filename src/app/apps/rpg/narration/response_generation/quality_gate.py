from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .contracts import SemanticResponsePlan, SemanticSection


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_DEBUG_PREFIX = re.compile(r"^(?:result|action|narration|response)\s*:\s*", re.IGNORECASE)
_BANNED_META_PHRASES = (
    "unsupported action",
    "the turn contract",
    "current turn contract",
    "grounding failed",
    "no matching resolver",
    "the model could not determine",
    "the action resolves according to current state",
    "the action resolves according to the current state",
)
_LOW_VALUE_PHRASES = (
    "the air is thick with",
    "a palpable sense of",
    "time seems to stand still",
)


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[str, ...]
    debug_labels: tuple[str, ...] = ()
    banned_meta_phrases: tuple[str, ...] = ()
    low_value_phrases: tuple[str, ...] = ()
    duplicate_sentences: tuple[str, ...] = ()
    repeated_openings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def rewrite_recommended(self) -> bool:
        return bool(self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": list(self.issues),
            "debug_labels": list(self.debug_labels),
            "banned_meta_phrases": list(self.banned_meta_phrases),
            "low_value_phrases": list(self.low_value_phrases),
            "duplicate_sentences": list(self.duplicate_sentences),
            "repeated_openings": list(self.repeated_openings),
            "rewrite_recommended": self.rewrite_recommended,
        }


class QualityGate:
    """Evaluate and deterministically repair the text the player will see."""

    def evaluate(self, text: str) -> QualityReport:
        sentences = _sentences(text)
        lowered = text.casefold()
        debug_labels = tuple(
            match.group(0).strip()
            for sentence in sentences
            if (match := _DEBUG_PREFIX.match(sentence)) is not None
        )
        canonical_sentences = tuple(_DEBUG_PREFIX.sub("", sentence) for sentence in sentences)
        banned = tuple(phrase for phrase in _BANNED_META_PHRASES if phrase in lowered)
        low_value = tuple(phrase for phrase in _LOW_VALUE_PHRASES if phrase in lowered)
        duplicates = _duplicates(canonical_sentences)
        repeated_openings = _repeated_openings(canonical_sentences)
        issues: list[str] = []
        issues.extend(f"debug_label:{label}" for label in debug_labels)
        issues.extend(f"banned_meta:{phrase}" for phrase in banned)
        issues.extend(f"low_value_phrase:{phrase}" for phrase in low_value)
        issues.extend(f"duplicate_sentence:{sentence}" for sentence in duplicates)
        issues.extend(f"repeated_opening:{opening}" for opening in repeated_openings)
        return QualityReport(
            issues=tuple(issues),
            debug_labels=debug_labels,
            banned_meta_phrases=banned,
            low_value_phrases=low_value,
            duplicate_sentences=duplicates,
            repeated_openings=repeated_openings,
        )

    def repair_plan(
        self,
        plan: SemanticResponsePlan,
        *,
        remove_low_value_phrases: bool = False,
    ) -> tuple[SemanticResponsePlan, tuple[str, ...]]:
        repaired: list[SemanticSection] = []
        history: list[str] = []
        seen_sentences: set[str] = set()
        deterministic_fallback = bool(plan.metadata.get("fallback_reason")) or any(
            bool(section.metadata.get("fallback")) for section in plan.sections
        )
        repair_low_value = deterministic_fallback or remove_low_value_phrases
        for section in plan.sections:
            text = _DEBUG_PREFIX.sub("", section.text.strip())
            if text != section.text.strip():
                history.append(f"removed_debug_prefix:{section.section_id}")
            if repair_low_value:
                for phrase in _LOW_VALUE_PHRASES:
                    if phrase in text.casefold():
                        text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
                        text = " ".join(text.split()).strip(" ,;:-")
                        history.append(
                            f"removed_low_value_phrase:{section.section_id}:{phrase}"
                        )
            kept: list[str] = []
            for sentence in _sentences(text):
                normalized = _normalize(sentence)
                if not normalized:
                    continue
                if any(phrase in normalized for phrase in _BANNED_META_PHRASES):
                    history.append(f"removed_meta_sentence:{section.section_id}")
                    continue
                if normalized in seen_sentences:
                    history.append(f"removed_duplicate_sentence:{section.section_id}")
                    continue
                seen_sentences.add(normalized)
                kept.append(sentence.strip())
            repaired_text = " ".join(kept).strip()
            if repaired_text:
                repaired.append(replace(section, text=repaired_text))
            elif section.text.strip():
                history.append(f"removed_empty_section:{section.section_id}")
        return replace(plan, sections=tuple(repaired)), tuple(history)


def _sentences(text: str) -> tuple[str, ...]:
    stripped = str(text or "").strip()
    if not stripped:
        return ()
    return tuple(part.strip() for part in _SENTENCE_BOUNDARY.split(stripped) if part.strip())


def _normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _duplicates(sentences: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for sentence in sentences:
        normalized = _normalize(sentence)
        if normalized in seen and normalized not in duplicates:
            duplicates.append(normalized)
        seen.add(normalized)
    return tuple(duplicates)


def _repeated_openings(sentences: Iterable[str]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for sentence in sentences:
        words = _normalize(sentence).split()
        if len(words) < 3:
            continue
        opening = " ".join(words[:3])
        counts[opening] = counts.get(opening, 0) + 1
    return tuple(sorted(opening for opening, count in counts.items() if count >= 3))
