"""Compact foreground RPG turn response contract.

``build_turn_response_v2`` produces ``RpgTurnResponse`` (WP-8.6): the turn route
publishes it as its OpenAPI response and the production-turn characterization
validates every response against it. Fields the budget may drop are optional,
and nested runtime projections stay open (``extra="allow"``), so the model
documents the response without filtering it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .turn_response_budget import enforce_turn_response_budget
from .visible_response import build_visible_response

TURN_RESPONSE_CONTRACT_VERSION = "rpg_turn_response_v2"
TURN_RESPONSE_MAX_BYTES = 50_000


class _OpenContract(BaseModel):
    model_config = ConfigDict(extra="allow")


class RpgTurnVisibleResponse(_OpenContract):
    format_version: str | None = None
    plain_text: str | None = None
    narration: str | None = None
    npc: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


class RpgTurnState(_OpenContract):
    revision: int | None = None
    changed: bool = False
    changed_domains: list[str] = Field(default_factory=list)


class RpgTurnResult(_OpenContract):
    ok: bool = True
    turn_id: str | None = None
    tick: int | None = None
    interaction_id: str | None = None
    stateful: bool | None = None
    changed_domains: list[str] = Field(default_factory=list)
    action_type: str | None = None
    semantic_action_type: str | None = None
    semantic_family: str | None = None
    outcome: str | None = None
    narration_status: str | None = None
    llm_called: bool | None = None
    llm_purpose: str | None = None
    source: str | None = None
    visible_response: RpgTurnVisibleResponse | None = None
    timing: dict[str, float] = Field(default_factory=dict)


class RpgTurnResponse(_OpenContract):
    ok: bool
    contract_version: Literal["rpg_turn_response_v2"]
    session_id: str
    submission_id: str | None = None
    interaction_id: str | None = None
    turn_id: str | None = None
    simulation_tick: int | None = None
    job_id: str | None = None
    trace_id: str | None = None
    command: str
    visible_response: RpgTurnVisibleResponse
    response: str | None = None
    content: str | None = None
    result: RpgTurnResult
    state: RpgTurnState
    timing: dict[str, float] = Field(default_factory=dict)
    session_summary: dict[str, Any] = Field(default_factory=dict)
    canonical_narrative_response: dict[str, Any] | None = None


def build_turn_response_v2(
    result: dict[str, Any],
    *,
    session_id: str,
    command: str,
    session: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Project a potentially huge runtime result into a bounded browser contract."""

    root = _dict(result)
    nested = _dict(root.get("result"))
    authoritative = _dict(root.get("authoritative"))
    sources = (root, nested, authoritative)
    visible = build_visible_response(root, command)
    text = _text(visible.get("plain_text"))
    turn_id = _first_text(sources, "turn_id")
    tick = _first_int(sources, "tick", "simulation_tick")
    submission_id = _first_text(sources, "submission_id")
    interaction_id = _first_text(sources, "interaction_id") or turn_id
    timing = _compact_timing(sources)
    dialogue_quality = _first_dict(sources, "dialogue_quality")
    job_id = _job_id(root)
    stateful = _first_bool(sources, "stateful")
    changed_domains = _changed_domains(sources, stateful=stateful)
    state_revision = _state_revision(session, sources)
    session_summary = _session_summary(session or _dict(root.get("session")))
    compact_result = {
        "ok": root.get("ok") is not False,
        "turn_id": turn_id or None,
        "tick": tick,
        "interaction_id": interaction_id or None,
        "stateful": stateful,
        "changed_domains": changed_domains,
        "action_type": _first_text(sources, "action_type") or None,
        "semantic_action_type": _first_text(sources, "semantic_action_type") or None,
        "semantic_family": _first_text(sources, "semantic_family") or None,
        "outcome": _first_text(sources, "outcome") or None,
        "narration_status": _first_text(sources, "narration_status") or None,
        "llm_called": _first_bool(sources, "llm_called"),
        "llm_purpose": _first_text(sources, "llm_purpose") or None,
        "source": _first_text(sources, "source") or None,
        "dialogue_quality_hook_applied": _first_bool(
            sources,
            "dialogue_quality_hook_applied",
        ),
        "dialogue_quality": _drop_none(
            {
                "acceptable": dialogue_quality.get("acceptable"),
                "repaired": dialogue_quality.get("repaired"),
                "repair_source": dialogue_quality.get("repair_source"),
                "violations": dialogue_quality.get("violations"),
                "warnings": dialogue_quality.get("warnings"),
            }
        ),
        "visible_response": visible,
        "timing": timing,
    }
    payload = {
        "ok": True,
        "contract_version": TURN_RESPONSE_CONTRACT_VERSION,
        "session_id": session_id,
        "submission_id": submission_id or None,
        "interaction_id": interaction_id or None,
        "turn_id": turn_id or None,
        "simulation_tick": tick,
        "job_id": job_id or None,
        "trace_id": _text(trace_id) or None,
        "command": command,
        "visible_response": visible,
        "response": text,
        "content": text,
        "result": _drop_none(compact_result),
        "state": {
            "revision": state_revision,
            "changed": bool(changed_domains),
            "changed_domains": changed_domains,
        },
        "timing": timing,
        "session_summary": session_summary,
        "creation_server_trace": _compact_server_trace(_dict(root.get("creation_server_trace"))),
    }
    return enforce_turn_response_budget(
        _drop_none(payload),
        max_bytes=TURN_RESPONSE_MAX_BYTES,
    )


def _compact_timing(sources: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    raw = _first_dict(sources, "manual_turn_stage_timing", "stage_timing", "timing")
    allowed = {
        "manual_turn_ms",
        "pre_runtime_intent_llm_ms",
        "compact_dialogue_llm_ms",
        "deterministic_runtime_apply_ms",
        "grounding_validation_ms",
        "repair_ms",
        "state_snapshot_ms",
        "deferred_enqueue_ms",
        "provider_ms",
        "runtime_ms",
        "persistence_ms",
        "serialization_ms",
    }
    return {
        key: round(float(value), 3)
        for key, value in raw.items()
        if key in allowed and isinstance(value, (int, float))
    }


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    manifest = _dict(session.get("manifest"))
    state = _dict(session.get("state"))
    simulation = _dict(session.get("simulation_state")) or state
    runtime = _dict(session.get("runtime_state"))
    scene = _dict(state.get("scene")) or _dict(simulation.get("scene"))
    player = _dict(state.get("player")) or _dict(simulation.get("player_state"))
    return _drop_none(
        {
            "id": _text(manifest.get("session_id") or manifest.get("id")) or None,
            "title": _text(manifest.get("title") or session.get("title")) or None,
            "location": _text(
                scene.get("location_name")
                or scene.get("location")
                or state.get("location")
                or simulation.get("location")
            ) or None,
            "turn_count": _int_or_none(manifest.get("turn_count") or session.get("turn_count")),
            "interaction_seq": _int_or_none(runtime.get("interaction_seq")),
            "state_revision": _int_or_none(runtime.get("state_revision")),
            "player_level": _int_or_none(player.get("level")),
            "player_hp": _int_or_none(player.get("hp") or player.get("health")),
        }
    )


def _compact_server_trace(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "server_job_created_at",
        "server_job_started_at",
        "server_job_completed_at",
        "server_response_persisted_at",
        "job_id",
        "submission_id",
    }
    return {key: item for key, item in value.items() if key in allowed and item is not None}


def _job_id(root: dict[str, Any]) -> str:
    trace = _dict(root.get("creation_server_trace"))
    direct = _text(trace.get("job_id"))
    if direct:
        return direct
    job = _dict(root.get("foreground_job"))
    return _text(job.get("id"))


def _changed_domains(sources: tuple[dict[str, Any], ...], *, stateful: bool | None) -> list[str]:
    explicit: list[str] = []
    for source in sources:
        value = source.get("changed_domains")
        if not isinstance(value, list):
            value = _dict(source.get("state")).get("changed_domains")
        if isinstance(value, list):
            for item in value:
                text = _text(item)
                if text and text not in explicit:
                    explicit.append(text)
    if explicit:
        return explicit[:12]
    family = _first_text(sources, "semantic_family")
    action = _first_text(sources, "action_type", "semantic_action_type")
    if stateful is False or action == "npc_interpretive_dialogue":
        return ["conversation"]
    domains = ["conversation"]
    mapping = {
        "combat": ["combat", "player"],
        "trade": ["currency", "inventory", "merchant"],
        "item": ["inventory", "player"],
        "travel": ["location", "world"],
        "quest": ["quests", "journal"],
    }
    for item in mapping.get(family, []):
        if item not in domains:
            domains.append(item)
    return domains if stateful else []


def _state_revision(session: dict[str, Any] | None, sources: tuple[dict[str, Any], ...]) -> int | None:
    runtime = _dict((session or {}).get("runtime_state"))
    value = runtime.get("state_revision")
    if value is None:
        for source in sources:
            nested_state = _dict(source.get("state"))
            if nested_state.get("revision") is not None:
                value = nested_state.get("revision")
                break
    if value is None:
        value = _first_value(sources, "state_revision", "revision")
    return _int_or_none(value)


def _first_dict(sources: tuple[dict[str, Any], ...], *keys: str) -> dict[str, Any]:
    for source in sources:
        for key in keys:
            value = _dict(source.get(key))
            if value:
                return value
    return {}


def _first_text(sources: tuple[dict[str, Any], ...], *keys: str) -> str:
    value = _first_value(sources, *keys)
    return _text(value)


def _first_int(sources: tuple[dict[str, Any], ...], *keys: str) -> int | None:
    return _int_or_none(_first_value(sources, *keys))


def _first_bool(sources: tuple[dict[str, Any], ...], *keys: str) -> bool | None:
    value = _first_value(sources, *keys)
    return value if isinstance(value, bool) else None


def _first_value(sources: tuple[dict[str, Any], ...], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != {}}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
