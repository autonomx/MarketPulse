"""
Sentence splitting for Storyteller audio streaming.

Provides smart text splitting that:
- Splits on sentence boundaries
- Avoids breaking dialogue mid-sentence
"""

import re
from typing import List

# Abbreviations that end with a period but don't mark sentence endings
_ABBREVIATION_RE = re.compile(
    r'(?:'
    r'\b(?:Mr|Mrs|Ms|Dr|Prof|St|Jr|Sr|Gen|Gov|Sgt|Rev|Hon|Vol|Inc|Ltd|Corp|vs|etc)\.'
    r'|'
    r'\b(?:i\.e|e\.g)\.'
    r')$'
)

# Sentence boundary: punctuation (optionally followed by closing quote) then whitespace
_SENTENCE_BOUNDARY_RE = re.compile(r'([.!?]["\']?)\s+')


def split_sentences(text: str) -> List[str]:
    """Split text into sentences, preserving dialogue boundaries.

    Handles:
    - Standard sentence endings (. ! ?)
    - Dialogue with quotes ("Hello." she said.)
    - Abbreviations (Mr., Mrs., Dr., etc.) - don't split on these
    """
    if not text or not text.strip():
        return ['']

    text = text.strip()
    sentences: List[str] = []
    last = 0

    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        punct_pos = match.start()

        # Don't split on abbreviations
        prefix = text[:punct_pos + 1]
        if _ABBREVIATION_RE.search(prefix):
            continue

        # Don't split when punctuation is inside quotes
        if text[:punct_pos].count('"') % 2 == 1:
            continue

        end = match.start() + len(match.group(1))
        sentence = text[last:end].strip()
        if sentence:
            sentences.append(sentence)
        last = match.end()

    remaining = text[last:].strip()
    if remaining:
        sentences.append(remaining)

    return sentences if sentences else ['']
