"""Independent audit for speech cues left inside narration spans.

The span detector owns boundaries. This audit cannot change canonical text or
claim that a passage is dialogue; it only prevents suspicious text from being
silently approved as narration.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from .models import SourceSpan


AUDIT_VERSION = "audiobook-dialogue-coverage-v1"
_QUOTE_MARKS = frozenset('"\'«»‹›「」『』„“‚‘’‟')
_DASH_LINE = re.compile(r"(?m)^[ \t]*[-–—―][ \t]+(?=\S)")
_SPEECH_VERBS = (
    "said", "asked", "replied", "answered", "shouted", "yelled", "whispered",
    "muttered", "murmured", "cried", "called", "snapped", "growled", "hissed",
    "exclaimed", "responded",
)
_SPEECH_VERB_RE = "|".join(re.escape(item) for item in _SPEECH_VERBS)
_SPEECH_TAG = re.compile(r"\b(?:" + _SPEECH_VERB_RE + r")\b", re.IGNORECASE)
_SPEECH_ATTRIBUTION = re.compile(
    r"(?:"
    r"\b(?:[Hh]e|[Ss]he|[Tt]hey)\s+(?i:" + _SPEECH_VERB_RE + r")\b"
    r"|\b[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2}\s+(?i:"
    + _SPEECH_VERB_RE + r")\b"
    r"|\b(?i:" + _SPEECH_VERB_RE + r")\s+(?:"
    r"[Hh]e|[Ss]he|[Tt]hey|[A-Z][a-z][\w'.-]*(?:\s+[A-Z][a-z][\w'.-]*){0,2})\b"
    r")"
)


def _looks_like_closing_quote(text: str, index: int) -> bool:
    """Avoid treating a closing quotation mark as a new speech opening cue."""
    previous = index - 1
    while previous >= 0 and text[previous].isspace():
        previous -= 1
    following = index + 1
    while following < len(text) and text[following].isspace():
        following += 1
    if previous < 0:
        return False
    previous_char = text[previous]
    next_char = text[following] if following < len(text) else ""
    # Closing quotes normally follow content/punctuation and are followed by
    # whitespace, sentence punctuation, or the end of the span. Opening quotes
    # normally have a non-space content character immediately after them.
    return (
        (not next_char or next_char in ".,;:!?)]}\n" or text[index + 1:index + 2].isspace())
        and (previous_char.isalnum() or previous_char in ".,;:!?)]")
    )


def _quote_offset(text: str) -> int | None:
    for index, char in enumerate(text):
        if char not in _QUOTE_MARKS and unicodedata.category(char) not in {"Pi", "Pf"}:
            continue
        if char in {"'", "’"} and index > 0 and text[index - 1].isalnum():
            continue
        if _looks_like_closing_quote(text, index):
            continue

        line_start = text.rfind("\n", 0, index) + 1
        prefix = text[line_start:index]
        nearby_prefix = prefix[-120:]
        if not prefix.strip():
            return index
        stripped = prefix.rstrip()
        if (
            _SPEECH_TAG.search(nearby_prefix)
            and stripped.endswith((",", ":", "—", "–"))
        ):
            return index
        if stripped.endswith((".", "!", "?", ":", "—", "–")):
            return index
    return None


def audit_dialogue_coverage(spans: Sequence[SourceSpan]) -> dict[str, dict[str, Any]]:
    """Return review evidence for narration that still contains speech cues.

    Quote punctuation and line-leading dialogue dashes are scanned separately
    from the detector. Speech tags are a weaker cue, so they trigger review only
    in chapters with no detected dialogue and substantial text.
    """
    findings: dict[str, dict[str, Any]] = {}
    by_chapter: dict[str, list[SourceSpan]] = {}
    for span in spans:
        by_chapter.setdefault(span.chapter_id, []).append(span)

    for chapter_spans in by_chapter.values():
        has_dialogue = any(
            span.structural_kind == "dialogue" and span.source_text.strip()
            for span in chapter_spans
        )
        chapter_chars = sum(len(span.source_text) for span in chapter_spans)
        for span in chapter_spans:
            if span.structural_kind != "narration":
                continue
            quote = _quote_offset(span.source_text)
            dash = _DASH_LINE.search(span.source_text)
            tag = (
                _SPEECH_ATTRIBUTION.search(span.source_text)
                if not has_dialogue and chapter_chars >= 2_000 else None
            )
            if quote is not None:
                signal, index = "quote_in_narration", quote
            elif dash is not None:
                signal, index = "dialogue_dash_in_narration", dash.start()
            elif tag is not None:
                signal, index = "speech_tag_without_dialogue", tag.start()
            else:
                continue
            findings[span.id] = {
                "audit_version": AUDIT_VERSION,
                "signal": signal,
                "source_offset": span.start_offset + index,
                "excerpt": span.source_text[max(0, index - 40):index + 120],
            }
    return findings
