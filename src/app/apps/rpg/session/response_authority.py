"""Pure response-authority resolution for RPG turn contracts."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.world_reasoning_contracts import build_response_authority

_PARSE_NOISE = {"[object object]", "undefined", "null", "none", "nan", "[]", "[ ]", "{}", "{ }", "tool_calls: []", '"tool_calls": []'}
_RUNTIME_KINDS = {"buy", "sell", "attack", "travel", "equip", "unequip", "pay", "hire", "runtime_action"}


def resolve_response_authority(
    *,
    player_input: str,
    intent_result: dict[str, Any] | None = None,
    world_assessment: dict[str, Any] | None = None,
    grounding_packet: dict[str, Any] | None = None,
    semantic_advisory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the authority allowed to answer or mutate for this turn."""

    text = _norm(player_input)
    intent = _d(intent_result)
    assessment = _d(world_assessment)
    semantic = _d(semantic_advisory)
    packet = _d(grounding_packet)

    if _is_parse_noise(text):
        return build_response_authority(source="system", confidence="high", metadata={"reason": "parse_noise"})

    target_id = _s(intent.get("target_id") or semantic.get("target_id"))
    target_name = _s(intent.get("target_name") or semantic.get("target_name"))
    if not target_id or not target_name:
        packet_id, packet_name = _addressed_npc_from_packet(packet)
        target_id = target_id or packet_id
        target_name = target_name or packet_name

    kind = _s(intent.get("kind") or semantic.get("action_type")).casefold()
    actionability = _s(assessment.get("actionability")).casefold()
    if actionability == "runtime_action" or kind in _RUNTIME_KINDS or bool(assessment.get("state_change_allowed")):
        return build_response_authority(
            source="deterministic_runtime",
            confidence="high",
            metadata={"reason": "runtime_required"},
        )

    if target_id.startswith("npc:") or (target_id and target_name):
        return build_response_authority(
            source="addressed_npc",
            authority_id=target_id if target_id.startswith("npc:") else f"npc:{target_id}",
            display_name=target_name,
            confidence="high",
            metadata={"reason": "addressed_npc"},
        )

    if kind == "observation" or actionability == "observe" or "look around" in text or text == "look":
        return build_response_authority(source="narrator", confidence="high", metadata={"reason": "observation"})

    if kind in {"world_consequence", "environment", "physical"}:
        return build_response_authority(
            source="world_simulation",
            confidence="medium",
            metadata={"reason": "world_consequence"},
        )

    return build_response_authority(source="narrator", confidence="medium", metadata={"reason": "default_visible_response"})


def _addressed_npc_from_packet(packet: dict[str, Any]) -> tuple[str, str]:
    addressed = _d(_d(packet.get("npc_context")).get("addressed_npcs_by_id"))
    if addressed:
        npc_id, profile = next(iter(addressed.items()))
        profile = _d(profile)
        return f"npc:{_s(npc_id)}", _s(profile.get("name"))
    addressed_list = _l(_d(packet.get("npc_context")).get("addressed_npcs"))
    if addressed_list:
        profile = _d(addressed_list[0])
        npc_id = _s(profile.get("id"))
        return (f"npc:{npc_id}" if npc_id else "", _s(profile.get("name")))
    return "", ""


def _is_parse_noise(text: str) -> bool:
    if not text or text in _PARSE_NOISE:
        return True
    alpha = sum(1 for char in text if char.isalpha())
    return len(text) > 2 and alpha == 0


def _norm(value: Any) -> str:
    return _s(value).casefold().strip()


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
