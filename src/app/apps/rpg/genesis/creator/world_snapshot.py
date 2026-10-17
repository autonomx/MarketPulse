"""Phase 2.5 — World Snapshot + Graph Diff.

Provides deterministic snapshot wrapping around the existing world
inspection result, plus graph/entity diff computation.

All functions accept plain dicts and return plain dicts suitable for
JSON serialization.  Nothing here mutates the setup payload.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .world_graph import (
    build_entity_inspector,
    build_simulation_summary,
    build_world_graph,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

# ---------------------------------------------------------------------------
# Stable hashing and normalization helpers
# ---------------------------------------------------------------------------


def _stable_hash(obj: Any) -> str:
    """Stable content hash for snapshot identity and dedupe."""
    try:
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        payload = repr(obj)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_scalar(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {
            k: _normalize_value(v)
            for k, v in sorted(value.items())
            if _normalize_value(v) not in (None, [], {})
        }
        return normalized
    if isinstance(value, list):
        items = [_normalize_value(v) for v in value]
        items = [v for v in items if v not in (None, [], {})]
        if all(not isinstance(v, (dict, list)) for v in items):
            try:
                return sorted(items)
            except Exception:
                return items
        return items
    return _normalize_scalar(value)


def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    return _normalize_value(entity or {})


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str] | None:
    edge = _safe_dict(edge)
    source = edge.get("source")
    target = edge.get("target")
    edge_type = edge.get("type")
    if not (source and target and edge_type):
        return None
    return (str(source), str(target), str(edge_type))


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def build_world_snapshot(
    setup_payload: dict[str, Any],
    label: str | None = None,
) -> dict[str, Any]:
    """Build a full snapshot wrapper from a setup payload.

    Returns
    -------
    dict
        ``{"snapshot_id": ..., "label": ..., "created_at": ...,
           "graph": {...}, "simulation": {...}, "inspector": {...},
           "summary": {"node_count": int, "edge_count": int}}``
    """
    content_hash = _stable_hash(setup_payload)
    snapshot_id = f"snap_{content_hash[:8]}"
    graph = build_world_graph(setup_payload)
    simulation = build_simulation_summary(setup_payload)
    inspector = build_entity_inspector(setup_payload)

    return {
        "snapshot_id": snapshot_id,
        "label": label or "Snapshot",
        "created_at": time.time(),
        "content_hash": content_hash,
        "setup": setup_payload,
        "graph": graph,
        "simulation": simulation,
        "inspector": inspector,
        "summary": {
            "node_count": len(graph.get("nodes", [])),
            "edge_count": len(graph.get("edges", [])),
        },
    }


# ---------------------------------------------------------------------------
# Graph diff
# ---------------------------------------------------------------------------


def compute_graph_diff(
    before_graph: dict[str, Any] | None,
    after_graph: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute added/removed/changed nodes and added/removed edges.

    Parameters
    ----------
    before_graph, after_graph :
        Each has shape ``{"nodes": [...], "edges": [...]}``.

    Returns
    -------
    dict
        ``{"nodes": {"added": [...], "removed": [...], "changed": [...]},
           "edges": {"added": [...], "removed": [...]},
           "summary": [...]}``
    """
    before_graph = before_graph or {"nodes": [], "edges": []}
    after_graph = after_graph or {"nodes": [], "edges": []}

    # --- Node diff ---
    before_nodes: dict[str, dict[str, Any]] = {
        n["id"]: {
            "id": n.get("id"),
            "label": n.get("label"),
            "type": n.get("type"),
            "meta": _normalize_value(n.get("meta") or {}),
        }
        for n in _safe_list(before_graph.get("nodes"))
        if _safe_dict(n).get("id")
    }
    after_nodes: dict[str, dict[str, Any]] = {
        n["id"]: {
            "id": n.get("id"),
            "label": n.get("label"),
            "type": n.get("type"),
            "meta": _normalize_value(n.get("meta") or {}),
        }
        for n in _safe_list(after_graph.get("nodes"))
        if _safe_dict(n).get("id")
    }

    added_ids = sorted(set(after_nodes) - set(before_nodes))
    removed_ids = sorted(set(before_nodes) - set(after_nodes))

    changed: list[dict[str, Any]] = []
    for nid in sorted(set(before_nodes) & set(after_nodes)):
        b = before_nodes[nid]
        a = after_nodes[nid]
        changed_fields: list[str] = []
        if b.get("label") != a.get("label"):
            changed_fields.append("label")
        if b.get("type") != a.get("type"):
            changed_fields.append("type")
        if _normalize_value(b.get("meta")) != _normalize_value(a.get("meta")):
            changed_fields.append("meta")
        if changed_fields:
            changed.append({"id": nid, "fields": sorted(changed_fields)})

    # --- Edge diff (deduplicated via set) ---
    before_edges = {
        ek for ek in (_edge_key(e) for e in _safe_list(before_graph.get("edges"))) if ek
    }
    after_edges = {
        ek for ek in (_edge_key(e) for e in _safe_list(after_graph.get("edges"))) if ek
    }

    added_edges = [
        {"source": k[0], "target": k[1], "type": k[2]}
        for k in sorted(after_edges - before_edges)
    ]
    removed_edges = [
        {"source": k[0], "target": k[1], "type": k[2]}
        for k in sorted(before_edges - after_edges)
    ]

    # --- Summary ---
    summary = summarize_graph_diff(
        added_ids, removed_ids, changed, added_edges, removed_edges,
    )

    return {
        "nodes": {
            "added": added_ids,
            "removed": removed_ids,
            "changed": changed,
        },
        "edges": {
            "added": added_edges,
            "removed": removed_edges,
        },
        "summary": summary,
    }


def summarize_graph_diff(
    added_ids: list[str],
    removed_ids: list[str],
    changed: list[dict[str, Any]],
    added_edges: list[dict[str, Any]],
    removed_edges: list[dict[str, Any]],
) -> list[str]:
    """Return human-readable summary strings."""
    lines: list[str] = []

    def _pl(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    if added_ids:
        lines.append(f"{_pl(len(added_ids), 'node')} added")
    if removed_ids:
        lines.append(f"{_pl(len(removed_ids), 'node')} removed")
    if changed:
        lines.append(f"{_pl(len(changed), 'node')} changed")
    if added_edges:
        lines.append(f"{_pl(len(added_edges), 'edge')} added")
    if removed_edges:
        lines.append(f"{_pl(len(removed_edges), 'edge')} removed")
    return lines


# ---------------------------------------------------------------------------
# Entity history diff
# ---------------------------------------------------------------------------


def _list_diff(a: list[Any], b: list[Any]) -> dict[str, list[Any]]:
    a_set = set(a or [])
    b_set = set(b or [])
    return {
        "added": sorted(list(b_set - a_set)),
        "removed": sorted(list(a_set - b_set)),
    }


def compute_entity_history_diff(
    before_inspector: dict[str, Any] | None,
    after_inspector: dict[str, Any] | None,
    entity_id: str,
) -> dict[str, Any]:
    """Compute per-entity field diff between two inspector outputs.

    Parameters
    ----------
    before_inspector, after_inspector :
        Each has shape ``{"entities": {"npc_mara_voss": {...}, ...}}``.
    entity_id :
        The entity to inspect.

    Returns
    -------
    dict
        ``{"entity_id": ..., "exists_before": bool, "exists_after": bool,
           "diff": {"changed_fields": [...], "before": {...}, "after": {...},
                    "related_added": [...], "related_removed": [...]}}``
    """
    before_entities = (before_inspector or {}).get("entities") or {}
    after_entities = (after_inspector or {}).get("entities") or {}

    before_entity = _safe_dict(before_entities.get(entity_id))
    after_entity = _safe_dict(after_entities.get(entity_id))

    exists_before = before_entity is not None and bool(before_entity)
    exists_after = after_entity is not None and bool(after_entity)

    if not exists_before and not exists_after:
        return {
            "entity_id": entity_id,
            "exists_before": False,
            "exists_after": False,
            "changed_fields": [],
            "field_diffs": {},
            "before": {},
            "after": {},
            "related_added": [],
            "related_removed": [],
        }

    before_detail = _normalize_entity(_safe_dict(before_entity.get("details")))
    after_detail = _normalize_entity(_safe_dict(after_entity.get("details")))

    all_fields = sorted(set(before_detail.keys()) | set(after_detail.keys()))
    changed_fields: list[str] = []
    field_diffs: dict[str, Any] = {}
    for field in all_fields:
        before_value = before_detail.get(field)
        after_value = after_detail.get(field)
        if before_value != after_value:
            changed_fields.append(field)
            if isinstance(before_value, list) and isinstance(after_value, list):
                field_diffs[field] = _list_diff(before_value, after_value)
            else:
                field_diffs[field] = {
                    "before": before_value,
                    "after": after_value,
                }

    before_related = sorted(set(_safe_list(before_entity.get("related_ids"))))
    after_related = sorted(set(_safe_list(after_entity.get("related_ids"))))

    return {
        "entity_id": entity_id,
        "exists_before": exists_before,
        "exists_after": exists_after,
        "changed_fields": changed_fields,
        "field_diffs": field_diffs,
        "before": before_detail,
        "after": after_detail,
        "related_added": sorted(list(set(after_related) - set(before_related))),
        "related_removed": sorted(list(set(before_related) - set(after_related))),
    }


