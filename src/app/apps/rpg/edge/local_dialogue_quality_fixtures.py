"""Known-state fixtures for the local-only RPG dialogue quality smoke."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.edge.dialogue_quality_benchmark import (
    BRAN_PROFILE,
    DialogueBenchmarkCase,
    default_dialogue_benchmark_cases,
)
from app.apps.rpg.session.new_game import RpgNewGameRequest, create_new_game_session
from app.apps.rpg.session.service import load_session, save_session

LOCAL_DIALOGUE_FIXTURE_VERSION = "rpg_local_dialogue_fixture_v1"

MIRA_PROFILE = {
    "id": "npc:mira",
    "npc_id": "npc:mira",
    "name": "Mira",
    "role": "road scout and caravan tracker",
    "biography": {
        "public": (
            "Mira scouts the old road for caravan traffic and can read fresh wagon "
            "tracks around the quarry turnoff."
        ),
    },
    "personality": {
        "summary": "Observant, economical with words, and careful about claims.",
        "values": ["evidence", "safe roads", "keeping travelers alive"],
        "speech_style": "Brief field observations grounded in tracks, weather, and road signs.",
    },
}


def dialogue_benchmark_case(case_id: str) -> DialogueBenchmarkCase:
    normalized = str(case_id or "").strip()
    for case in default_dialogue_benchmark_cases():
        if case.case_id == normalized:
            return case
    raise ValueError(f"unknown_dialogue_benchmark_case:{normalized}")


def provision_local_dialogue_fixture(
    *,
    case: DialogueBenchmarkCase,
    run_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create or reset one disposable session to a known benchmark case."""

    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("dialogue_fixture_run_id_required")

    created = False
    if session_id:
        session = load_session(session_id)
        if not session:
            raise ValueError(f"dialogue_fixture_session_not_found:{session_id}")
        fixture = _dict(_dict(session.get("runtime_state")).get("local_dialogue_quality_fixture"))
        if fixture.get("run_id") != normalized_run_id:
            raise ValueError("dialogue_fixture_run_id_mismatch")
    else:
        result = create_new_game_session(
            RpgNewGameRequest.model_validate(
                {
                    "seed": 18041,
                    "starting_location": "rusty_flagon_tavern",
                    "player": {"name": "Elara"},
                }
            )
        )
        session = _dict(result.get("session"))
        session_id = str(result.get("session_id") or "").strip()
        if not session or not session_id:
            raise RuntimeError("dialogue_fixture_session_creation_failed")
        created = True

    applied = apply_local_dialogue_fixture(
        session,
        case=case,
        run_id=normalized_run_id,
    )
    saved = save_session(applied, compact=True)
    manifest = _dict(saved.get("manifest"))
    resolved_session_id = str(manifest.get("session_id") or manifest.get("id") or session_id or "")
    return {
        "ok": True,
        "fixture_version": LOCAL_DIALOGUE_FIXTURE_VERSION,
        "case_id": case.case_id,
        "run_id": normalized_run_id,
        "session_id": resolved_session_id,
        "created": created,
    }


def apply_local_dialogue_fixture(
    session: dict[str, Any],
    *,
    case: DialogueBenchmarkCase,
    run_id: str,
) -> dict[str, Any]:
    """Reset dialogue-relevant state without accepting arbitrary fixture payloads."""

    session = deepcopy(session)
    manifest = _dict(session.get("manifest"))
    manifest["title"] = f"Local dialogue qualification: {case.case_id}"
    manifest["archived"] = False
    session["manifest"] = manifest

    state = _dict(session.get("state"))
    state["location"] = "Rusty Flagon Tavern"
    state["current_location"] = "Rusty Flagon Tavern"
    state["current_turn"] = 0
    state["turn_count"] = 0
    player = _dict(state.get("player"))
    player["name"] = "Elara"
    state["player"] = player
    trust_score = {"low": -20, "neutral": 0, "high": 75}.get(case.trust, 0)
    state["relationships"] = [
        {
            "id": "npc:bran",
            "npc_id": "npc:bran",
            "name": "Bran",
            "stance": case.trust,
            "score": trust_score,
            "trust": case.trust,
        }
    ]
    state["relationship_index"] = {
        "npc:bran": {"trust": case.trust, "score": trust_score},
    }
    session["state"] = state

    simulation = _dict(session.get("simulation_state"))
    location_id = _current_location_id(simulation)
    present_ids = _present_npc_ids(case)
    npc_index = _dict(simulation.get("npc_index"))
    npc_index["npc:bran"] = deepcopy(BRAN_PROFILE)
    npc_index["npc:mira"] = deepcopy(MIRA_PROFILE)
    for npc_id, profile in npc_index.items():
        if isinstance(profile, dict):
            profile["location_id"] = location_id if npc_id in present_ids else "location:offstage"
    simulation["npc_index"] = npc_index
    simulation["npcs"] = deepcopy(npc_index)
    simulation["relationships"] = {
        "npc:bran": {
            "trust": trust_score / 100.0,
            "trust_label": case.trust,
        }
    }
    simulation["npc_minds"] = {
        "npc:bran": {"beliefs": {"player": {"trust": trust_score / 100.0}}},
        "npc:mira": {"beliefs": {"player": {"trust": 0.0}}},
    }
    scene = _dict(simulation.get("scene"))
    scene.update(
        {
            "location_name": "Rusty Flagon Tavern",
            "nearby_npcs": [_presence_row(npc_id, npc_index) for npc_id in present_ids],
            "npcs": [_presence_row(npc_id, npc_index) for npc_id in present_ids],
            "present_npc_ids": list(present_ids),
        }
    )
    simulation["scene"] = scene
    player_state = _dict(simulation.get("player_state"))
    player_state["nearby_npc_ids"] = list(present_ids)
    simulation["player_state"] = player_state
    present_state = _dict(simulation.get("present_npc_state"))
    present_state[location_id] = list(present_ids)
    present_state["debug"] = {
        "location_id": location_id,
        "present_npcs": list(present_ids),
        "source": LOCAL_DIALOGUE_FIXTURE_VERSION,
    }
    simulation["present_npc_state"] = present_state
    session["simulation_state"] = simulation

    recent = [
        {
            "player_input": "Earlier question",
            "npc_line": line,
            "visible_response": {
                "messages": [
                    {
                        "kind": "npc_dialogue",
                        "speaker_id": "npc:bran",
                        "speaker": "Bran",
                        "text": line,
                    }
                ],
            },
        }
        for line in case.recent_lines
    ]
    runtime = _dict(session.get("runtime_state"))
    for key in (
        "turn_history",
        "narration_artifacts_by_turn",
        "narrative_replays",
        "interaction_history",
        "last_turn_contract",
        "last_turn_result",
    ):
        runtime.pop(key, None)
    runtime["tick"] = 0
    runtime["recent_interactions"] = recent
    runtime["current_scene"] = {
        "scene_id": str(scene.get("scene_id") or location_id),
        "location_id": location_id,
        "location_name": "Rusty Flagon Tavern",
        "present_npc_ids": list(present_ids),
        "nearby_npcs": [
            _presence_row(npc_id, npc_index) for npc_id in present_ids
        ],
    }
    runtime.pop("scene", None)
    runtime["local_dialogue_quality_fixture"] = {
        "fixture_version": LOCAL_DIALOGUE_FIXTURE_VERSION,
        "run_id": run_id,
        "case_id": case.case_id,
        "category": case.category,
        "trust": case.trust,
        "present_npc_ids": list(present_ids),
        "recent_line_count": len(recent),
    }
    session["runtime_state"] = runtime
    return session


def _present_npc_ids(case: DialogueBenchmarkCase) -> tuple[str, ...]:
    if case.absent_target:
        return ("npc:mira",)
    if case.category == "group_conversation":
        return ("npc:bran", "npc:mira")
    return ("npc:bran",)


def _presence_row(npc_id: str, npc_index: dict[str, Any]) -> dict[str, Any]:
    profile = _dict(npc_index.get(npc_id))
    return {
        "npc_id": npc_id,
        "id": npc_id,
        "name": str(profile.get("name") or npc_id),
        "role": str(profile.get("role") or ""),
    }


def _current_location_id(simulation: dict[str, Any]) -> str:
    player_state = _dict(simulation.get("player_state"))
    world = _dict(simulation.get("world"))
    return str(
        player_state.get("location_id")
        or world.get("current_location_id")
        or simulation.get("current_location_id")
        or "location:rusty_flagon_tavern"
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
