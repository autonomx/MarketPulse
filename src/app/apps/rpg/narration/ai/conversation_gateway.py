from __future__ import annotations

from typing import Any, Dict, List

from .conversation_prompt_builder import build_npc_conversation_line_prompt
from .conversation_response_parser import (
    is_valid_conversation_line,
    parse_conversation_line_response,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def generate_recorded_conversation_line(
    llm_gateway: Any,
    conversation: Dict[str, Any],
    speaker_id: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    recent_lines: List[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt = build_npc_conversation_line_prompt(
        conversation,
        speaker_id,
        simulation_state,
        runtime_state,
        recent_lines,
    )
    raw = llm_gateway.generate(prompt) if llm_gateway else ""
    parsed = parse_conversation_line_response(raw)
    if not is_valid_conversation_line(parsed):
        return {}
    conv_id = _safe_str(_safe_dict(conversation).get("conversation_id"))
    turn = int(_safe_dict(conversation).get("turn_count", 0) or 0) + 1
    return {
        "key": f"conversation_line:{conv_id}:{turn}",
        "prompt": prompt,
        "raw": raw,
        "parsed": parsed,
        "source": "llm",
    }


def write_conversation_line(
    conversation: dict[str, Any],
    speaker_id: str,
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    recent_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """The world conversation tick's line writer (``world.contracts.ConversationHooks.write_line``)."""
    from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway

    return generate_recorded_conversation_line(
        build_app_llm_gateway(),
        conversation,
        speaker_id,
        simulation_state,
        runtime_state,
        recent_lines,
    )
