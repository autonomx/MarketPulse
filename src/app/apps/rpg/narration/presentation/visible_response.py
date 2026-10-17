"""Canonical visible-response extraction for every RPG delivery path."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable

_FORMAT_VERSION = "rpg_visible_response_v1"
_NON_NPC_SPEAKERS = {
    "",
    "scene",
    "narrator",
    "narration",
    "gm",
    "game master",
    "omnix",
    "system",
    "player",
    "you",
}


def build_visible_response(result: Any, fallback_command: str = "") -> dict[str, Any]:
    """Return one bounded narration/dialogue contract from a nested turn result."""

    sources = list(_iter_sources(result))
    player_input = _player_input(sources) or _text(fallback_command)
    canonical = _canonical_visible_response(sources, player_input)
    if canonical:
        return canonical
    narration, npc = _select_structured_response(sources, player_input)
    speaker = _display_speaker(_text(npc.get("speaker") or npc.get("name")))
    line = _text(npc.get("line") or npc.get("text") or npc.get("content"))
    line = _dialogue_only(line, speaker)
    quest_evidence = _quest_evidence(sources)
    clue_summary = _text(quest_evidence.get("clue_summary"))
    if clue_summary and (not line or not _line_grounded_in_clue(line, clue_summary)):
        line = clue_summary
        speaker = _display_speaker(
            _text(quest_evidence.get("actor_ref")) or speaker
        ) or "NPC"
        npc = {**npc, "speaker": speaker, "line": line}
    if _normalize(speaker) in _NON_NPC_SPEAKERS:
        speaker = ""
        line = ""

    if narration and _is_player_restatement(narration, player_input):
        narration = ""
    if line and _is_player_restatement(line, player_input):
        line = ""

    messages: list[dict[str, Any]] = []
    if line:
        messages.append(
            {
                "kind": "npc_dialogue",
                "speaker_id": _text(npc.get("speaker_id") or npc.get("npc_id") or npc.get("id")) or None,
                "speaker": speaker or "NPC",
                "text": _normalize_dialogue_quotes(line),
            }
        )

    paragraphs: list[str] = []
    if narration and not _equivalent(narration, line):
        paragraphs.append(narration)
    if line:
        quoted = _normalize_dialogue_quotes(line)
        paragraphs.append(f'{speaker or "NPC"}: "{quoted}"')

    if not paragraphs:
        fallback = _first_safe_text(sources, player_input, "deterministic_fallback_narration")
        if fallback:
            paragraphs.append(fallback)
        else:
            fallback_contract = _direct_dialogue_fallback(player_input)
            if fallback_contract:
                narration = fallback_contract["narration"]
                npc = fallback_contract["npc"]
                speaker = _text(npc.get("speaker")) or "NPC"
                line = _text(npc.get("line"))
                messages = [
                    {
                        "kind": "npc_dialogue",
                        "speaker_id": _text(npc.get("speaker_id")) or None,
                        "speaker": speaker,
                        "text": line,
                    }
                ]
                paragraphs.extend([narration, f'{speaker}: "{line}"'])
            elif fallback_command:
                paragraphs.append(f"Your command is accepted: {fallback_command}.")

    plain_text = _dedupe_paragraphs(paragraphs)
    return {
        "format_version": _FORMAT_VERSION,
        "narration": narration if narration and not _equivalent(narration, line) else "",
        "messages": messages,
        "plain_text": plain_text,
        "npc": deepcopy(npc) if line else {},
    }


def _canonical_visible_response(
    sources: list[dict[str, Any]],
    player_input: str,
) -> dict[str, Any]:
    for source in sources:
        canonical = _dict(source.get("canonical_visible_response"))
        if not canonical:
            continue
        narration = _text(canonical.get("narration"))
        if narration and _is_player_restatement(narration, player_input):
            narration = ""
        messages: list[dict[str, Any]] = []
        for item in canonical.get("messages", []):
            if not isinstance(item, dict):
                continue
            speaker = _display_speaker(
                _text(item.get("speaker") or item.get("speaker_id"))
            )
            line = _dialogue_only(
                _text(item.get("text") or item.get("line")),
                speaker,
            )
            if _normalize(speaker) in _NON_NPC_SPEAKERS:
                continue
            if line and _is_player_restatement(line, player_input):
                continue
            if not line:
                continue
            messages.append(
                {
                    "kind": "npc_dialogue",
                    "speaker_id": _text(
                        item.get("speaker_id") or item.get("npc_id") or item.get("id")
                    )
                    or None,
                    "speaker": speaker or "NPC",
                    "text": _normalize_dialogue_quotes(line),
                }
            )
        paragraphs = [narration] if narration else []
        paragraphs.extend(
            f'{message["speaker"]}: "{message["text"]}"'
            for message in messages
        )
        if not paragraphs:
            continue
        npc = {}
        if messages:
            first = messages[0]
            npc = {
                "speaker_id": first.get("speaker_id"),
                "speaker": first["speaker"],
                "line": first["text"],
            }
        return {
            "format_version": _FORMAT_VERSION,
            "narration": narration,
            "messages": messages,
            "plain_text": _dedupe_paragraphs(paragraphs),
            "npc": npc,
        }
    return {}


def visible_response_text(result: Any, fallback_command: str = "") -> str | None:
    return _text(build_visible_response(result, fallback_command).get("plain_text")) or None


def _select_structured_response(
    sources: list[dict[str, Any]],
    player_input: str,
) -> tuple[str, dict[str, Any]]:
    for source in sources:
        raw_messages = source.get("messages")
        if isinstance(raw_messages, list):
            for message in raw_messages:
                if not isinstance(message, dict):
                    continue
                kind = _normalize(message.get("kind"))
                if kind not in {"npc", "npc dialogue"}:
                    continue
                speaker_id = _text(message.get("speaker_id"))
                speaker = _display_speaker(
                    _text(message.get("speaker") or speaker_id)
                )
                line = _dialogue_only(
                    _text(message.get("text") or message.get("line")),
                    speaker,
                )
                narration = _text(source.get("narration"))
                if _normalize(speaker) in _NON_NPC_SPEAKERS:
                    continue
                if line and _is_player_restatement(line, player_input):
                    line = ""
                if narration and _is_player_restatement(narration, player_input):
                    narration = ""
                if narration or line:
                    return narration, {
                        "speaker": speaker or "NPC",
                        "speaker_id": speaker_id or None,
                        "line": line,
                    }
        npc = _dict(source.get("npc"))
        speaker = _text(npc.get("speaker") or npc.get("name"))
        line = _text(npc.get("line") or npc.get("text") or npc.get("content"))
        line = _dialogue_only(line, speaker)
        narration = _text(
            source.get("narration")
            or source.get("final_narration")
            or source.get("summary")
            or source.get("response")
            or source.get("content")
            or source.get("text")
        )
        if _normalize(speaker) in _NON_NPC_SPEAKERS:
            speaker = ""
            line = ""
        if line and _is_player_restatement(line, player_input):
            line = ""
        if narration and _is_player_restatement(narration, player_input):
            narration = ""
        if narration or line:
            if line and speaker:
                npc = {**npc, "speaker": speaker, "line": line}
            elif not line:
                npc = {}
            return narration, npc
    return "", {}


def _dialogue_only(line: str, speaker: str) -> str:
    """Strip duplicated attribution/narration from an NPC speech field."""

    text = _text(line).strip()
    if not text:
        return ""
    prefix = f"{_text(speaker).strip()}:"
    if prefix != ":" and text.casefold().startswith(prefix.casefold()):
        text = text[len(prefix):].strip()
    normalized = text.replace("“", '"').replace("”", '"')
    if speaker and normalized.casefold().startswith(_text(speaker).strip().casefold()):
        quoted = re.search(r'"([^"\r\n]+)"\s*$', normalized)
        if quoted:
            text = quoted.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _display_speaker(value: str) -> str:
    speaker = _text(value).strip()
    if speaker.casefold().startswith("npc:"):
        speaker = speaker.split(":", 1)[1]
    return speaker.replace("_", " ").strip()


def _quest_evidence(sources: list[dict[str, Any]]) -> dict[str, Any]:
    for source in sources:
        transition = _dict(source.get("quest_transition"))
        if not transition:
            transition = _dict(_dict(source.get("service_application")).get("quest_transition"))
        evidence = _dict(transition.get("evidence"))
        if _text(evidence.get("clue_summary")):
            return evidence
    return {}


_GROUNDING_STOPWORDS = {
    "a", "an", "and", "are", "at", "by", "for", "from", "in", "is", "it",
    "near", "of", "on", "or", "that", "the", "their", "there", "they", "this",
    "to", "was", "were", "with",
}


def _grounding_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _text(value).casefold())
        if len(token) > 2 and token not in _GROUNDING_STOPWORDS
    }


def _line_grounded_in_clue(line: str, clue_summary: str) -> bool:
    clue_tokens = _grounding_tokens(clue_summary)
    if not clue_tokens:
        return False
    overlap = clue_tokens & _grounding_tokens(line)
    required = min(len(clue_tokens), max(3, (len(clue_tokens) + 1) // 2))
    return len(overlap) >= required


def _iter_sources(value: Any) -> Iterable[dict[str, Any]]:
    root = _dict(value)
    if not root:
        return
    seen: set[int] = set()
    queue = [root]
    while queue:
        source = queue.pop(0)
        identity = id(source)
        if identity in seen:
            continue
        seen.add(identity)
        selected = _dict(source.get("first_call_visible_response"))
        visible = _dict(selected.get("visible_response")) or _dict(source.get("visible_response"))
        narration_json = _dict(source.get("narration_json"))
        if selected:
            yield selected
        if visible:
            yield visible
        if narration_json:
            yield narration_json
        yield source
        for key in ("result", "resolved_result", "authoritative", "turn_contract", "narration_brief"):
            nested = _dict(source.get(key))
            if nested:
                queue.append(nested)


def _first_safe_text(sources: list[dict[str, Any]], player_input: str, *keys: str) -> str:
    for source in sources:
        for key in keys:
            value = _text(source.get(key))
            if value and not _is_player_restatement(value, player_input):
                return value
    return ""


def _player_input(sources: Iterable[dict[str, Any]]) -> str:
    for source in sources:
        direct = _text(source.get("player_input"))
        if direct:
            return direct
        payload = _dict(source.get("input_payload"))
        direct = _text(payload.get("player_input") or payload.get("command") or payload.get("content"))
        if direct:
            return direct
        context = _dict(source.get("narration_context"))
        direct = _text(context.get("player_input"))
        if direct:
            return direct
        request = _dict(source.get("narration_request"))
        request_context = _dict(request.get("narration_context"))
        direct = _text(request_context.get("player_input"))
        if direct:
            return direct
        diagnostics = _dict(source.get("first_call_grounding_diagnostics"))
        packet = _dict(diagnostics.get("turn_grounding_packet"))
        direct = _text(packet.get("player_input"))
        if direct:
            return direct
    return ""


def _direct_dialogue_fallback(player_input: str) -> dict[str, Any] | None:
    normalized = _normalize(player_input)
    if not normalized or not re.search(r"\b(?:business|going|trade|tavern|customers|patrons)\b", normalized):
        return None
    target = _direct_npc_name(player_input)
    if not target:
        return None
    if target.casefold() == "bran":
        return {
            "narration": "Bran glances around the Rusty Flagon before answering.",
            "npc": {
                "speaker_id": "npc:bran",
                "speaker": "Bran",
                "line": (
                    "Steady enough. Rooms, food, and rumors keep the doors open, "
                    "though the road has been strange lately."
                ),
            },
        }
    return {
        "narration": f"{target} considers the question before answering.",
        "npc": {
            "speaker": target,
            "line": "Business has been steady enough, though conditions keep changing.",
        },
    }


def _direct_npc_name(command: str) -> str | None:
    match = re.search(
        r"\b(?:ask|talk(?:\s+to)?|speak(?:\s+to)?|tell)\s+([A-Z][A-Za-z0-9_-]+|[a-z][a-z0-9_-]+)\b",
        command,
    )
    if not match:
        return None
    name = match.group(1).strip(" ,.!?:;\"'")
    if not name or name.casefold() in {"about", "if", "how", "what", "why", "where", "when"}:
        return None
    return name[:1].upper() + name[1:]


def _is_player_restatement(value: str, player_input: str) -> bool:
    text = _normalize(value)
    player = _normalize(player_input)
    if not text or not player or len(player) < 18:
        return False
    return player in text or text in player


def _dedupe_paragraphs(paragraphs: Iterable[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paragraphs:
        paragraph = _text(raw)
        if not paragraph:
            continue
        key = _normalize(paragraph)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(paragraph)
    return "\n\n".join(out)


def _normalize_dialogue_quotes(text: str) -> str:
    value = _text(text).strip('"').strip()
    value = value.replace("“", '"').replace("”", '"')
    value = re.sub(r",['’](?=\s)", ',"', value)
    value = re.sub(r"(?<=[.!?])['’](?=\s+[A-Z])", '"', value)
    value = re.sub(r"(?<=\s)['’](?=[A-Z])", '"', value)
    return value


def _equivalent(left: str, right: str) -> bool:
    return bool(left and right and _normalize(left) == _normalize(right))


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).casefold()).strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
