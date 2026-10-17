"""Deterministic in-world repairs for the generic dialogue fallback."""
from __future__ import annotations

import re
from typing import Any

_GENERIC_CLARIFICATION_LINE = "Ask that plainly again, and I will answer as best I can."
_CLIENT_CORRUPTION_MARKERS = {"object object", "undefined", "null"}


def repair_dialogue_fallback(
    *,
    topic: str,
    line: str,
    speaker: str,
    profile: dict[str, Any],
    player_input: str,
) -> tuple[str, str]:
    """Replace generic clarification only when the input can support a reply."""

    if line != _GENERIC_CLARIFICATION_LINE:
        return topic, line
    repaired = _generic_question_fallback(
        speaker=speaker,
        profile=profile,
        player_input=player_input,
    )
    if repaired[1]:
        return repaired
    diegetic = _diegetic_line(
        speaker=speaker,
        profile=profile,
        player_input=player_input,
    )
    return diegetic if diegetic[1] else (topic, line)


def _generic_question_fallback(
    *,
    speaker: str,
    profile: dict[str, Any],
    player_input: str,
) -> tuple[str, str]:
    text = player_input.casefold()
    if not ("?" in text or any(term in text for term in ("what", "why", "how", "where", "who", "tell me", "any "))):
        return "", ""

    role = _text(profile.get("role") or profile.get("occupation") or profile.get("title"))
    role_phrase = f" as {role}" if role else ""
    if any(term in text for term in ("trouble", "troubles", "problem", "problems", "concern", "concerns", "wrong", "worry", "worries")):
        return (
            "concern_inquiry",
            f"I can answer that{role_phrase}, but I will not turn guesses into facts. There have been enough concerns nearby that I would listen carefully, ask what you want to know, and separate rumor from what I have seen.",
        )
    if any(term in text for term in ("rumor", "rumour", "news", "gossip", "heard", "word")):
        return (
            "rumor_inquiry",
            f"I hear pieces of news{role_phrase}, but I trust only some of them. Ask about a person, place, or road, and I will tell you what sounds solid.",
        )
    if any(term in text for term in ("think", "thought", "opinion", "feel")):
        return (
            "opinion_question",
            f"My opinion{role_phrase} is worth only what I have lived and heard, but I can give it plainly if you name the matter.",
        )
    if any(term in text for term in ("where", "place", "road", "town", "tavern", "local")):
        return (
            "local_knowledge",
            f"I can tell you what I know of the local roads and people{role_phrase}, but I will keep it to what belongs in this place and this moment.",
        )
    speaker_name = _text(speaker).strip() or "I"
    if speaker_name != "I":
        return (
            "information_inquiry",
            f"{speaker_name} considers the question before answering from what they know, not from guesswork. Ask the part you care about most, and they will answer directly.",
        )
    return (
        "information_inquiry",
        "I can answer from what I know, but I will not invent certainty. Ask the part you care about most, and I will answer directly.",
    )


def _diegetic_line(
    *,
    speaker: str,
    profile: dict[str, Any],
    player_input: str,
) -> tuple[str, str]:
    text = player_input.casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not _looks_like_meaningful_text(normalized):
        return "", ""

    speaker_name = speaker.strip() or "NPC"
    role = _text(profile.get("role") or profile.get("occupation") or profile.get("title")).strip()
    role_phrase = f" as {role}" if role else ""
    if any(term in text for term in ("trouble", "troubles", "problem", "problems", "concern", "concerns", "wrong", "worry", "worries")):
        return (
            "concern_inquiry",
            f"I can answer that{role_phrase}, but I will not turn guesses into facts. There have been enough concerns nearby that I would listen carefully, ask what you want to know, and separate rumor from what I have seen.",
        )
    if any(term in text for term in ("rumor", "rumour", "news", "gossip", "heard", "word")):
        return (
            "rumor_inquiry",
            f"I hear pieces of news{role_phrase}, but I trust only some of them. Ask about a person, place, or road, and I will tell you what sounds solid.",
        )
    if any(term in text for term in ("think", "thought", "opinion", "feel")):
        return (
            "opinion_question",
            f"My opinion{role_phrase} is worth only what I have lived and heard, but I can give it plainly if you name the matter.",
        )
    if any(term in text for term in ("where", "place", "road", "town", "tavern", "local")):
        return (
            "local_knowledge",
            f"I can tell you what I know of the local roads and people{role_phrase}, but I will keep it to what belongs in this place and this moment.",
        )
    if any(term in text for term in ("jump", "climb", "lift", "run", "dance", "prove", "show me")):
        return (
            "capability_response",
            "I can answer in-world, but I will not pretend a feat happened just because it was requested. Say what outcome you want, and I will tell you what seems possible here.",
        )
    if any(term in text for term in ("sing", "nonsense", "odd", "strange")):
        return (
            "diegetic_reaction",
            f"{speaker_name} treats that as something happening in the room, not as a command failure. The moment is awkward, but it remains part of the world.",
        )
    if any(term in text for term in ("i ", "you ", "we ", "tell ", "ask ", "say ")):
        return (
            "diegetic_response",
            "I can answer that as something said or attempted here, but I will keep it grounded in what is known and possible rather than inventing a new fact.",
        )
    return (
        "information_inquiry",
        f"{speaker_name} considers the moment before answering from what they know, not from guesswork. Make the next part concrete, and they will answer directly.",
    )


def _looks_like_meaningful_text(text: str) -> bool:
    if not text or text in _CLIENT_CORRUPTION_MARKERS or len(text) < 3:
        return False
    alpha = sum(1 for char in text if char.isalpha())
    return alpha >= max(3, len(text) // 3)


def _text(value: Any) -> str:
    return str(value) if value is not None else ""
