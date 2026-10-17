"""Persistent, deterministic focus for ordinary player/NPC dialogue."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Mapping

_MAX_DIRECT_BEATS = 12
_MAX_THREADS = 32
_DEFAULT_FOCUS_TIMEOUT_TICKS = 8
_GENERIC_NPC_IDS = {"", "npc", "npc:npc", "npc:unknown", "unknown"}


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _l(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _s(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _s(value).casefold()).strip()


def _clip(value: Any, limit: int = 500) -> str:
    return _s(value)[:limit]


def _dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _s(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _npc_rows(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("npc_index", "npcs", "known_npcs", "nearby_npcs", "characters", "actor_states", "npc_states", "actors"):
        value = source.get(key)
        if isinstance(value, Mapping):
            for npc_id, raw in value.items():
                row = _d(raw)
                row.setdefault("id", _s(npc_id))
                rows.append(row)
        elif isinstance(value, list):
            rows.extend(_d(raw) for raw in value)
    present_state = _d(source.get("present_npc_state"))
    if present_state:
        rows.extend(_npc_rows(present_state))
    return rows


def _npc_catalog(simulation_state: Mapping[str, Any], runtime_state: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for source in (simulation_state, runtime_state):
        for row in _npc_rows(source):
            npc_id = _s(row.get("id") or row.get("npc_id") or row.get("actor_id"))
            if not npc_id or npc_id.casefold() in _GENERIC_NPC_IDS:
                continue
            name = _s(row.get("name") or row.get("display_name") or row.get("title") or npc_id)
            catalog.setdefault(npc_id, {"id": npc_id, "name": name})
    return catalog


def _alias_map(catalog: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for npc_id, row in catalog.items():
        for value in (npc_id, npc_id.replace("npc:", ""), row.get("name")):
            alias = _norm(value)
            if alias:
                aliases.setdefault(alias, npc_id)
    return aliases


def _canonical_npc_id(value: Any, catalog: Mapping[str, Mapping[str, str]]) -> str:
    raw = _s(value)
    if not raw or raw.casefold() in _GENERIC_NPC_IDS:
        return ""
    if raw in catalog:
        return raw
    aliases = _alias_map(catalog)
    return aliases.get(_norm(raw), "")


def _present_ids(simulation_state: Mapping[str, Any], runtime_state: Mapping[str, Any]) -> list[str]:
    sim = _d(simulation_state)
    runtime = _d(runtime_state)
    player = _d(sim.get("player_state"))
    scene = _d(runtime.get("current_scene") or runtime.get("grounded_scene_context") or sim.get("current_scene") or sim.get("scene"))
    present_state = _d(sim.get("present_npc_state"))
    ids: list[Any] = []
    for source in (scene, player, runtime, present_state):
        ids.extend(_l(source.get("present_npc_ids")))
        ids.extend(_l(source.get("nearby_npc_ids")))
    return _dedupe(ids)


def _location_id(simulation_state: Mapping[str, Any], runtime_state: Mapping[str, Any]) -> str:
    sim = _d(simulation_state)
    runtime = _d(runtime_state)
    scene = _d(runtime.get("current_scene") or runtime.get("grounded_scene_context") or sim.get("current_scene") or sim.get("scene"))
    player = _d(sim.get("player_state"))
    return _s(scene.get("location_id") or player.get("location_id") or runtime.get("location_id"))


def _scene_id(simulation_state: Mapping[str, Any], runtime_state: Mapping[str, Any]) -> str:
    sim = _d(simulation_state)
    runtime = _d(runtime_state)
    scene = _d(runtime.get("current_scene") or runtime.get("grounded_scene_context") or sim.get("current_scene") or sim.get("scene"))
    return _s(scene.get("scene_id") or scene.get("id"))


def _thread_state(simulation_state: Mapping[str, Any], runtime_state: Mapping[str, Any]) -> dict[str, Any]:
    return _d(
        _d(simulation_state).get("conversation_thread_state")
        or _d(runtime_state).get("conversation_thread_state")
    )


def _thread_id(thread: Mapping[str, Any]) -> str:
    return _s(thread.get("thread_id") or thread.get("id"))


def _thread_beats(thread: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_d(row) for row in _l(thread.get("beats")) if isinstance(row, Mapping)]


def _participant_ids(thread: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for participant in _l(thread.get("participants")):
        if isinstance(participant, Mapping):
            values.append(participant.get("id") or participant.get("npc_id"))
        else:
            values.append(participant)
    values.extend(_l(thread.get("participant_ids")))
    return _dedupe(values)


def _is_player_thread(thread: Mapping[str, Any]) -> bool:
    participant_ids = {value.casefold() for value in _participant_ids(thread)}
    if "player" in participant_ids:
        return True
    participation = _d(thread.get("player_participation"))
    if participation.get("included") is True:
        return True
    return any(_s(beat.get("speaker_id")).casefold() == "player" for beat in _thread_beats(thread))


def _thread_location_matches(thread: Mapping[str, Any], location_id: str) -> bool:
    thread_location = _s(thread.get("location_id"))
    return not location_id or not thread_location or thread_location == location_id


def _thread_is_fresh(thread: Mapping[str, Any], tick: int, timeout_ticks: int) -> bool:
    if tick <= 0 or timeout_ticks <= 0:
        return True
    updated_tick = int(thread.get("updated_tick") or thread.get("tick") or 0)
    return not updated_tick or tick - updated_tick <= timeout_ticks


def _exact_turns(thread: Mapping[str, Any]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for beat in _thread_beats(thread)[-_MAX_DIRECT_BEATS:]:
        speaker_id = _s(beat.get("speaker_id"))
        target_id = _s(beat.get("target_id") or beat.get("listener_id"))
        text = _clip(beat.get("text") or beat.get("line") or beat.get("message"), 700)
        if not speaker_id or not text:
            continue
        turns.append(
            {
                "beat_id": _s(beat.get("beat_id") or beat.get("turn_id")),
                "speaker_id": speaker_id,
                "target_id": target_id,
                "text": text,
                "player_input": text if speaker_id.casefold() == "player" else "",
                "summary": text if speaker_id.casefold() != "player" else "",
                "scene_id": _s(beat.get("scene_id") or thread.get("scene_id")),
                "location_id": _s(beat.get("location_id") or thread.get("location_id")),
                "tick": int(beat.get("tick") or 0),
            }
        )
    return turns


def _latest_directed_npc_beat(thread: Mapping[str, Any]) -> dict[str, Any]:
    for beat in reversed(_thread_beats(thread)):
        speaker_id = _s(beat.get("speaker_id"))
        target_id = _s(beat.get("target_id") or beat.get("listener_id"))
        if speaker_id.startswith("npc:") and target_id.casefold() == "player":
            return beat
    return {}


def _thread_resolution_candidates(
    thread: Mapping[str, Any],
    *,
    catalog: Mapping[str, Mapping[str, str]],
    present: set[str],
) -> tuple[list[str], dict[str, Any]]:
    explicit = _canonical_npc_id(
        thread.get("default_target_id")
        or thread.get("active_target_id")
        or thread.get("target_id"),
        catalog,
    )
    latest_directed = _latest_directed_npc_beat(thread)
    directed_id = _canonical_npc_id(latest_directed.get("speaker_id"), catalog)
    latest_any = _thread_beats(thread)[-1] if _thread_beats(thread) else {}
    latest_target = ""
    if _s(latest_any.get("speaker_id")).casefold() == "player":
        latest_target = _canonical_npc_id(latest_any.get("target_id") or latest_any.get("listener_id"), catalog)
    elif _s(latest_any.get("target_id") or latest_any.get("listener_id")).casefold() == "player":
        latest_target = _canonical_npc_id(latest_any.get("speaker_id"), catalog)
    participants = [
        _canonical_npc_id(value, catalog)
        for value in _participant_ids(thread)
        if _s(value).startswith("npc:")
    ]
    candidates = _dedupe([explicit, directed_id, latest_target, *participants])
    if present:
        candidates = [value for value in candidates if value in present]
    return candidates, latest_directed


def _active_threads(state: Mapping[str, Any], *, location_id: str, tick: int, timeout_ticks: int) -> list[dict[str, Any]]:
    threads = {_thread_id(row): _d(row) for row in _l(state.get("threads")) if _thread_id(_d(row))}
    ordered_ids = _dedupe(
        [
            state.get("active_dialogue_thread_id"),
            state.get("active_thread_id"),
            *reversed(_l(state.get("active_thread_ids"))),
        ]
    )
    selected: list[dict[str, Any]] = []
    for thread_id in ordered_ids:
        thread = threads.get(thread_id)
        if not thread or not _is_player_thread(thread):
            continue
        if not _thread_location_matches(thread, location_id) or not _thread_is_fresh(thread, tick, timeout_ticks):
            continue
        selected.append(thread)
    if selected:
        return selected
    fallback = [
        thread for thread in threads.values()
        if _is_player_thread(thread)
        and _thread_location_matches(thread, location_id)
        and _thread_is_fresh(thread, tick, timeout_ticks)
    ]
    fallback.sort(key=lambda row: int(row.get("updated_tick") or row.get("tick") or 0), reverse=True)
    return fallback[:2]


def _explicit_named_ids(player_input: str, catalog: Mapping[str, Mapping[str, str]]) -> list[str]:
    text = f" {_norm(player_input)} "
    matches: list[str] = []
    for alias, npc_id in _alias_map(catalog).items():
        if alias and f" {alias} " in text:
            matches.append(npc_id)
    return _dedupe(matches)


def _resolution(
    *,
    target_id: str = "",
    target_name: str = "",
    thread: Mapping[str, Any] | None = None,
    reply_to_beat: Mapping[str, Any] | None = None,
    source: str,
    candidates: Iterable[Any] = (),
    confidence: float = 0.0,
    ambiguous: bool = False,
    location_id: str = "",
) -> dict[str, Any]:
    thread = _d(thread)
    reply_to_beat = _d(reply_to_beat)
    candidate_ids = _dedupe(candidates)
    exact_turns = _exact_turns(thread) if thread else []
    return {
        "target_id": target_id,
        "target_name": target_name,
        "thread_id": _thread_id(thread),
        "reply_to_beat_id": _s(reply_to_beat.get("beat_id") or reply_to_beat.get("turn_id")),
        "resolution_source": source,
        "candidate_target_ids": candidate_ids,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "locked": bool(target_id and not ambiguous),
        "ambiguous": bool(ambiguous),
        "requires_clarification": bool(ambiguous or not target_id),
        "location_id": location_id,
        "dialogue_context": {
            "thread_id": _thread_id(thread),
            "participants": deepcopy(_l(thread.get("participants"))),
            "default_target_id": target_id or _s(thread.get("default_target_id")),
            "last_directed_beat_id": _s(reply_to_beat.get("beat_id") or reply_to_beat.get("turn_id")),
            "recent_turns": exact_turns,
        },
        "source": "deterministic_dialogue_focus_v1",
    }


def resolve_dialogue_target(
    *,
    player_input: str,
    simulation_state: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
    candidate_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a durable target without interpreting arbitrary utterance meaning."""

    sim = _d(simulation_state)
    runtime = _d(runtime_state)
    action = _d(candidate_action)
    catalog = _npc_catalog(sim, runtime)
    present_ids = _present_ids(sim, runtime)
    present = set(present_ids)
    location_id = _location_id(sim, runtime)
    tick = int(runtime.get("tick") or sim.get("tick") or 0)
    settings = _d(runtime.get("runtime_settings") or runtime.get("settings"))
    timeout_ticks = int(settings.get("dialogue_focus_timeout_ticks") or _DEFAULT_FOCUS_TIMEOUT_TICKS)

    named = _explicit_named_ids(player_input, catalog)
    named_valid = [npc_id for npc_id in named if not present or npc_id in present]
    if len(named_valid) == 1:
        npc_id = named_valid[0]
        return _resolution(
            target_id=npc_id,
            target_name=_s(catalog.get(npc_id, {}).get("name")),
            source="explicit_input",
            candidates=named_valid,
            confidence=1.0,
            location_id=location_id,
        )
    if len(named_valid) > 1:
        return _resolution(
            source="explicit_input_ambiguous",
            candidates=named_valid,
            ambiguous=True,
            location_id=location_id,
        )
    if named and not named_valid:
        return _resolution(
            source="explicit_target_absent",
            candidates=named,
            ambiguous=False,
            location_id=location_id,
        )

    parsed_target = _canonical_npc_id(
        action.get("target_id") or action.get("npc_id") or action.get("target") or action.get("target_name"),
        catalog,
    )
    if parsed_target and (not present or parsed_target in present):
        return _resolution(
            target_id=parsed_target,
            target_name=_s(catalog.get(parsed_target, {}).get("name")),
            source="parsed_action",
            candidates=[parsed_target],
            confidence=1.0,
            location_id=location_id,
        )

    state = _thread_state(sim, runtime)
    ambiguous_candidates: list[str] = []
    for thread in _active_threads(state, location_id=location_id, tick=tick, timeout_ticks=timeout_ticks):
        candidates, latest_directed = _thread_resolution_candidates(thread, catalog=catalog, present=present)
        if len(candidates) == 1:
            npc_id = candidates[0]
            return _resolution(
                target_id=npc_id,
                target_name=_s(catalog.get(npc_id, {}).get("name")),
                thread=thread,
                reply_to_beat=latest_directed,
                source="active_thread",
                candidates=candidates,
                confidence=1.0,
                location_id=location_id,
            )
        ambiguous_candidates.extend(candidates)

    ambiguous_candidates = _dedupe(ambiguous_candidates)
    if len(ambiguous_candidates) > 1:
        return _resolution(
            source="active_thread_ambiguous",
            candidates=ambiguous_candidates,
            ambiguous=True,
            location_id=location_id,
        )

    if len(present_ids) == 1:
        npc_id = present_ids[0]
        return _resolution(
            target_id=npc_id,
            target_name=_s(catalog.get(npc_id, {}).get("name")),
            source="sole_present_npc",
            candidates=[npc_id],
            confidence=0.8,
            location_id=location_id,
        )

    return _resolution(
        source="clarification_required",
        candidates=present_ids,
        ambiguous=len(present_ids) > 1,
        location_id=location_id,
    )


def _result_resolution(result: Mapping[str, Any]) -> dict[str, Any]:
    resolved = _d(result.get("resolved_result") or result.get("result"))
    direct = _d(result.get("dialogue_resolution") or resolved.get("dialogue_resolution"))
    if direct:
        return direct
    diagnostics = _d(result.get("first_call_grounding_diagnostics"))
    packet = _d(diagnostics.get("turn_grounding_packet"))
    return _d(_d(packet.get("priority_context")).get("dialogue_resolution"))


def _npc_line(result: Mapping[str, Any]) -> tuple[str, str, str]:
    sources = [
        _d(result.get("visible_response")),
        _d(result.get("canonical_visible_response")),
        _d(_d(result.get("result")).get("visible_response")),
    ]
    for visible in sources:
        npc = _d(visible.get("npc"))
        line = _clip(npc.get("line"), 900)
        if line:
            return _s(npc.get("speaker_id")), _s(npc.get("speaker")), line
    npc = _d(result.get("npc"))
    return _s(npc.get("speaker_id")), _s(npc.get("speaker")), _clip(npc.get("line"), 900)


def _resolved_target(result: Mapping[str, Any], catalog: Mapping[str, Mapping[str, str]]) -> tuple[str, str]:
    resolution = _result_resolution(result)
    resolved = _d(result.get("resolved_result") or result.get("result"))
    npc_id, speaker, _ = _npc_line(result)
    target_id = _canonical_npc_id(
        resolution.get("target_id")
        or resolved.get("target_id")
        or npc_id,
        catalog,
    )
    target_name = _s(
        resolution.get("target_name")
        or resolved.get("target_name")
        or speaker
        or catalog.get(target_id, {}).get("name")
    )
    return target_id, target_name


def _ensure_state(session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    simulation_state = _d(session.get("simulation_state"))
    runtime_state = _d(session.get("runtime_state"))
    state = _d(simulation_state.get("conversation_thread_state"))
    state.setdefault("threads", [])
    state.setdefault("active_thread_ids", [])
    state.setdefault("world_signals", [])
    state.setdefault("pending_player_response", {})
    state.setdefault("cooldowns", {})
    state.setdefault("debug", {})
    simulation_state["conversation_thread_state"] = state
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state
    return simulation_state, runtime_state, state


def _append_beat(thread: dict[str, Any], beat: dict[str, Any]) -> None:
    beats = [_d(row) for row in _l(thread.get("beats"))]
    beat_id = _s(beat.get("beat_id"))
    if beat_id and any(_s(row.get("beat_id")) == beat_id for row in beats):
        return
    beats.append(beat)
    thread["beats"] = beats[-_MAX_DIRECT_BEATS:]


def record_direct_dialogue_exchange(
    *,
    session: dict[str, Any],
    player_input: str,
    result: dict[str, Any],
    tick: int = 0,
    turn_id: str = "",
    persist: bool = False,
) -> dict[str, Any]:
    """Record the exact directed exchange and persist its derived focus."""

    if not isinstance(session, dict) or not isinstance(result, dict) or result.get("ok") is not True:
        return {"recorded": False, "reason": "invalid_dialogue_result"}
    simulation_state, runtime_state, state = _ensure_state(session)
    catalog = _npc_catalog(simulation_state, runtime_state)
    target_id, target_name = _resolved_target(result, catalog)
    _, visible_name, line = _npc_line(result)
    if not target_id or target_id.casefold() in _GENERIC_NPC_IDS:
        return {"recorded": False, "reason": "unresolved_dialogue_target"}
    target_name = target_name or visible_name or target_id.replace("npc:", "").replace("_", " ").title()

    resolution = _result_resolution(result)
    location_id = _location_id(simulation_state, runtime_state)
    scene_id = _scene_id(simulation_state, runtime_state)
    tick = int(tick or runtime_state.get("tick") or simulation_state.get("tick") or 0)
    turn_id = _s(turn_id or result.get("turn_id") or f"turn:{tick}")
    thread_id = _s(resolution.get("thread_id")) or f"conversation:{location_id or 'unknown'}:player:{target_id}"

    threads = [_d(row) for row in _l(state.get("threads"))]
    thread = next((row for row in threads if _thread_id(row) == thread_id), None)
    if thread is None:
        thread = {
            "thread_id": thread_id,
            "participants": [
                {"id": "player", "name": "Player"},
                {"id": target_id, "name": target_name},
            ],
            "location_id": location_id,
            "scene_id": scene_id,
            "beats": [],
            "created_tick": tick,
            "participation_mode": "direct",
            "source": "direct_dialogue_focus_v1",
        }
        threads.append(thread)
    thread["default_target_id"] = target_id
    thread["active_target_id"] = target_id
    thread["location_id"] = location_id or _s(thread.get("location_id"))
    thread["scene_id"] = scene_id or _s(thread.get("scene_id"))
    thread["updated_tick"] = tick
    thread["participation_mode"] = "direct"

    player_beat_id = f"conversation:player:{turn_id}:{target_id}"
    _append_beat(
        thread,
        {
            "beat_id": player_beat_id,
            "turn_id": turn_id,
            "thread_id": thread_id,
            "speaker_id": "player",
            "speaker_name": "Player",
            "listener_id": target_id,
            "listener_name": target_name,
            "target_id": target_id,
            "line": _clip(player_input, 700),
            "text": _clip(player_input, 700),
            "reply_to_beat_id": _s(resolution.get("reply_to_beat_id")),
            "scene_id": scene_id,
            "location_id": location_id,
            "tick": tick,
            "participation_mode": "direct",
            "source": "direct_dialogue_focus_v1",
        },
    )
    npc_beat_id = ""
    if line:
        npc_beat_id = f"conversation:npc:{turn_id}:{target_id}"
        _append_beat(
            thread,
            {
                "beat_id": npc_beat_id,
                "turn_id": turn_id,
                "thread_id": thread_id,
                "speaker_id": target_id,
                "speaker_name": target_name,
                "listener_id": "player",
                "listener_name": "Player",
                "target_id": "player",
                "line": line,
                "text": line,
                "scene_id": scene_id,
                "location_id": location_id,
                "tick": tick,
                "participation_mode": "direct",
                "source": "direct_dialogue_focus_v1",
            },
        )

    state["threads"] = threads[-_MAX_THREADS:]
    active_ids = [value for value in _l(state.get("active_thread_ids")) if _s(value) != thread_id]
    active_ids.append(thread_id)
    state["active_thread_ids"] = active_ids[-_MAX_THREADS:]
    state["active_dialogue_thread_id"] = thread_id
    state["active_dialogue_target_id"] = target_id
    state["debug"] = {
        "last_direct_dialogue_recorded": True,
        "thread_id": thread_id,
        "target_id": target_id,
        "turn_id": turn_id,
        "tick": tick,
        "source": "direct_dialogue_focus_v1",
    }

    persisted_resolution = {
        **resolution,
        "target_id": target_id,
        "target_name": target_name,
        "thread_id": thread_id,
        "reply_to_beat_id": _s(resolution.get("reply_to_beat_id")),
        "resolution_source": _s(resolution.get("resolution_source") or "resolved_turn_contract"),
        "candidate_target_ids": _dedupe(resolution.get("candidate_target_ids") or [target_id]),
        "confidence": float(resolution.get("confidence") or 1.0),
        "locked": True,
        "ambiguous": False,
        "requires_clarification": False,
        "source": "deterministic_dialogue_focus_v1",
    }
    runtime_state["dialogue_focus"] = deepcopy(persisted_resolution)
    runtime_state["conversation_thread_state"] = deepcopy(state)
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state

    result["dialogue_resolution"] = deepcopy(persisted_resolution)
    resolved = _d(result.get("resolved_result") or result.get("result"))
    if resolved:
        resolved["target_id"] = target_id
        resolved["target_name"] = target_name
        resolved["dialogue_resolution"] = deepcopy(persisted_resolution)
        if isinstance(result.get("resolved_result"), Mapping):
            result["resolved_result"] = resolved
        if isinstance(result.get("result"), Mapping):
            result["result"] = deepcopy(resolved)
    result["conversation_thread_record"] = {
        "recorded": True,
        "thread_id": thread_id,
        "player_beat_id": player_beat_id,
        "npc_beat_id": npc_beat_id,
        "target_id": target_id,
        "source": "direct_dialogue_focus_v1",
    }
    result["session"] = deepcopy(session)
    result["simulation_state"] = deepcopy(simulation_state)
    result["runtime_state"] = deepcopy(runtime_state)

    persisted = False
    persist_error = ""
    if persist:
        try:
            from app.apps.rpg.session.session_runtime_store import save_runtime_session

            save_runtime_session(session)
            persisted = True
        except Exception as exc:  # pragma: no cover - persistence is best effort here
            persist_error = f"{type(exc).__name__}: {exc}"
    result["conversation_thread_record"]["persisted"] = persisted
    if persist_error:
        result["conversation_thread_record"]["persist_error"] = persist_error
    return deepcopy(result["conversation_thread_record"])
