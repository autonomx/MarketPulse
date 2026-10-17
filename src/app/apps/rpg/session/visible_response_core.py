"""Canonical RPG session runtime.

Single source of truth for:
- building a persisted session from adventure-builder startup
- loading/saving canonical sessions
- executing player turns against canonical session state
- shaping turn/bootstrap payloads for the frontend

This replaces the legacy in-memory GameSession / pipeline.py / routes.py flow.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any


logger = logging.getLogger(__name__)




def _has_pending_conversation_response(simulation_state: dict[str, Any]) -> bool:
    thread_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    pending = _safe_dict(thread_state.get("pending_player_response"))
    return bool(pending.get("thread_id") and pending.get("topic_id"))


def _interaction_visible_result_reason(
    general_interaction_result: dict[str, Any],
) -> str:
    general_interaction_result = _safe_dict(general_interaction_result)
    interaction = _safe_dict(general_interaction_result.get("interaction_result"))

    for key in (
        "inventory_result",
        "container_result",
        "repair_result",
        "consumable_result",
        "crafting_result",
        "merchant_result",
        "loot_result",
        "combat_result",
    ):
        nested = _safe_dict(interaction.get(key) or general_interaction_result.get(key))
        if nested and _safe_str(nested.get("reason")):
            return _safe_str(nested.get("reason"))

    if interaction and _safe_str(interaction.get("reason")):
        return _safe_str(interaction.get("reason"))

    return ""


def _replace_stale_visible_result_text(text: Any, *, visible_reason: str) -> str:
    text = _safe_str(text)
    visible_reason = _safe_str(visible_reason)
    if not visible_reason:
        return text
    if not text.strip():
        return f"Result: {visible_reason}"

    # Replace stale fallback result lines while preserving the rest of the
    # generated narration text. This catches:
    #   Result: unknown_item
    #   Result: item_not_found
    #   Result: unknown
    text = re.sub(
        r"(?im)^(\s*Result:\s*)(unknown_item|item_not_found|unknown)\s*$",
        rf"\1{visible_reason}",
        text,
    )

    # Some payloads embed the stale result mid-line.
    text = re.sub(
        r"(?i)Result:\s*(unknown_item|item_not_found|unknown)",
        f"Result: {visible_reason}",
        text,
    )

    return text


def _patch_visible_interaction_reason_into_payload_text(
    payload: dict[str, Any],
    *,
    visible_reason: str,
) -> dict[str, Any]:
    payload = _safe_dict(payload)
    visible_reason = _safe_str(visible_reason)
    if not payload or not visible_reason:
        return payload

    text_keys = (
        "narration",
        "final_narration",
        "narration_preview",
        "raw_payload_narration",
        "action",
        "result",
        "summary",
        "result_summary",
        "action_result",
        "outcome",
    )

    for key in text_keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            # A structured value (``result`` is the turn's result) keeps its
            # structure: only its texts are patched. It used to be turned into
            # its Python repr, which later stages parsed back (WP-5.6).
            payload[key] = _replace_stale_result_in_texts(value, visible_reason=visible_reason)
        else:
            payload[key] = _replace_stale_visible_result_text(value, visible_reason=visible_reason)

    return payload


_STALE_RESULT_LINE = re.compile(r"(?im)^(\s*Result:\s*)(unknown_item|item_not_found|unknown)\s*$")
_STALE_RESULT_INLINE = re.compile(r"(?i)Result:\s*(unknown_item|item_not_found|unknown)")


def _replace_stale_result_in_texts(value: Any, *, visible_reason: str) -> Any:
    """``value`` with the stale result lines in its strings replaced (empty strings stay empty)."""
    if isinstance(value, str):
        if "result:" not in value.lower():
            return value
        value = _STALE_RESULT_LINE.sub(lambda match: f"{match.group(1)}{visible_reason}", value)
        return _STALE_RESULT_INLINE.sub(f"Result: {visible_reason}", value)
    if isinstance(value, dict):
        return {key: _replace_stale_result_in_texts(item, visible_reason=visible_reason) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_stale_result_in_texts(item, visible_reason=visible_reason) for item in value]
    return value


def _apply_visible_interaction_reason_to_resolved_result(
    resolved_result: dict[str, Any],
    *,
    general_interaction_result: dict[str, Any],
) -> dict[str, Any]:
    resolved_result = _safe_dict(resolved_result)
    visible_reason = _interaction_visible_result_reason(general_interaction_result)
    if not visible_reason:
        return resolved_result

    resolved_result["visible_interaction_reason"] = visible_reason

    stale_values = {
        "",
        "item_not_found",
        "unknown_item",
        "unknown",
        "Action: You act.",
        "You act.",
    }

    current_result = _safe_str(resolved_result.get("result"))
    current_action = _safe_str(resolved_result.get("action"))

    if current_result in stale_values:
        resolved_result["result"] = visible_reason

    if (
        current_action in stale_values
        or current_action.startswith("Result: item_not_found")
        or current_action.startswith("Result: unknown_item")
    ):
        resolved_result["action"] = f"Result: {visible_reason}"

    # Some report/narration paths read summary/text instead of result/action.
    for key in ("summary", "result_summary", "action_result", "outcome"):
        current = _safe_str(resolved_result.get(key))
        if (
            current in stale_values
            or current.startswith("Result: item_not_found")
            or current.startswith("Result: unknown_item")
        ):
            resolved_result[key] = visible_reason

    return resolved_result


def _runtime_narration_payload_is_final(payload: dict[str, Any]) -> bool:
    payload = _safe_dict(payload)
    return (
        _safe_str(payload.get("source")) == "provider_runtime_narration"
        and bool(_safe_str(payload.get("narration")).strip())
        and not bool(payload.get("grounding_fallback"))
    )


def _normalize_visible_echo_text(value: Any) -> str:
    return re.sub(r"\W+", " ", _safe_str(value).casefold()).strip()


def _player_input_from_final_result(final_result: dict[str, Any]) -> str:
    final_result = _safe_dict(final_result)
    nested = _safe_dict(final_result.get("result"))
    turn_contract = _safe_dict(final_result.get("turn_contract")) or _safe_dict(
        nested.get("turn_contract")
    )
    return _safe_str(
        final_result.get("player_input")
        or nested.get("player_input")
        or turn_contract.get("player_input")
        or turn_contract.get("player_action")
        or turn_contract.get("action")
    )


def _is_preservable_authoritative_narration(
    final_result: dict[str, Any], narration: Any
) -> bool:
    text = _safe_str(narration).strip()
    if not text:
        return False

    player_input = _player_input_from_final_result(final_result)
    if player_input and _normalize_visible_echo_text(
        text
    ) == _normalize_visible_echo_text(player_input):
        return False

    lowered = text.casefold()
    blocked_fragments = (
        "result: you cannot find that object here",
        "no known route matches that destination",
        "no routes are currently available",
        "no supported semantic action",
        "no_supported_semantic_action_detected",
        "item_not_found",
        "unknown_item",
    )
    return not any(fragment in lowered for fragment in blocked_fragments)


def _accepted_combat_narration_payload(final_result: dict[str, Any]) -> dict[str, Any]:
    final_result = _safe_dict(final_result)
    nested = _safe_dict(final_result.get("result"))
    resolved = _safe_dict(final_result.get("resolved_result")) or _safe_dict(
        nested.get("resolved_result")
    )

    validation = (
        _safe_dict(final_result.get("combat_narration_validation"))
        or _safe_dict(nested.get("combat_narration_validation"))
        or _safe_dict(resolved.get("combat_narration_validation"))
    )
    payload = (
        _safe_dict(final_result.get("combat_narration_payload"))
        or _safe_dict(nested.get("combat_narration_payload"))
        or _safe_dict(resolved.get("combat_narration_payload"))
    )
    if validation.get("ok") is True and _safe_str(payload.get("narration")).strip():
        return payload
    return {}


def _accepted_direct_companion_presentation(
    final_result: dict[str, Any],
) -> dict[str, Any]:
    final_result = _safe_dict(final_result)
    nested = _safe_dict(final_result.get("result"))
    resolved = _safe_dict(final_result.get("resolved_result")) or _safe_dict(
        nested.get("resolved_result")
    )
    direct = (
        _safe_dict(final_result.get("direct_companion_turn_result"))
        or _safe_dict(nested.get("direct_companion_turn_result"))
        or _safe_dict(resolved.get("direct_companion_turn_result"))
    )
    line = _safe_str(direct.get("line")).strip()
    if direct.get("matched") is True and line:
        return {
            "source": "direct_companion_response",
            "narration": line,
            "npc": {
                "speaker": _safe_str(
                    direct.get("name") or direct.get("npc_id") or "Companion"
                ),
                "line": line,
            },
        }
    return {}


def _select_final_visible_presentation(
    final_result: dict[str, Any],
    *,
    runtime_narration_payload: dict[str, Any],
    prior_narration: str,
    prior_npc: dict[str, Any],
    prior_llm_called: bool,
) -> dict[str, Any]:
    final_result = _safe_dict(final_result)
    runtime_narration_payload = _safe_dict(runtime_narration_payload)

    combat_payload = _accepted_combat_narration_payload(final_result)
    if combat_payload:
        return {
            "source": "combat_narration",
            "narration": _safe_str(combat_payload.get("narration")).strip(),
            "npc": _safe_dict(combat_payload.get("npc")),
            "llm_called": True,
            "runtime_payload_source": _safe_str(
                runtime_narration_payload.get("source")
            ),
        }

    direct_companion_payload = _accepted_direct_companion_presentation(final_result)
    if direct_companion_payload:
        return {
            "source": _safe_str(direct_companion_payload.get("source")),
            "narration": _safe_str(direct_companion_payload.get("narration")).strip(),
            "npc": _safe_dict(direct_companion_payload.get("npc")),
            "llm_called": False,
            "runtime_payload_source": _safe_str(
                runtime_narration_payload.get("source")
            ),
        }

    if _runtime_narration_payload_is_final(runtime_narration_payload):
        return {
            "source": "provider_runtime_narration",
            "narration": _safe_str(runtime_narration_payload.get("narration")).strip(),
            "npc": _safe_dict(runtime_narration_payload.get("npc")),
            "llm_called": True,
            "runtime_payload_source": _safe_str(
                runtime_narration_payload.get("source")
            ),
        }

    if _is_preservable_authoritative_narration(final_result, prior_narration):
        return {
            "source": "authoritative_runtime_result",
            "narration": _safe_str(prior_narration).strip(),
            "npc": _safe_dict(prior_npc),
            "llm_called": bool(prior_llm_called),
            "runtime_payload_source": _safe_str(
                runtime_narration_payload.get("source")
            ),
        }

    return {
        "source": _safe_str(runtime_narration_payload.get("source"))
        or "runtime_narration",
        "narration": _safe_str(runtime_narration_payload.get("narration")).strip(),
        "npc": _safe_dict(runtime_narration_payload.get("npc")),
        "llm_called": _safe_str(runtime_narration_payload.get("source"))
        == "provider_runtime_narration",
        "runtime_payload_source": _safe_str(runtime_narration_payload.get("source")),
    }


def _call_combat_narration_provider_text(prompt: str) -> str:
    """Call the app's central active LLM provider for combat narration.

    This must not hardcode Cerebras, LM Studio, OpenRouter, etc.
    It should use whichever provider the app currently marks active.
    """

    system_text = (
        "You are the RPG combat narration layer. "
        "Return only the strict JSON requested by the user prompt."
    )

    # Preferred: use the same central gateway normal RPG narration uses.
    try:
        from app.apps.rpg.foundation.provider_access import chat_completion

        raw = chat_completion(
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            purpose="combat_narration",
        )
        return _extract_llm_text_from_response(raw)
    except ImportError:
        pass

    # Fallback: use the process provider service directly.
    try:
        from app.apps.rpg.foundation.provider_access import get_provider

        provider = get_provider()
    except Exception as exc:
        raise RuntimeError(
            f"combat_narration_active_provider_not_available:{type(exc).__name__}: {exc}"
        ) from exc

    if provider is None:
        raise RuntimeError("combat_narration_active_provider_not_available")

    provider_debug = {
        "type": type(provider).__name__,
        "module": type(provider).__module__,
        "has_chat_completion": hasattr(provider, "chat_completion"),
    }

    if not hasattr(provider, "chat_completion"):
        raise RuntimeError(
            "combat_narration_active_provider_has_no_chat_completion:"
            + json.dumps(provider_debug, ensure_ascii=False, sort_keys=True)
        )

    try:
        from app.providers.base import ChatMessage

        messages = [
            ChatMessage(role="system", content=system_text),
            ChatMessage(role="user", content=prompt),
        ]
    except Exception:
        # Only use dict fallback if your active-provider gateway supports it.
        # Most app providers expect ChatMessage.
        # Dict messages for gateways that accept them.
        messages = [
            {"role": "system", "content": system_text},  # type: ignore[list-item]
            {"role": "user", "content": prompt},  # type: ignore[list-item]
        ]

    try:
        raw = provider.chat_completion(
            messages=messages,
            stream=False,
        )
    except Exception as exc:
        raise RuntimeError(
            "combat_narration_active_provider_call_failed:"
            + f"{type(exc).__name__}: {exc}:"
            + json.dumps(provider_debug, ensure_ascii=False, sort_keys=True)
        )

    return _extract_llm_text_from_response(raw)


def _extract_llm_text_from_response(raw: Any) -> str:
    if hasattr(raw, "content") and isinstance(getattr(raw, "content", None), str):
        text = getattr(raw, "content")
    elif isinstance(raw, dict):
        if isinstance(raw.get("content"), str):
            text = raw["content"]
        elif isinstance(raw.get("text"), str):
            text = raw["text"]
        elif isinstance(raw.get("response"), str):
            text = raw["response"]
        elif isinstance(raw.get("message"), dict) and isinstance(
            raw["message"].get("content"), str
        ):
            text = raw["message"]["content"]
        elif isinstance(raw.get("choices"), list) and raw["choices"]:
            first = raw["choices"][0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(
                    message.get("content"), str
                ):
                    text = message["content"]
                elif isinstance(first.get("text"), str):
                    text = first["text"]
                else:
                    text = json.dumps(raw, ensure_ascii=False)
            else:
                text = json.dumps(raw, ensure_ascii=False)
        else:
            text = json.dumps(raw, ensure_ascii=False)
    else:
        text = "" if raw is None else str(raw)

    text = text.strip()
    if not text:
        raise RuntimeError("combat_narration_provider_returned_empty_text")

    return text


def _apply_combat_narration_if_needed(
    payload: dict[str, Any],
    *,
    combat_result: dict[str, Any],
    combat_state: dict[str, Any],
) -> dict[str, Any]:
    """Attach real LLM combat narration to the active result payload.

    This must run before fallback visible narration is finalized.
    """

    payload = _safe_dict(payload)
    combat_result = _safe_dict(combat_result)
    combat_state = _safe_dict(combat_state)

    from app.apps.rpg.session.fast_combat_narration_skip import (
        apply_fast_combat_narration_skip,
    )

    fast_result = apply_fast_combat_narration_skip(
        payload,
        combat_result=combat_result,
        combat_state=combat_state,
        contract_builder=build_combat_narration_contract,
    )
    if fast_result is not None:
        return fast_result

    payload["combat_narration_attempted"] = True

    if not combat_contract_requires_llm(combat_result):
        return payload

    payload["combat_narration_attempted"] = True
    payload["llm_purpose"] = "combat_narration"
    payload["combat_narration_error"] = ""

    try:
        combat_narration = generate_combat_narration_sync(
            combat_result=combat_result,
            combat_state=combat_state,
            llm_json_call=_call_combat_narration_provider_text,
        )

        contract = _safe_dict(combat_narration.get("combat_narration_contract"))
        validation = _safe_dict(combat_narration.get("combat_narration_validation"))
        narration_payload = _safe_dict(combat_narration.get("payload"))

        payload["llm_called"] = bool(combat_narration.get("llm_called"))
        payload["llm_purpose"] = "combat_narration"
        payload["combat_narration_contract"] = deepcopy(contract)
        payload["combat_narration_validation"] = deepcopy(validation)
        payload["combat_narration_payload"] = deepcopy(narration_payload)
        payload["combat_narration_accepted"] = bool(combat_narration.get("accepted"))

        if combat_narration.get("accepted") is True:
            narration = _safe_str(narration_payload.get("narration"))
            action = _safe_str(narration_payload.get("action"))

            payload["narration"] = narration
            payload["final_narration"] = narration
            payload["narration_preview"] = narration
            payload["raw_payload_narration"] = narration
            payload["action"] = action
            payload["npc"] = _safe_dict(narration_payload.get("npc"))
            payload["reward"] = _safe_str(narration_payload.get("reward"))
            payload["followup_hooks"] = _safe_list(
                narration_payload.get("followup_hooks")
            )
        else:
            payload["combat_narration_rejected"] = True
            # Keep fallback, but expose validation warnings.
            fallback = f"Result: {_safe_str(combat_result.get('reason'))}"
            if not _safe_str(payload.get("narration")).strip():
                payload["narration"] = fallback
            if not _safe_str(payload.get("final_narration")).strip():
                payload["final_narration"] = payload["narration"]
            if not _safe_str(payload.get("narration_preview")).strip():
                payload["narration_preview"] = payload["narration"]

    except Exception as exc:
        payload["llm_called"] = False
        payload["llm_purpose"] = "combat_narration"
        payload["combat_narration_error"] = f"{type(exc).__name__}: {exc}"
        if not _safe_dict(payload.get("combat_narration_contract")):
            payload["combat_narration_contract"] = build_combat_narration_contract(
                combat_result=combat_result,
                combat_state=combat_state,
            )
        if not _safe_dict(payload.get("combat_narration_validation")):
            payload["combat_narration_validation"] = {
                "ok": False,
                "warnings": ["combat_narration_provider_error"],
                "source": "deterministic_combat_narration_validator",
            }
        fallback = f"Result: {_safe_str(combat_result.get('reason'))}"
        if not _safe_str(payload.get("narration")).strip():
            payload["narration"] = fallback
        if not _safe_str(payload.get("final_narration")).strip():
            payload["final_narration"] = payload["narration"]
        if not _safe_str(payload.get("narration_preview")).strip():
            payload["narration_preview"] = payload["narration"]

    return payload


from app.apps.rpg.narration.combat_contract import (
    build_combat_narration_contract,
    combat_contract_requires_llm,
)
from app.apps.rpg.narration.combat_service import generate_combat_narration_sync
from app.apps.rpg.session.state_normalization import _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list
