from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _keys_changed(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    all_keys = sorted(set(before.keys()) | set(after.keys()))
    changed = []
    for key in all_keys:
        if before.get(key) != after.get(key):
            changed.append(str(key))
    return changed


def build_tick_diff(before_state: Dict[str, Any], after_state: Dict[str, Any]) -> Dict[str, Any]:
    before_state = _safe_dict(before_state)
    after_state = _safe_dict(after_state)

    before_events = _safe_list(before_state.get("events"))
    after_events = _safe_list(after_state.get("events"))
    before_consequences = _safe_list(before_state.get("consequences"))
    after_consequences = _safe_list(after_state.get("consequences"))

    before_social = _safe_dict(before_state.get("social_state"))
    after_social = _safe_dict(after_state.get("social_state"))
    before_sandbox = _safe_dict(before_state.get("sandbox_state"))
    after_sandbox = _safe_dict(after_state.get("sandbox_state"))

    before_npc_minds = _safe_dict(before_state.get("npc_minds"))
    after_npc_minds = _safe_dict(after_state.get("npc_minds"))

    before_player_state = _safe_dict(before_state.get("player_state"))
    after_player_state = _safe_dict(after_state.get("player_state"))
    before_inventory = _safe_dict(before_player_state.get("inventory_state"))
    after_inventory = _safe_dict(after_player_state.get("inventory_state"))
    before_party = _safe_dict(before_player_state.get("party_state"))
    after_party = _safe_dict(after_player_state.get("party_state"))

    before_narrative = _safe_dict(before_party.get("narrative_state"))
    after_narrative = _safe_dict(after_party.get("narrative_state"))

    changed_npcs = []
    for npc_id in sorted(set(before_npc_minds.keys()) | set(after_npc_minds.keys())):
        if before_npc_minds.get(npc_id) != after_npc_minds.get(npc_id):
            changed_npcs.append(npc_id)

    before_event_id_set = set()
    has_event_ids = False
    for e in before_events:
        if isinstance(e, dict) and e.get("id") is not None:
            before_event_id_set.add(str(e.get("id")))
            has_event_ids = True

    before_consequence_id_set = set()
    has_consequence_ids = False
    for c in before_consequences:
        if isinstance(c, dict) and c.get("id") is not None:
            before_consequence_id_set.add(str(c.get("id")))
            has_consequence_ids = True

    new_events = []
    for i, item in enumerate(after_events):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if has_event_ids and item_id is not None:
            if str(item_id) not in before_event_id_set:
                new_events.append(dict(item))
        elif i >= len(before_events):
            new_events.append(dict(item))

    new_consequences = []
    for i, item in enumerate(after_consequences):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if has_consequence_ids and item_id is not None:
            if str(item_id) not in before_consequence_id_set:
                new_consequences.append(dict(item))
        elif i >= len(before_consequences):
            new_consequences.append(dict(item))

    return {
        "tick_before": _safe_int(before_state.get("tick"), 0),
        "tick_after": _safe_int(after_state.get("tick"), 0),
        "new_events": new_events[:20],
        "new_consequences": new_consequences[:20],
        "social_keys_changed": _keys_changed(before_social, after_social),
        "sandbox_keys_changed": _keys_changed(before_sandbox, after_sandbox),
        "player_keys_changed": _keys_changed(before_player_state, after_player_state),
        "inventory_keys_changed": _keys_changed(before_inventory, after_inventory),
        "inventory_before": before_inventory,
        "inventory_after": after_inventory,
        "party_keys_changed": _keys_changed(before_party, after_party),
        "party_narrative_keys_changed": _keys_changed(before_narrative, after_narrative),
        "party_narrative_before": before_narrative,
        "party_narrative_after": after_narrative,
        "party_before": before_party,
        "party_after": after_party,
        "changed_npc_ids": changed_npcs[:20],
        # Conversation diff fields
        "new_conversations": _new_conversations(before_state, after_state),
        "closed_conversations": _closed_conversations(before_state, after_state),
        "new_conversation_lines": _new_conversation_lines(before_state, after_state),
        "conversation_interventions": _conversation_interventions(before_state, after_state),
        "summary": {
            "event_delta": len(after_events) - len(before_events),
            "consequence_delta": len(after_consequences) - len(before_consequences),
            "npc_changes": len(changed_npcs),
            "inventory_item_kinds_before": len(_safe_list(before_inventory.get("items"))),
            "inventory_item_kinds_after": len(_safe_list(after_inventory.get("items"))),
            "party_size_before": len(_safe_list(before_party.get("companions"))),
            "party_size_after": len(_safe_list(after_party.get("companions"))),
            "party_narrative_history_before": len(_safe_list(before_narrative.get("history"))),
            "party_narrative_history_after": len(_safe_list(after_narrative.get("history"))),
        },
    }


def _conv_state(sim: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(_safe_dict(_safe_dict(sim).get("social_state")).get("conversations"))


def _new_conversations(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    before_ids = {str(_safe_dict(c).get("conversation_id")) for c in _safe_list(_conv_state(before).get("active"))}
    out: List[Dict[str, Any]] = []
    for c in _safe_list(_conv_state(after).get("active")):
        c = _safe_dict(c)
        cid = str(c.get("conversation_id", ""))
        if cid and cid not in before_ids:
            out.append(dict(c))
    return out[:10]


def _closed_conversations(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    before_recent_ids = {str(_safe_dict(c).get("conversation_id")) for c in _safe_list(_conv_state(before).get("recent"))}
    out: List[Dict[str, Any]] = []
    for c in _safe_list(_conv_state(after).get("recent")):
        c = _safe_dict(c)
        cid = str(c.get("conversation_id", ""))
        if cid and cid not in before_recent_ids:
            out.append(dict(c))
    return out[:10]


def _new_conversation_lines(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    before_lines = _safe_dict(_conv_state(before).get("lines_by_conversation"))
    after_lines = _safe_dict(_conv_state(after).get("lines_by_conversation"))
    out: List[Dict[str, Any]] = []
    for cid, after_rows in sorted(after_lines.items()):
        before_count = len(_safe_list(before_lines.get(cid)))
        for line in _safe_list(after_rows)[before_count:]:
            if isinstance(line, dict):
                out.append(dict(line))
    return out[:20]


def _conversation_interventions(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    before_rt = _safe_dict(before.get("runtime_state"))
    after_rt = _safe_dict(after.get("runtime_state"))
    before_int = _safe_dict(before_rt.get("last_conversation_intervention"))
    after_int = _safe_dict(after_rt.get("last_conversation_intervention"))
    if after_int and after_int != before_int:
        return [dict(after_int)]
    return []
