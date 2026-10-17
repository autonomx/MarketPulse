"""Deterministic, lossless structural spans; unfamiliar punctuation stays source text."""
from __future__ import annotations

import re
from typing import Protocol

from .hashing import text_hash
from .models import SourceSpan


DETECTOR_VERSION = "audiobook-spans-v9"
_OPEN_TO_CLOSE = {'"': '"', "'": "'", '“': '”', '«': '»', '「': '」', '『': '』', '‘': '’'}
STYLE_RULES: dict[str, tuple[str, str]] = {
    "low_double_quotes": ("„", "“"),
    "low_single_quotes": ("‚", "‘"),
    "single_angle_quotes": ("‹", "›"),
    "horizontal_dash": ("―", ""),
    "hyphen_dash": ("-", ""),
    "speaker_labels": ("", ""),
}
_SPEECH_TAG_VERBS = (
    "said", "asked", "replied", "answered", "shouted", "yelled", "whispered",
    "muttered", "murmured", "cried", "called", "snapped", "growled", "hissed",
    "sighed", "added", "continued", "insisted", "warned", "ordered", "demanded",
    "exclaimed", "remarked", "responded",
)
_DASH_ATTRIBUTION = re.compile(
    r",\s*(?:(?:[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})|he|she|they|"
    r"the\s+[\w'-]+)\s+(?:"
    + "|".join(re.escape(item) for item in _SPEECH_TAG_VERBS)
    + r")\b",
    re.IGNORECASE,
)
_INLINE_ATTRIBUTION_VERBS = tuple(
    item for item in _SPEECH_TAG_VERBS if item != "called"
)
_INLINE_ATTRIBUTION_VERB_RE = "|".join(
    re.escape(item) for item in _INLINE_ATTRIBUTION_VERBS
)
_BEFORE_QUOTE_ATTRIBUTION = re.compile(
    r"(?:(?:[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})|he|she|they|"
    r"the\s+[\w'-]+)\s+(?:"
    + _INLINE_ATTRIBUTION_VERB_RE
    + r")\b[^.!?\n]{0,80}[,;:\-—]?\s*$",
    re.IGNORECASE,
)
_AFTER_QUOTE_ATTRIBUTION = re.compile(
    r"^\s*[,;:\-—]?\s*(?:(?:[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})|"
    r"he|she|they|the\s+[\w'-]+)\s+(?:"
    + _INLINE_ATTRIBUTION_VERB_RE
    + r")\b",
    re.IGNORECASE,
)
_WRAPPED_QUOTE_MAX_CHARS = 2000
_WRAPPED_QUOTE_MAX_LINES = 8
_SPEAKER_LABEL_LINE = re.compile(
    r"^[ \t]*(?P<label>[A-Z][\w'’.-]*(?:[ \t]+[A-Z][\w'’.-]*){0,2})"
    r":[ \t]*\S[^\n]*$", re.MULTILINE,
)


def speaker_label_prefix_end(text: str) -> int | None:
    """Return the end of a leading speaker label using the detector's grammar."""
    match = _SPEAKER_LABEL_LINE.match(text)
    if match is None:
        return None
    end = match.end("label") + 1  # Include the colon.
    while end < len(text) and text[end] in " \t":
        end += 1
    return end


class SpanDetector(Protocol):
    version: str

    def detect(self, chapter_id: str, text: str) -> tuple[SourceSpan, ...]: ...


class UnicodeDialogueDetector:
    version = DETECTOR_VERSION

    def __init__(self, *, styles: tuple[str, ...] = ()) -> None:
        unknown = set(styles) - STYLE_RULES.keys()
        if unknown:
            raise ValueError(f"unsupported dialogue styles: {sorted(unknown)}")
        self.styles = tuple(sorted(set(styles)))
        self._open_to_close = dict(_OPEN_TO_CLOSE)
        self._dash_prefixes = ["—", "–"]
        for style in self.styles:
            if style == "speaker_labels":
                continue
            opening, closing = STYLE_RULES[style]
            if closing:
                self._open_to_close[opening] = closing
            elif opening == "-":
                self._dash_prefixes.extend(["- ", "-\t"])
            else:
                self._dash_prefixes.append(opening)

    def detect(self, chapter_id: str, text: str) -> tuple[SourceSpan, ...]:
        if not text:
            return ()
        boundaries: list[tuple[int, int, str]] = []
        labelled_lines = self._labelled_dialogue_lines(text)
        if "speaker_labels" in self.styles:
            labelled_lines.update(match.start() for match in _SPEAKER_LABEL_LINE.finditer(text))
        cursor = 0
        while cursor < len(text):
            line_end = self._line_end(text, cursor)
            line = text[cursor:line_end]
            if cursor in labelled_lines:
                ranges = [(cursor, line_end, "dialogue")]
            elif line.lstrip().startswith(tuple(self._dash_prefixes)):
                ranges = self._dash_line_ranges(text, cursor, line_end)
            else:
                ranges = self._line_ranges(text, cursor, line_end)
            boundaries.extend(ranges)
            cursor = max(line_end, max((item[1] for item in ranges), default=line_end))

        merged: list[tuple[int, int, str]] = []
        for start, end, kind in boundaries:
            # Narration may be coalesced for compactness, but dialogue boundaries
            # are semantic attribution boundaries. Consecutive dialogue lines can
            # belong to different speakers and must remain independently assignable.
            if (
                kind == "narration"
                and merged
                and merged[-1][1] == start
                and merged[-1][2] == kind
            ):
                previous = merged[-1]
                merged[-1] = (previous[0], end, kind)
            else:
                merged.append((start, end, kind))
        return tuple(
            SourceSpan(
                id=f"ab:sp:{text_hash(f'{chapter_id}:{ordinal}:{start}:{end}:{text[start:end]}')}",
                chapter_id=chapter_id,
                ordinal=ordinal,
                start_offset=start,
                end_offset=end,
                source_text=text[start:end],
                source_hash=text_hash(text[start:end]),
                structural_kind=kind,
                detector_version=self.version,
            )
            for ordinal, (start, end, kind) in enumerate(merged)
        )

    @staticmethod
    def _labelled_dialogue_lines(text: str) -> set[int]:
        """Recognize exchanges, rather than treating every colon heading as speech.

        Require at least three nearby turns, multiple labels, and a returning
        speaker. Blank lines may separate turns; prose ends the exchange.
        """
        result: set[int] = set()
        run: list[re.Match[str]] = []

        def finish_run() -> None:
            labels = {match.group("label") for match in run}
            if len(run) >= 3 and 1 < len(labels) < len(run):
                result.update(match.start() for match in run)

        for match in _SPEAKER_LABEL_LINE.finditer(text):
            if run and text[run[-1].end():match.start()].strip():
                finish_run()
                run = []
            run.append(match)
        finish_run()
        return result

    @staticmethod
    def _line_end(text: str, start: int) -> int:
        newline = text.find("\n", start)
        return len(text) if newline < 0 else newline + 1

    @staticmethod
    def _is_internal_apostrophe(text: str, index: int) -> bool:
        return (
            0 < index < len(text) - 1
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )

    @classmethod
    def _find_close(cls, text: str, closing: str, start: int, end: int) -> int:
        finish = text.find(closing, start, end)
        while finish >= 0 and closing in {"'", '’'} and cls._is_internal_apostrophe(text, finish):
            finish = text.find(closing, finish + 1, end)
        return finish

    @staticmethod
    def _dash_line_ranges(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
        """Keep em-dash dialogue lossless while separating obvious speech tags."""
        line = text[start:end]
        match = _DASH_ATTRIBUTION.search(line)
        if match is None:
            return [(start, end, "dialogue")]
        split = start + match.start() + 1
        if split <= start or split >= end:
            return [(start, end, "dialogue")]
        return [(start, split, "dialogue"), (split, end, "narration")]

    @staticmethod
    def _is_inline_non_dialogue_quote(
        text: str, *, opening_index: int, closing_index: int,
        line_start: int, line_end: int,
    ) -> bool:
        """Keep quoted terms/titles inside prose out of the dialogue lane."""
        prefix = text[line_start:opening_index].rstrip()
        if not prefix:
            return False
        if prefix.endswith((".", "!", "?", ":", "—", "–", ",")):
            return False
        nearby_prefix = prefix[-180:]
        suffix = text[closing_index + 1:line_end]
        if _BEFORE_QUOTE_ATTRIBUTION.search(nearby_prefix):
            return False
        if _AFTER_QUOTE_ATTRIBUTION.search(suffix[:180]):
            return False
        return True

    @classmethod
    def _wrapped_close(
        cls, text: str, *, opening_index: int, current_line_end: int,
        opening: str, closing: str,
    ) -> int:
        """Find a close quote across PDF hard wraps without swallowing paragraphs."""
        if current_line_end >= len(text):
            return -1
        line_start = text.rfind("\n", 0, opening_index) + 1
        first = line_start
        while first < current_line_end and text[first] in {" ", "\t"}:
            first += 1

        next_end = cls._line_end(text, current_line_end)
        next_first = current_line_end
        while next_first < next_end and text[next_first] in {" ", "\t"}:
            next_first += 1
        if (
            opening_index == first
            and next_first < next_end
            and text[next_first] == opening
        ):
            return -1

        limit = min(len(text), opening_index + _WRAPPED_QUOTE_MAX_CHARS)
        finish = cls._find_close(text, closing, current_line_end, limit)
        if finish < 0:
            return -1
        between = text[current_line_end:finish + 1]
        if re.search(r"\n[ \t]*\n", between):
            return -1
        if text.count("\n", current_line_end, finish + 1) > _WRAPPED_QUOTE_MAX_LINES:
            return -1
        return finish

    def _line_ranges(self, text: str, start: int, end: int) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        cursor = start
        index = start
        scan_end = end

        while index < scan_end:
            char = text[index]
            if char not in self._open_to_close:
                index += 1
                continue
            if char in {"'", '‘'} and index > start and text[index - 1].isalnum():
                index += 1
                continue

            closing = self._open_to_close[char]
            finish = self._find_close(text, closing, index + 1, scan_end)
            if finish < 0:
                finish = self._wrapped_close(
                    text,
                    opening_index=index,
                    current_line_end=scan_end,
                    opening=char,
                    closing=closing,
                )
                if finish >= 0:
                    scan_end = self._line_end(text, finish)
                else:
                    newline = text.rfind("\n", start, index + 1)
                    line_start = start if newline < 0 else newline + 1
                    first = line_start
                    while first < scan_end and text[first] in {" ", "\t"}:
                        first += 1
                    if index == first:
                        ranges.append((cursor, scan_end, "dialogue"))
                        cursor = scan_end
                        index = scan_end
                        break
                    index += 1
                    continue

            newline = text.rfind("\n", start, index + 1)
            line_start = start if newline < 0 else newline + 1
            if self._is_inline_non_dialogue_quote(
                text,
                opening_index=index,
                closing_index=finish,
                line_start=line_start,
                line_end=scan_end,
            ):
                index = finish + 1
                continue

            if index > cursor:
                ranges.append((cursor, index, "narration"))
            ranges.append((index, finish + 1, "dialogue"))
            cursor = finish + 1
            index = cursor

        if cursor < scan_end:
            ranges.append((cursor, scan_end, "narration"))
        return ranges
