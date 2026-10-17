from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from app.apps.rpg.session.dialogue_focus import resolve_dialogue_target
from app.apps.rpg.narration.memory_prompt import build_relevant_memory_context_from_runtime


def _d(v: Any) -> Dict[str, Any]:
    return dict(v) if isinstance(v, dict) else {}


def _l(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _s(v: Any) -> str:
    return str(v) if v is not None else ""


def _clip(v: Any, n: int = 600) -> str:
    return _s(v).strip()[:n]


def _norm(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _s(v).casefold()).strip()


def _dedupe(values: List[Any], limit: int = 8) -> List[str]:
    out, seen = [], set()
    for value in values:
        text = _s(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _npc_id(key: str, npc: Dict[str, Any]) -> str:
    return _s(npc.get("id") or npc.get("npc_id") or npc.get("actor_id") or key).strip()


def _npc_name(npc_id: str, npc: Dict[str, Any]) -> str:
    return _s(npc.get("name") or npc.get("title") or npc_id).strip()


def _scene(sim: Dict[str, Any], rt: Dict[str, Any]) -> Dict[str, Any]:
    return _d(
        rt.get("current_scene")
        or rt.get("grounded_scene_context")
        or sim.get("current_scene")
        or sim.get("scene")
    )


def _iter_npc_sources(src: Dict[str, Any]) -> List[Any]:
    values: List[Any] = []
    for key in ("npc_index", "npcs", "known_npcs", "nearby_npcs", "characters"):
        values.append(src.get(key))

    present_state = _d(src.get("present_npc_state"))
    for key in ("npc_index", "npcs", "present_npcs", "nearby_npcs", "characters"):
        values.append(present_state.get(key))

    social_state = _d(src.get("social_state"))
    values.append(social_state.get("profiles"))
    return values


def _npcs(sim: Dict[str, Any], rt: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows, seen = [], set()

    def add(key: str, raw: Any) -> None:
        npc = _d(raw)
        npc_id = _npc_id(key, npc)
        if not npc_id or npc_id.casefold() in seen:
            return
        seen.add(npc_id.casefold())
        npc = dict(npc)
        npc.setdefault("id", npc_id)
        npc.setdefault("npc_id", npc_id)
        rows.append(npc)

    for src in (sim, rt):
        for value in _iter_npc_sources(src):
            if isinstance(value, dict):
                for npc_id, npc in value.items():
                    add(_s(npc_id), npc)
            elif isinstance(value, list):
                for npc in value:
                    add("", npc)
    rows.sort(key=lambda x: (_s(x.get("location_id")), _s(x.get("name")), _s(x.get("id"))))
    return rows


def _present_ids(sim: Dict[str, Any], rt: Dict[str, Any], scene: Dict[str, Any]) -> List[str]:
    player = _d(sim.get("player_state"))
    present_state = _d(sim.get("present_npc_state"))
    ids = []
    for source in (scene, player, rt, present_state):
        ids += _l(source.get("present_npc_ids")) + _l(source.get("nearby_npc_ids"))
    return _dedupe(ids, 12)


def _traits(npc: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    vals = []
    for src in (npc, profile, _d(npc.get("personality")), _d(profile.get("personality"))):
        vals += _l(src.get("traits")) + _l(src.get("values")) + _l(src.get("fears"))
    return _dedupe(vals, 12)


def _bio(npc: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, str]:
    nb, pb = _d(npc.get("biography")), _d(profile.get("biography"))
    public = pb.get("public") or pb.get("summary") or pb.get("text") or nb.get("public") or nb.get("summary") or nb.get("text") or npc.get("biography") or npc.get("backstory") or npc.get("description") or npc.get("summary")
    private = pb.get("private") or pb.get("secrets") or nb.get("private") or npc.get("secret")
    return {"public": _clip(public, 1200), "private": _clip(private, 800)}


def _personality(npc: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    np, pp = _d(npc.get("personality")), _d(profile.get("personality"))
    summary = pp.get("summary") or pp.get("profile") or pp.get("description") or np.get("summary") or np.get("profile") or np.get("description") or npc.get("personality_summary") or npc.get("personality")
    style = pp.get("speech_style") or pp.get("voice") or pp.get("dialogue_style") or np.get("speech_style") or np.get("voice") or np.get("dialogue_style") or npc.get("speech_style") or npc.get("voice")
    examples = _dedupe(_l(pp.get("speech_examples")) + _l(pp.get("dialogue_examples")) + _l(np.get("speech_examples")) + _l(np.get("dialogue_examples")) + _l(npc.get("speech_examples")), 5)
    traits = _traits(npc, profile)
    if not summary and traits:
        summary = "This NPC is characterized by: " + ", ".join(traits[:8]) + "."
    return {"summary": _clip(summary, 900), "traits": traits, "values": _dedupe(_l(pp.get("values")) + _l(np.get("values")), 8), "fears": _dedupe(_l(pp.get("fears")) + _l(np.get("fears")), 8), "social_style": _clip(style, 600), "speech_examples": [_clip(x, 180) for x in examples]}


def _inventory(npc: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    inv = _d(npc.get("inventory")) or _d(profile.get("inventory"))
    return {"visible": [_clip(x, 120) for x in (_l(inv.get("visible")) or _l(npc.get("visible_inventory")))[:12]], "equipped": [_clip(x, 120) for x in (_l(inv.get("equipped")) or _l(npc.get("equipped")))[:8]], "merchant_offers_if_applicable": (_l(npc.get("merchant_offers")) or _l(npc.get("offers")))[:12], "private": [_clip(x, 120) for x in (_l(inv.get("private")) or _l(npc.get("private_inventory")))[:8]]}


def _relationship(npc_id: str, sim: Dict[str, Any], rt: Dict[str, Any]) -> Dict[str, Any]:
    rel = _d(sim.get("relationships") or rt.get("relationships"))
    return _d(rel.get(npc_id)) or _d(_d(_d(sim.get("social_state")).get("relationships")).get(npc_id))


def _knowledge(npc: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    kb = _d(profile.get("knowledge_boundaries")) or _d(npc.get("knowledge_boundaries"))
    return {"publicly_knows": [_clip(x, 140) for x in _l(kb.get("publicly_knows"))[:10]], "may_discuss": [_clip(x, 140) for x in _l(kb.get("may_discuss"))[:10]], "does_not_know": [_clip(x, 140) for x in _l(kb.get("does_not_know"))[:10]], "must_not_reveal": [_clip(x, 140) for x in _l(kb.get("must_not_reveal"))[:10]]}


def _rich_profile(npc_id: str, npc: Dict[str, Any], sim: Dict[str, Any], rt: Dict[str, Any], loader: Callable[[str], Dict[str, Any] | None] | None) -> Dict[str, Any]:
    profile: Dict[str, Any] = {}
    if loader:
        try:
            profile = _d(loader(npc_id))
        except Exception:
            profile = {}
    bio = _bio(npc, profile)
    personality = _personality(npc, profile)
    caps = _d(profile.get("capabilities")) or _d(npc.get("capabilities"))
    return {
        "id": npc_id,
        "name": _npc_name(npc_id, npc),
        "role": _clip(npc.get("role") or profile.get("role"), 120),
        "location_id": _s(npc.get("location_id") or npc.get("current_location_id")),
        "visible_profile": {"short_description": _clip(npc.get("description") or npc.get("summary"), 300), "public_biography": bio["public"], "current_role": _clip(npc.get("role") or profile.get("role"), 120), "speech_style": personality["social_style"], "visible_mood": _clip(npc.get("mood") or npc.get("visible_mood"), 120)},
        "biography": bio,
        "personality_profile": personality,
        "relationship_to_player": _relationship(npc_id, sim, rt),
        "capabilities": {"combat_style": _clip(caps.get("combat_style") or npc.get("combat_style"), 180), "skills": [_clip(x, 80) for x in (_l(caps.get("skills")) or _l(npc.get("skills")))[:12]], "role": _clip(npc.get("role") or profile.get("role"), 120)},
        "inventory": _inventory(npc, profile),
        "knowledge_boundaries": _knowledge(npc, profile),
        "party_status": _d(npc.get("party_status")),
        "source": "grounded_turn_packet_v1",
    }


def nearby_npc_profiles(*, simulation_state: dict[str, Any], runtime_state: dict[str, Any], npc_ids: list[str]) -> list[dict[str, Any]]:
    """Grounded profiles of the given NPCs, in order; ids the session does not know are skipped."""
    sim, rt = _d(simulation_state), _d(runtime_state)
    by_id = {_npc_id("", npc): npc for npc in _npcs(sim, rt)}
    return [_rich_profile(npc_id, by_id[npc_id], sim, rt, None) for npc_id in npc_ids if npc_id in by_id]


def _referenced_npcs(player_input: str, npcs: List[Dict[str, Any]]) -> List[str]:
    text = _norm(player_input)
    out = []
    for npc in npcs:
        npc_id = _npc_id("", npc)
        tokens = [_norm(_npc_name(npc_id, npc)), _norm(npc_id.replace("npc:", ""))]
        if any(t and t in text for t in tokens):
            out.append(npc_id)
    return _dedupe(out, 3)


def _addressed(referenced: List[str], present: List[str]) -> List[str]:
    if referenced:
        return _dedupe(
            [npc_id for npc_id in referenced if not present or npc_id in present],
            3,
        )
    return list(present) if len(present) == 1 else []


def _recent(rt: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{"player_input": _clip(_d(x).get("player_input") or _d(x).get("input"), 180), "summary": _clip(_d(x).get("summary") or _d(x).get("narration") or _d(x).get("result"), 220)} for x in _l(rt.get("turn_history"))[-6:]]


def build_turn_grounding_packet(*, player_input: str, simulation_state: Dict[str, Any], runtime_state: Dict[str, Any], candidate_action: Dict[str, Any] | None = None, profile_loader: Callable[[str], Dict[str, Any] | None] | None = None) -> Dict[str, Any]:
    sim, rt, cand = _d(simulation_state), _d(runtime_state), _d(candidate_action)
    player = _d(sim.get("player_state"))
    scene = _scene(sim, rt)
    all_npcs = _npcs(sim, rt)
    present = _present_ids(sim, rt, scene)
    referenced = _referenced_npcs(player_input, all_npcs)
    dialogue_resolution = resolve_dialogue_target(
        player_input=player_input,
        simulation_state=sim,
        runtime_state=rt,
        candidate_action=cand,
    )
    locked_target_id = _s(dialogue_resolution.get("target_id")) if dialogue_resolution.get("locked") else ""
    addressed = [locked_target_id] if locked_target_id else _addressed(referenced, present)
    referenced_absent = [npc_id for npc_id in referenced if present and npc_id not in present]
    by_id = {_npc_id("", npc): npc for npc in all_npcs}
    addressed_profiles = [_rich_profile(npc_id, by_id.get(npc_id, {}), sim, rt, profile_loader) for npc_id in addressed if npc_id]
    nearby = []
    for npc in all_npcs:
        npc_id = _npc_id("", npc)
        if present and npc_id not in present:
            continue
        nearby.append({"id": npc_id, "name": _npc_name(npc_id, npc), "role": _clip(npc.get("role"), 100), "location_id": _s(npc.get("location_id") or npc.get("current_location_id"))})
        if len(nearby) >= 8:
            break
    inv = _d(player.get("inventory_state"))
    combat = _d(rt.get("combat_state") or sim.get("combat_state"))
    relevant_memory = build_relevant_memory_context_from_runtime(
        rt,
        player_input=player_input,
        actor_ids=addressed,
        location_id=scene.get("location_id") or player.get("location_id"),
    )
    dialogue_context = _d(dialogue_resolution.get("dialogue_context"))
    exact_recent_turns = [_d(row) for row in _l(dialogue_context.get("recent_turns")) if _d(row)]
    recent_turns = exact_recent_turns[-8:] or _recent(rt)
    return {
        "format_version": "turn_grounding_packet_v1",
        "source": "deterministic_runtime_context",
        "player_input": _clip(player_input, 500),
        "candidate_action": {"action_type": _s(cand.get("action_type")), "target_id": _s(cand.get("target_id")), "target_name": _s(cand.get("target_name")), "npc_id": _s(cand.get("npc_id")), "item_id": _s(cand.get("item_id"))},
        "priority_context": {
            "current_scene": {"scene_id": _s(scene.get("scene_id")), "location_id": _s(scene.get("location_id")), "location_name": _clip(scene.get("location_name") or scene.get("title"), 120), "summary": _clip(scene.get("summary") or scene.get("scene"), 500), "present_npc_ids": present},
            "active_modes": {"combat_active": bool(combat.get("active")), "commerce_available": bool(_l(rt.get("transaction_menus")) or _l(rt.get("active_transaction_menus"))), "active_interaction_count": len(_l(sim.get("active_interactions"))), "narration_mode": _s(rt.get("narration_mode") or _d(rt.get("settings")).get("narration_mode"))},
            "addressed_npc_ids": addressed,
            "referenced_absent_npc_ids": referenced_absent,
            "dialogue_resolution": dialogue_resolution,
            "dialogue_context": dialogue_context,
            "recent_turns": recent_turns,
        },
        "authoritative_state": {"player": {"location_id": _s(player.get("location_id")), "stats": _d(player.get("stats")), "skills": _d(player.get("skills")), "currency": _d(inv.get("currency")), "inventory_items": _l(inv.get("items"))[:20]}, "combat": combat, "active_interactions": _l(sim.get("active_interactions"))[:8], "quests": _d(sim.get("quest_state") or rt.get("quest_state"))},
        "npc_context": {"addressed_npcs": addressed_profiles, "nearby_npcs": nearby},
        "relevant_memory": relevant_memory,
        "private_context": {"note": "Private biography/inventory fields are for adjudication only. Do not reveal them unless runtime exposes them.", "addressed_npc_private_fields_present": [{"id": _s(p.get("id")), "has_private_biography": bool(_s(_d(p.get("biography")).get("private"))), "has_private_inventory": bool(_l(_d(p.get("inventory")).get("private")))} for p in addressed_profiles]},
        "rules": {"runtime_state_overrides_memory": True, "dialogue_target_lock_is_authoritative": bool(dialogue_resolution.get("locked")), "first_llm_may_classify_intent": True, "first_llm_may_answer_non_stateful_interpretive_dialogue": True, "first_llm_must_not_resolve_stateful_outcomes": True, "stateful_actions_require_deterministic_runtime": True, "do_not_reveal_private_context": True},
    }
