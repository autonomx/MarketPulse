"""Phase 2 — World Graph + Simulation Inspector backend.

Provides deterministic, read-only graph building, simulation summary
computation, and per-entity inspector maps from an adventure setup payload.

All functions accept a raw ``setup_payload`` dict (the same shape the
creator routes already traffic) and return plain dicts suitable for
JSON serialization.
"""

from __future__ import annotations

from typing import Any, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

# ---------------------------------------------------------------------------
# Node / edge type constants
# ---------------------------------------------------------------------------

NODE_TYPES = ("faction", "npc", "location", "thread", "opening")

EDGE_TYPES = (
    "member_of",
    "located_in",
    "involves",
    "pressures",
    "connected_to",
    "starts_at",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _thread_related_ids(thread: dict[str, Any]) -> dict[str, list[str]]:
    """Extract directly referenced ids from a thread payload."""
    thread = _safe_dict(thread)
    return {
        "npc_ids": [x for x in _safe_list(thread.get("npc_ids")) if x],
        "faction_ids": [x for x in _safe_list(thread.get("faction_ids")) if x],
        "location_ids": [x for x in _safe_list(thread.get("location_ids")) if x],
    }


def _infer_thread_links(
    thread: dict[str, Any],
    npc_by_id: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Infer missing thread links through linked NPC faction/location."""
    direct = _thread_related_ids(thread)
    factions = set(direct["faction_ids"])
    locations = set(direct["location_ids"])
    npcs = set(direct["npc_ids"])

    for npc_id in list(npcs):
        npc = _safe_dict(npc_by_id.get(npc_id))
        faction_id = npc.get("faction_id")
        location_id = npc.get("location_id")
        if faction_id:
            factions.add(faction_id)
        if location_id:
            locations.add(location_id)

    return {
        "npc_ids": sorted(npcs),
        "faction_ids": sorted(factions),
        "location_ids": sorted(locations),
    }


def _make_node(node_id: str, node_type: str, label: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "meta": meta or {},
    }


def _make_edge(source: str, target: str, edge_type: str) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "type": edge_type,
    }


# ---------------------------------------------------------------------------
# build_world_graph
# ---------------------------------------------------------------------------


def build_world_graph(setup_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a graph of nodes and edges from the adventure setup.

    Returns ``{"nodes": [...], "edges": [...]}``.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    # --- Factions ---
    for fac in _safe_list(setup_payload.get("factions")):
        fac = _safe_dict(fac)
        fid = _safe_str(fac.get("faction_id"))
        if not fid:
            continue
        nodes.append(_make_node(fid, "faction", _safe_str(fac.get("name")) or fid, {
            "description": _safe_str(fac.get("description")),
            "goals": _safe_list(fac.get("goals")),
        }))
        node_ids.add(fid)

    # --- Locations ---
    for loc in _safe_list(setup_payload.get("locations")):
        loc = _safe_dict(loc)
        lid = _safe_str(loc.get("location_id"))
        if not lid:
            continue
        nodes.append(_make_node(lid, "location", _safe_str(loc.get("name")) or lid, {
            "description": _safe_str(loc.get("description")),
            "tags": _safe_list(loc.get("tags")),
        }))
        node_ids.add(lid)

    # --- NPCs ---
    for npc in _safe_list(setup_payload.get("npc_seeds")):
        npc = _safe_dict(npc)
        nid = _safe_str(npc.get("npc_id"))
        if not nid:
            continue
        faction_id = _safe_str(npc.get("faction_id"))
        location_id = _safe_str(npc.get("location_id"))
        nodes.append(_make_node(nid, "npc", _safe_str(npc.get("name")) or nid, {
            "role": _safe_str(npc.get("role")),
            "faction_id": faction_id,
            "location_id": location_id,
            "goals": _safe_list(npc.get("goals")),
        }))
        node_ids.add(nid)

        # NPC → faction edge
        if faction_id and faction_id in node_ids:
            edges.append(_make_edge(nid, faction_id, "member_of"))

        # NPC → location edge
        if location_id and location_id in node_ids:
            edges.append(_make_edge(nid, location_id, "located_in"))

    # --- Threads ---
    metadata = _safe_dict(setup_payload.get("metadata"))
    threads = _safe_list(metadata.get("regenerated_threads"))
    npc_by_id = {npc.get("npc_id"): npc for npc in _safe_list(setup_payload.get("npc_seeds")) if npc.get("npc_id")}
    for idx, thread in enumerate(threads):
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id")) or f"thread_{idx}"
        label = _safe_str(thread.get("title")) or _safe_str(thread.get("label")) or f"Thread {idx + 1}"
        nodes.append(_make_node(tid, "thread", label, {
            "description": _safe_str(thread.get("description")),
            "involved_entities": _safe_list(thread.get("involved_entities")),
        }))
        node_ids.add(tid)

        inferred = _infer_thread_links(thread, npc_by_id)

        # Thread → referenced entities (NPCs)
        for ref_id in inferred["npc_ids"]:
            if ref_id and ref_id in node_ids:
                edges.append(_make_edge(tid, ref_id, "involves"))

        # Thread → referenced factions
        for ref_id in inferred["faction_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id and ref_id in node_ids:
                edges.append(_make_edge(tid, ref_id, "pressures"))

        # Thread → referenced locations
        for ref_id in inferred["location_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id and ref_id in node_ids:
                edges.append(_make_edge(tid, ref_id, "connected_to"))

    # --- Opening ---
    starting_location = _safe_str(setup_payload.get("starting_location_id"))
    starting_npcs = _safe_list(setup_payload.get("starting_npc_ids"))

    # Only add opening node if there is context
    if starting_location or starting_npcs:
        opening_id = "opening"
        nodes.append(_make_node(opening_id, "opening", "Opening Scene", {
            "starting_location_id": starting_location,
            "starting_npc_ids": starting_npcs,
        }))
        node_ids.add(opening_id)

        if starting_location and starting_location in node_ids:
            edges.append(_make_edge(opening_id, starting_location, "starts_at"))

        for npc_id in starting_npcs:
            npc_id = _safe_str(npc_id)
            if npc_id and npc_id in node_ids:
                edges.append(_make_edge(opening_id, npc_id, "connected_to"))

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# build_simulation_summary
# ---------------------------------------------------------------------------


def build_simulation_summary(setup_payload: dict[str, Any]) -> dict[str, Any]:
    """Compute a lightweight simulation summary from the setup payload.

    Returns entity counts, hot locations, isolated factions,
    orphan NPCs, unresolved threads, and resolved context.
    """
    factions = _safe_list(setup_payload.get("factions"))
    locations = _safe_list(setup_payload.get("locations"))
    npcs = _safe_list(setup_payload.get("npc_seeds"))
    metadata = _safe_dict(setup_payload.get("metadata"))
    threads = _safe_list(metadata.get("regenerated_threads"))

    # --- Entity counts ---
    entity_counts = {
        "factions": len(factions),
        "locations": len(locations),
        "npcs": len(npcs),
        "threads": len(threads),
    }

    # --- Build lookup maps ---
    faction_ids = {_safe_str(_safe_dict(f).get("faction_id")) for f in factions if _safe_str(_safe_dict(f).get("faction_id"))}
    location_ids = {_safe_str(_safe_dict(l).get("location_id")) for l in locations if _safe_str(_safe_dict(l).get("location_id"))}

    # NPCs per location / faction
    npcs_at_location: dict[str, list[str]] = {}
    npcs_in_faction: dict[str, list[str]] = {}
    orphan_npcs: list[dict[str, str]] = []

    for npc in npcs:
        npc = _safe_dict(npc)
        nid = _safe_str(npc.get("npc_id"))
        if not nid:
            continue
        loc_id = _safe_str(npc.get("location_id"))
        fac_id = _safe_str(npc.get("faction_id"))

        if loc_id and loc_id in location_ids:
            npcs_at_location.setdefault(loc_id, []).append(nid)
        if fac_id and fac_id in faction_ids:
            npcs_in_faction.setdefault(fac_id, []).append(nid)

        if (not loc_id or loc_id not in location_ids) and (not fac_id or fac_id not in faction_ids):
            orphan_npcs.append({"npc_id": nid, "name": _safe_str(npc.get("name"))})

    # --- Threads per location / faction ---
    threads_at_location: dict[str, int] = {}
    threads_at_faction: dict[str, int] = {}
    factions_linked_by_threads: dict[str, set[str]] = {}
    npc_by_id = {npc.get("npc_id"): npc for npc in npcs if npc.get("npc_id")}

    for thread in threads:
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id")) or ""
        inferred = _infer_thread_links(thread, npc_by_id)

        for ref_id in inferred["location_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id in location_ids:
                threads_at_location[ref_id] = threads_at_location.get(ref_id, 0) + 1

        for ref_id in inferred["faction_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id in faction_ids:
                threads_at_faction[ref_id] = threads_at_faction.get(ref_id, 0) + 1

        # Track which factions share threads
        thread_factions = [_safe_str(r) for r in inferred["faction_ids"] if _safe_str(r) in faction_ids]
        for fid in thread_factions:
            factions_linked_by_threads.setdefault(fid, set()).update(thread_factions)

    # --- Hot locations ---
    hot_locations: list[dict[str, Any]] = []
    for loc in locations:
        loc = _safe_dict(loc)
        lid = _safe_str(loc.get("location_id"))
        if not lid:
            continue
        npc_count = len(npcs_at_location.get(lid, []))
        thread_count = threads_at_location.get(lid, 0)
        score = npc_count + thread_count
        if score > 0:
            hot_locations.append({
                "location_id": lid,
                "name": _safe_str(loc.get("name")),
                "npc_count": npc_count,
                "thread_count": thread_count,
                "score": score,
            })
    hot_locations.sort(key=lambda x: x["score"], reverse=True)

    # --- Isolated factions ---
    isolated_factions: list[dict[str, str]] = []
    for fac in factions:
        fac = _safe_dict(fac)
        fid = _safe_str(fac.get("faction_id"))
        if not fid:
            continue
        has_members = fid in npcs_in_faction
        has_threads = fid in threads_at_faction
        if not has_members and not has_threads:
            isolated_factions.append({
                "faction_id": fid,
                "name": _safe_str(fac.get("name")),
            })

    # --- Faction tensions ---
    faction_tensions: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for fid, linked in factions_linked_by_threads.items():
        for other_fid in linked:
            if other_fid == fid:
                continue
            pair = tuple(sorted([fid, other_fid]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(cast(tuple[str, str], pair))

            # Count shared NPCs at same locations
            shared_npcs = set(npcs_in_faction.get(fid, [])) & set(npcs_in_faction.get(other_fid, []))
            faction_tensions.append({
                "factions": list(pair),
                "shared_thread_link": True,
                "shared_npc_count": len(shared_npcs),
            })

    # --- Unresolved threads ---
    unresolved_threads: list[dict[str, str]] = []
    for thread in threads:
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id")) or ""
        title = _safe_str(thread.get("title")) or _safe_str(thread.get("label")) or ""
        status = _safe_str(thread.get("status"))
        if status != "resolved":
            unresolved_threads.append({"thread_id": tid, "title": title})

    # --- Resolved context ---
    resolved_context: dict[str, Any] = {}
    regen_opening = _safe_dict(metadata.get("regenerated_opening"))
    if regen_opening:
        resolved_context = {
            "location_id": _safe_str(regen_opening.get("resolved_context", {}).get("location_id")),
            "npc_ids": _safe_list(regen_opening.get("resolved_context", {}).get("npc_ids")),
            "opening_text": _safe_str(regen_opening.get("opening_text")),
        }

    return {
        "entity_counts": entity_counts,
        "hot_locations": hot_locations,
        "isolated_factions": isolated_factions,
        "orphan_npcs": orphan_npcs,
        "unresolved_threads": unresolved_threads,
        "faction_tensions": faction_tensions,
        "resolved_context": resolved_context,
    }


# ---------------------------------------------------------------------------
# build_entity_inspector
# ---------------------------------------------------------------------------


def build_entity_inspector(setup_payload: dict[str, Any]) -> dict[str, Any]:
    """Build per-entity inspector detail maps.

    Returns ``{"entities": {"npc_mara_voss": {...}, ...}}``.
    """
    entities: dict[str, dict[str, Any]] = {}
    metadata = _safe_dict(setup_payload.get("metadata"))
    threads = _safe_list(metadata.get("regenerated_threads"))
    npcs = _safe_list(setup_payload.get("npc_seeds"))
    npc_by_id = {npc.get("npc_id"): npc for npc in npcs if npc.get("npc_id")}

    # Build reverse indexes: entity_id → threads it appears in
    entity_threads: dict[str, list[dict[str, str]]] = {}
    for thread in threads:
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id")) or ""
        title = _safe_str(thread.get("title")) or _safe_str(thread.get("label")) or ""
        thread_ref = {"thread_id": tid, "title": title}
        inferred = _infer_thread_links(thread, npc_by_id)

        for ref_id in inferred["npc_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id:
                entity_threads.setdefault(ref_id, []).append(thread_ref)
        for ref_id in inferred["faction_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id:
                entity_threads.setdefault(ref_id, []).append(thread_ref)
        for ref_id in inferred["location_ids"]:
            ref_id = _safe_str(ref_id)
            if ref_id:
                entity_threads.setdefault(ref_id, []).append(thread_ref)

    # Build faction member map
    faction_members: dict[str, list[dict[str, str]]] = {}
    # Build location → faction map
    location_factions: dict[str, set[str]] = {}

    for npc in _safe_list(setup_payload.get("npc_seeds")):
        npc = _safe_dict(npc)
        nid = _safe_str(npc.get("npc_id"))
        if not nid:
            continue
        fac_id = _safe_str(npc.get("faction_id"))
        loc_id = _safe_str(npc.get("location_id"))
        if fac_id:
            faction_members.setdefault(fac_id, []).append({"npc_id": nid, "name": _safe_str(npc.get("name"))})
        if loc_id and fac_id:
            location_factions.setdefault(loc_id, set()).add(fac_id)

    # Location → NPC residents
    location_npcs: dict[str, list[dict[str, str]]] = {}
    for npc in _safe_list(setup_payload.get("npc_seeds")):
        npc = _safe_dict(npc)
        nid = _safe_str(npc.get("npc_id"))
        loc_id = _safe_str(npc.get("location_id"))
        if nid and loc_id:
            location_npcs.setdefault(loc_id, []).append({"npc_id": nid, "name": _safe_str(npc.get("name"))})

    # --- NPC inspectors ---
    for npc in _safe_list(setup_payload.get("npc_seeds")):
        npc = _safe_dict(npc)
        nid = _safe_str(npc.get("npc_id"))
        if not nid:
            continue
        entities[nid] = {
            "type": "npc",
            "name": _safe_str(npc.get("name")),
            "role": _safe_str(npc.get("role")),
            "description": _safe_str(npc.get("description")),
            "faction_id": _safe_str(npc.get("faction_id")),
            "location_id": _safe_str(npc.get("location_id")),
            "goals": _safe_list(npc.get("goals")),
            "related_threads": entity_threads.get(nid, []),
        }

    # --- Faction inspectors ---
    for fac in _safe_list(setup_payload.get("factions")):
        fac = _safe_dict(fac)
        fid = _safe_str(fac.get("faction_id"))
        if not fid:
            continue
        # Gather related locations: locations where members reside
        member_locations: set[str] = set()
        for npc in _safe_list(setup_payload.get("npc_seeds")):
            npc = _safe_dict(npc)
            if _safe_str(npc.get("faction_id")) == fid and _safe_str(npc.get("location_id")):
                member_locations.add(_safe_str(npc.get("location_id")))

        entities[fid] = {
            "type": "faction",
            "name": _safe_str(fac.get("name")),
            "description": _safe_str(fac.get("description")),
            "goals": _safe_list(fac.get("goals")),
            "members": faction_members.get(fid, []),
            "related_threads": entity_threads.get(fid, []),
            "related_locations": sorted(member_locations),
        }

    # --- Location inspectors ---
    for loc in _safe_list(setup_payload.get("locations")):
        loc = _safe_dict(loc)
        lid = _safe_str(loc.get("location_id"))
        if not lid:
            continue
        entities[lid] = {
            "type": "location",
            "name": _safe_str(loc.get("name")),
            "description": _safe_str(loc.get("description")),
            "tags": _safe_list(loc.get("tags")),
            "residents": location_npcs.get(lid, []),
            "involved_factions": sorted(location_factions.get(lid, set())),
            "related_threads": entity_threads.get(lid, []),
        }

    # --- Thread inspectors ---
    for idx, thread in enumerate(threads):
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id")) or f"thread_{idx}"
        inferred = _infer_thread_links(thread, npc_by_id)
        entities[tid] = {
            "type": "thread",
            "title": _safe_str(thread.get("title")) or _safe_str(thread.get("label")) or f"Thread {idx + 1}",
            "description": _safe_str(thread.get("description")),
            "involved_entities": inferred["npc_ids"],
            "faction_ids": inferred["faction_ids"],
            "location_ids": inferred["location_ids"],
            "status": _safe_str(thread.get("status")),
        }

    return {"entities": entities}


# ---------------------------------------------------------------------------
# Top-level inspect_world
# ---------------------------------------------------------------------------


def inspect_world(setup_payload: dict[str, Any]) -> dict[str, Any]:
    """Compute graph, simulation summary, and entity inspector in one call.

    This is the primary entry point used by the service/route layer.
    """
    return {
        "success": True,
        "graph": build_world_graph(setup_payload),
        "simulation": build_simulation_summary(setup_payload),
        "inspector": build_entity_inspector(setup_payload),
    }
