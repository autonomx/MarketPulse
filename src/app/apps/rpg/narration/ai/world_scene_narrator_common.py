"""Phase 5 — LLM Scene Engine + NPC Behavior

Turns structured scenes into narrative experiences:
    Scene → Narrative → NPC reactions → Dialogue → Player response

Provides prompt building, narrative generation, and response parsing
for the scene narration pipeline.

Phase 5.1 fixes:
- JSON-structured LLM output enforcement
- NPC state injection (memory, beliefs, relationships)
- Choice → action binding
- Scene action hooks
"""

from __future__ import annotations

# ruff: noqa: F401

import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.apps.rpg.narration.ai.grounding_settings import normalize_grounding_settings
from app.apps.rpg.narration.ai.grounding_validator import select_grounded_narration_candidate
from app.apps.rpg.world.memory.npc_memory_recall import memory_reference_is_backed
from app.apps.rpg.narration.dialogue.npc_response_architecture import (
    build_runtime_npc_response_architecture,
)
from app.apps.rpg.narration.presentation.current_turn_prompt_contract import (
    build_runtime_current_turn_prompt_contract,
    format_runtime_prompt_contract_block,
)
from app.apps.rpg.narration.presentation.grounding_validator import (
    build_runtime_presentation_guardrails_block,
    sanitize_unsupported_combat_payload,
)
from app.apps.rpg.narration.presentation.provider_payload import (
    parse_runtime_provider_payload,
)


# Phase 8: player-facing encounter view
from app.apps.rpg.rules.player import build_encounter_view
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

logger = logging.getLogger(__name__)

# Compatibility export for the retired single-process narration guard.
_ACTIVE_NARRATIONS: frozenset[str] = frozenset()

NARRATION_JSON_FORMAT_VERSION = "rpg_narration_v2"

NARRATION_JSON_SCHEMA_HINT = {
    "format_version": NARRATION_JSON_FORMAT_VERSION,
    "narration": "string",
    "action": "string",
    "npc": {
        "speaker": "string",
        "line": "string",
    },
    "reward": "string",
    "followup_hooks": [],
}


def _extract_llm_text(response):
    """Extract text from provider response in various formats."""
    if isinstance(response, str):
        return response.strip()

    if not isinstance(response, dict):
        return ""

    # OpenAI / Cerebras format
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]

        # Chat format
        msg = first.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        # Text format fallback
        text = first.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    # Direct text fallback
    if isinstance(response.get("text"), str):
        return response["text"].strip()

    return ""


def _llm_text(llm_gateway, prompt, *, context=None, on_chunk=None):
    """Call the LLM gateway and return the response as a clean string."""
    logger.info(
        "[RPG LLM GATEWAY] Calling LLM with prompt length: %d, context keys: %s",
        len(prompt),
        list(context.keys()) if context else [],
    )
    gateway_call = getattr(llm_gateway, "call", None)
    gateway_generate = getattr(llm_gateway, "generate", None)
    gateway_generate_stream = getattr(llm_gateway, "generate_stream", None)

    if on_chunk:
        # Try streaming if callback provided
        chunks = []
        try:
            logger.debug('[RPG][LLM] calling provider.stream')
            if callable(gateway_call):
                events = gateway_call("generate_stream", prompt, context=context or {})
            elif callable(gateway_generate_stream):
                events = gateway_generate_stream(prompt, context=context or {})
            else:
                raise AttributeError("gateway has no streaming interface")

            for event in events:
                piece = _safe_str(_safe_dict(event).get("text"))
                if piece:
                    chunks.append(piece)
                    on_chunk(piece)
            logger.debug('[RPG][LLM] stream completed, chunks: %s', len(chunks))
            return _extract_llm_text("".join(chunks).strip())
        except Exception as exc:
            logger.debug('[RPG][LLM] stream failed: %s', repr(exc))
            logger.exception(
                "[RPG LLM GATEWAY] Streaming failed, falling back to non-streaming"
            )
            if chunks:
                return _extract_llm_text("".join(chunks).strip())

    # Fallback to non-streaming
    try:
        logger.debug('[RPG][LLM] calling provider.generate')
        logger.debug('[ACTIVE PROVIDER] %s', llm_gateway)
        if callable(gateway_call):
            response = gateway_call("generate", prompt, context=context or {})
        elif callable(gateway_generate):
            response = gateway_generate(prompt, context=context or {})
        elif callable(gateway_generate_stream):
            chunks = []
            for event in gateway_generate_stream(prompt, context=context or {}):
                piece = _safe_str(_safe_dict(event).get("text"))
                if piece:
                    chunks.append(piece)
            response = "".join(chunks).strip()
        else:
            raise AttributeError("gateway has no generate or call interface")
        logger.debug('[RPG][LLM] raw response: %s', repr(response)[:500])
        logger.info(
            "[RPG LLM GATEWAY] Received response type: %s, length: %d",
            type(response),
            len(str(response)) if response else 0,
        )
    except Exception as exc:
        logger.debug('[RPG][LLM] generate failed: %s', repr(exc))
        logger.exception("[RPG LLM GATEWAY] LLM call failed")
        raise RuntimeError(
            f"live_llm_required_but_llm_failed: provider_exception: {repr(exc)}"
        )
    if response is None:
        logger.warning("[RPG LLM GATEWAY] LLM returned None")
        return ""
    # Extract text from response dict or return string directly
    return _extract_llm_text(response)


# ---------------------------------------------------------------------------
# Phase 6.5 — social context helpers
# ---------------------------------------------------------------------------
def _attach_social_context(scene, simulation_state):
    scene = dict(scene or {})
    simulation_state = simulation_state or {}
    social_state = simulation_state.get("social_state") or {}

    scene["active_rumors"] = [
        dict(item) for item in (simulation_state.get("active_rumors") or [])[:3]
    ]
    scene["active_alliances"] = [
        dict(item)
        for item in (social_state.get("alliances") or [])
        if item.get("status") == "active"
    ][:3]
    scene["faction_positions"] = {
        key: dict(value)
        for key, value in sorted((social_state.get("group_positions") or {}).items())
    }
    return scene


# ---------------------------------------------------------------------------
# Phase 6 — NPC mind context helpers
# ---------------------------------------------------------------------------


def _safe_str_p6(value):
    if value is None:
        return ""
    return str(value)


def _attach_npc_mind_context(actor, simulation_state):
    """Attach Phase 6 NPC mind context to an actor dict."""
    actor = dict(actor or {})
    simulation_state = simulation_state or {}

    npc_id = _safe_str_p6(actor.get("id"))
    npc_minds = simulation_state.get("npc_minds") or {}
    mind = npc_minds.get(npc_id) or {}

    if isinstance(mind, dict):
        actor["memory_summary"] = ((mind.get("memory") or {}).get("entries") or [])[:5]
        actor["belief_summary"] = (mind.get("beliefs") or {}).get("beliefs") or {}
        actor["active_goals"] = ((mind.get("goals") or {}).get("goals") or [])[:5]
        actor["last_decision"] = mind.get("last_decision") or {}

    return actor


_NARRATION_MAX_MARKDOWN = 300


def _title_case_token(value: Any) -> str:
    text = _safe_str(value).strip()
    if not text:
        return ""
    return text.replace("_", " ").strip().title()


def _force_live_llm_required(narration_context: dict[str, Any]) -> bool:
    narration_context = _safe_dict(narration_context)
    runtime_settings = _safe_dict(narration_context.get("runtime_settings"))
    performance = _safe_dict(narration_context.get("performance"))
    return bool(
        narration_context.get("require_live_llm_narration")
        or runtime_settings.get("require_live_llm_narration")
        or performance.get("require_live_llm_narration")
    )


__all__ = (
    'annotations',
    'json',
    'logging',
    're',
    'traceback',
    'dataclass',
    'field',
    'Any',
    'Callable',
    'Dict',
    'List',
    'Optional',
    'normalize_grounding_settings',
    'select_grounded_narration_candidate',
    'memory_reference_is_backed',
    'build_runtime_npc_response_architecture',
    'build_runtime_current_turn_prompt_contract',
    'format_runtime_prompt_contract_block',
    'build_runtime_presentation_guardrails_block',
    'sanitize_unsupported_combat_payload',
    'parse_runtime_provider_payload',
    'build_encounter_view',
    'logger',
    '_ACTIVE_NARRATIONS',
    'NARRATION_JSON_FORMAT_VERSION',
    'NARRATION_JSON_SCHEMA_HINT',
    '_extract_llm_text',
    '_llm_text',
    '_attach_social_context',
    '_safe_str_p6',
    '_attach_npc_mind_context',
    '_NARRATION_MAX_MARKDOWN',
    '_safe_str',
    '_safe_dict',
    '_safe_list',
    '_title_case_token',
    '_force_live_llm_required',
)
