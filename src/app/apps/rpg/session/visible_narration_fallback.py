from __future__ import annotations

from typing import Any, Iterable

# RPG session runtime responsibility module.
# Phase 8.29: ensure player turns always carry a visible fallback narration
# so the UI never gets stuck on a queued placeholder when a deferred job is
# missing, missed by SSE, or still pending.

_PHASE8_NARRATION_FALLBACK_SOURCE = "deterministic_phase8_queued_narration_visible_fallback_gate"
_PHASE8_EMPTY_VISIBLE_TEXT = {
    "",
    "[]",
    "{}",
    "[ ]",
    "{ }",
    "null",
    "none",
    "false",
    "true",
}


def _phase8_safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _phase8_safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _phase8_clean_visible_text(value: str) -> str:
    text = value.strip()
    if text.casefold() in _PHASE8_EMPTY_VISIBLE_TEXT:
        return ""
    return text


def _phase8_safe_str(value: Any) -> str:
    """Return only user-visible scalar text.

    This fallback path is rendered directly in the RPG transcript when queued
    narration is still pending. Do not stringify containers here: an empty list
    in a payload field becomes the literal text ``[]`` in the UI, and populated
    dict/list payloads tend to be machine contracts rather than prose.
    Structured dict/list fields are handled by dedicated helpers instead.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return _phase8_clean_visible_text(value)
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return ""
    return _phase8_clean_visible_text(str(value))


def _phase8_first_text(values: Iterable[Any]) -> str:
    for value in values:
        text = _phase8_safe_str(value).strip()
        if text:
            return text
    return ""


def _phase8_payload_candidates(authoritative_result: dict[str, Any]) -> list[dict[str, Any]]:
    authoritative_result = _phase8_safe_dict(authoritative_result)
    candidates: list[dict[str, Any]] = []
    for key in ("result", "authoritative", "payload"):
        candidate = _phase8_safe_dict(authoritative_result.get(key))
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    # Some call sites pass the authoritative fields directly at top level.
    top_level_keys = {
        "narration",
        "summary",
        "presentation",
        "structured_narration",
        "raw_llm_narrative",
        "deterministic_fallback_narration",
    }
    if any(key in authoritative_result for key in top_level_keys):
        candidates.insert(0, authoritative_result)

    return candidates


def _phase8_dialogue_line(npc_payload: dict[str, Any]) -> str:
    npc_payload = _phase8_safe_dict(npc_payload)
    speaker = _phase8_first_text((
        npc_payload.get("speaker"),
        npc_payload.get("name"),
        npc_payload.get("speaker_id"),
    ))
    line = _phase8_first_text((
        npc_payload.get("line"),
        npc_payload.get("text"),
        npc_payload.get("dialogue"),
    ))
    if speaker and line:
        return f'{speaker}: "{line}"'
    return line


def _phase8_structured_narration_text(payload: dict[str, Any]) -> str:
    payload = _phase8_safe_dict(payload)
    structured = _phase8_safe_dict(payload.get("structured_narration"))
    narration_json = _phase8_safe_dict(payload.get("narration_json"))
    if not structured and narration_json:
        structured = narration_json

    narration = _phase8_first_text((
        structured.get("narration"),
        payload.get("narration"),
        payload.get("final_narration"),
    ))
    action = _phase8_first_text((
        structured.get("action"),
        payload.get("action"),
    ))
    npc_line = _phase8_dialogue_line(
        _phase8_safe_dict(structured.get("npc"))
        or _phase8_safe_dict(payload.get("npc"))
    )
    reward = _phase8_first_text((structured.get("reward"), payload.get("reward")))

    parts: list[str] = []
    if narration:
        parts.append("Scene\n" + narration)
    if action:
        parts.append("Action\n" + action)
    if npc_line:
        parts.append(npc_line)
    if reward:
        parts.append("Rewards\n" + reward)
    return "\n".join(parts).strip()


def _phase8_presentation_text(payload: dict[str, Any]) -> str:
    presentation = _phase8_safe_dict(payload.get("presentation"))
    if not presentation:
        turn_contract = _phase8_safe_dict(payload.get("turn_contract"))
        presentation = _phase8_safe_dict(turn_contract.get("presentation"))

    direct = _phase8_first_text((
        presentation.get("narration"),
        presentation.get("response"),
        presentation.get("message"),
        presentation.get("text"),
        presentation.get("summary"),
        presentation.get("action_summary"),
    ))
    if direct:
        return direct

    speaker_turns = _phase8_safe_list(presentation.get("speaker_turns") or payload.get("speaker_turns"))
    lines: list[str] = []
    for raw in speaker_turns:
        raw = _phase8_safe_dict(raw)
        line = _phase8_dialogue_line(raw)
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _phase8_visible_fallback_text(authoritative_result: dict[str, Any]) -> str:
    for payload in _phase8_payload_candidates(authoritative_result):
        structured_text = _phase8_structured_narration_text(payload)
        if structured_text:
            return structured_text

        direct_text = _phase8_first_text((
            payload.get("deterministic_fallback_narration"),
            payload.get("fallback_narration"),
            payload.get("narration"),
            payload.get("final_narration"),
            payload.get("raw_llm_narrative"),
            payload.get("summary"),
            payload.get("result_summary"),
            payload.get("outcome"),
        ))
        if direct_text:
            return direct_text

        presentation_text = _phase8_presentation_text(payload)
        if presentation_text:
            return presentation_text

    player_input = _phase8_first_text((
        authoritative_result.get("player_input"),
        _phase8_safe_dict(authoritative_result.get("result")).get("player_input"),
        _phase8_safe_dict(authoritative_result.get("authoritative")).get("player_input"),
        _phase8_safe_dict(authoritative_result.get("payload")).get("player_input"),
    ))
    if player_input:
        return f"You continue: {player_input}"
    return "The turn is resolved and recorded."


def _phase8_patch_visible_fallback(authoritative_result: dict[str, Any]) -> dict[str, Any]:
    authoritative_result = _phase8_safe_dict(authoritative_result)
    if not authoritative_result:
        return authoritative_result

    fallback_text = _phase8_visible_fallback_text(authoritative_result).strip()
    if not fallback_text:
        return authoritative_result

    authoritative_result = dict(authoritative_result)
    # Assign instead of setdefault: existing values may be [], {}, or another
    # non-visible container that would otherwise render literally in the UI.
    authoritative_result["deterministic_fallback_narration"] = fallback_text
    authoritative_result.setdefault("fallback_narration_source", _PHASE8_NARRATION_FALLBACK_SOURCE)

    for key in ("result", "authoritative", "payload"):
        payload = _phase8_safe_dict(authoritative_result.get(key))
        if not payload:
            continue
        patched = dict(payload)
        patched["deterministic_fallback_narration"] = fallback_text
        patched.setdefault("fallback_narration_source", _PHASE8_NARRATION_FALLBACK_SOURCE)
        authoritative_result[key] = patched

    return authoritative_result


__all__ = ['_phase8_patch_visible_fallback']
