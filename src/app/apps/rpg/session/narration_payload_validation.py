from __future__ import annotations

from typing import Any, Iterable

# RPG session runtime responsibility module.
# Phase 8.34: a deterministic authoritative/fallback echo must not count as a
# completed visible narration.  Player-facing dialogue should use the LLM
# narrator's structured categorization when it is available; keyword-driven
# deterministic results remain simulation facts, not the final transcript voice.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_str as _safe_str,
)


_PHASE8_PART34_SOURCE = "phase8_llm_narration_authority_over_deterministic_fallback"
_LLM_NARRATION_SOURCES = {
    "provider_sync_visible_turn_narration",
    "phase8_sync_narration_stream_payload_mirror",
    "phase8_llm_narration_authority_over_deterministic_fallback",
}
_DETERMINISTIC_FALLBACK_SOURCES = {
    "deterministic_phase8_queued_narration_visible_fallback_gate",
    "phase8_part30_deterministic_dialogue_fallback",
    "deterministic",
    "deterministic_fallback",
}


def _phase8_part34_iter_payload_dicts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    payload = _safe_dict(payload)
    if payload:
        yield payload
    for key in ("result", "authoritative", "payload"):
        nested = _safe_dict(payload.get(key))
        if nested:
            yield nested


def _phase8_part34_clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in {"", "[]", "{}", "null", "none"} else text


def _phase8_part34_has_structured_llm_payload(source: dict[str, Any]) -> bool:
    source = _safe_dict(source)
    narration_json = _safe_dict(source.get("narration_json"))
    raw = source.get("raw_llm_narrative")
    npc = _safe_dict(source.get("npc")) or _safe_dict(narration_json.get("npc"))
    if bool(source.get("used_llm")):
        return True
    if narration_json.get("format_version") or narration_json.get("narration") or npc:
        return True
    if isinstance(raw, dict) and raw:
        return True
    # Raw provider text may be present as a string; accept it only when a normal
    # narration/action/npc field is also present, so generic deterministic text
    # cannot masquerade as completed LLM narration.
    if isinstance(raw, str) and raw.strip():
        return bool(
            _phase8_part34_clean_text(source.get("narration") or source.get("final_narration"))
            or npc
        )
    return False


def _phase8_part34_is_deterministic_visible_fallback(source: dict[str, Any]) -> bool:
    source = _safe_dict(source)
    fallback_source = _safe_str(source.get("fallback_narration_source")).strip()
    if fallback_source in _DETERMINISTIC_FALLBACK_SOURCES:
        return True
    if source.get("used_llm") is False:
        return True
    if _phase8_part34_has_structured_llm_payload(source):
        return False
    text = _phase8_part34_clean_text(
        source.get("narration")
        or source.get("final_narration")
        or source.get("raw_payload_narration")
        or source.get("deterministic_fallback_narration")
    )
    lowered = text.casefold()
    return bool(
        lowered.startswith("action:")
        or "\nresult:" in lowered
        or lowered.startswith("you continue:")
        or "no coin changes hands" in lowered
    )


def _phase8_part34_existing_completed_llm_narration(payload: dict[str, Any]) -> str:
    """Return completed text only when it is actually provider/LLM narration.

    The previous guard treated any completed text as final, including the
    deterministic Action/Result fallback.  That caused the real narrator to be
    skipped and let keyword/economy fallbacks override the LLM's dialogue
    categorization.  This guard only suppresses sync narration when the completed
    payload is already LLM-authored or structured as LLM narration.
    """

    for source in _phase8_part34_iter_payload_dicts(payload):
        status = _safe_str(source.get("narration_status")).strip().casefold()
        if status and status != "completed":
            continue
        text = _phase8_part34_clean_text(
            source.get("narration")
            or source.get("final_narration")
            or source.get("raw_payload_narration")
            or source.get("deterministic_fallback_narration")
        )
        if not text:
            continue
        fallback_source = _safe_str(source.get("fallback_narration_source")).strip()
        if fallback_source in _LLM_NARRATION_SOURCES:
            return text
        if _phase8_part34_is_deterministic_visible_fallback(source):
            continue
        if _phase8_part34_has_structured_llm_payload(source):
            return text
    return ""


# Keep the compatibility facade's final helper binding aligned with the runtime
# implementation while callers migrate to responsibility-owned modules.
_phase8_part31_existing_completed_narration = _phase8_part34_existing_completed_llm_narration

__all__ = ['_DETERMINISTIC_FALLBACK_SOURCES', '_LLM_NARRATION_SOURCES', '_PHASE8_PART34_SOURCE', '_phase8_part34_clean_text', '_phase8_part34_existing_completed_llm_narration', '_phase8_part34_is_deterministic_visible_fallback', '_phase8_part34_iter_payload_dicts']