"""Contract attachment helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_CONTRACT_KEYS = (
    "intent_result",
    "world_assessment",
    "response_authority",
    "turn_plan",
    "reasoning_trace",
)
_INTERPRETIVE_SOURCE = "world_grounded_interpretive_adjudication_v1"


def add_contracts_to_interpretive_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return an interpretive result enriched with generated contract dictionaries."""

    copied = deepcopy(_d(result))
    if copied.get("source") != _INTERPRETIVE_SOURCE:
        return copied

    from app.apps.rpg.session.response_authority import resolve_response_authority
    from app.apps.rpg.session.turn_plan import build_turn_plan_for_response
    from app.apps.rpg.session.world_reasoning_adapter import build_world_reasoning_from_interpretive_result
    from app.apps.rpg.session.world_reasoning_contracts import build_reasoning_trace

    mapped = build_world_reasoning_from_interpretive_result(copied)
    intent_result = _d(mapped.get("intent_result"))
    world_assessment = _d(mapped.get("world_assessment"))
    semantic_advisory = _d(copied.get("first_call_semantic_advisory"))
    grounding_validation = _d(copied.get("grounding_validation"))
    response_authority = resolve_response_authority(
        player_input=_s(copied.get("player_input")),
        intent_result=intent_result,
        world_assessment=world_assessment,
        grounding_packet=_d(grounding_validation.get("turn_grounding_packet")),
        semantic_advisory=semantic_advisory,
    )
    turn_plan = build_turn_plan_for_response(
        intent_result=intent_result,
        world_assessment=world_assessment,
        response_authority=response_authority,
        semantic_advisory=semantic_advisory,
    )
    reasoning_trace = build_reasoning_trace(
        intent_result=intent_result,
        world_assessment=world_assessment,
        response_authority=response_authority,
        turn_plan=turn_plan,
    )
    return attach_contracts_to_result(
        copied,
        {
            "intent_result": intent_result,
            "world_assessment": world_assessment,
            "response_authority": response_authority,
            "turn_plan": turn_plan,
            "reasoning_trace": reasoning_trace,
        },
    )


def attach_contracts_to_result(result: dict[str, Any], contracts: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with contract dictionaries attached in standard locations."""

    copied = deepcopy(_d(result))
    clean_contracts = {key: deepcopy(_d(contracts.get(key))) for key in _CONTRACT_KEYS}
    for key, value in clean_contracts.items():
        copied[key] = deepcopy(value)
    for container_key in ("result", "resolved_result", "grounding_validation"):
        container = _d(copied.get(container_key))
        if container:
            for key, value in clean_contracts.items():
                container[key] = deepcopy(value)
            copied[container_key] = container
    return copied


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
