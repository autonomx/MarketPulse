"""Phase 6.5 — Reputation Graph.

Deterministic reputation tracking between entities (NPCs, factions, player).
Reputation edges have trust, fear, respect, hostility scores clamped to [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

_KEYS = ("trust", "fear", "respect", "hostility")
_MAX_TARGETS_PER_SOURCE = 24


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


class ReputationGraph:
    """Deterministic reputation graph with bounded edges.

    Each source entity can track reputation toward multiple targets.
    The number of targets per source is capped at 24.
    """

    def __init__(self, edges: Dict[str, Dict[str, Dict[str, float]]] | None = None):
        self.edges: Dict[str, Dict[str, Dict[str, float]]] = {}
        raw = edges or {}
        for source_id, targets in raw.items():
            source_id = _safe_str(source_id)
            if not source_id or not isinstance(targets, dict):
                continue
            self.edges[source_id] = {}
            for target_id, rec in sorted(targets.items()):
                target_id = _safe_str(target_id)
                if not target_id or not isinstance(rec, dict):
                    continue
                self.edges[source_id][target_id] = {
                    key: _clamp(_safe_float(rec.get(key), 0.0))
                    for key in _KEYS
                }
            self._trim_source(source_id)

    def _ensure(self, source_id: str, target_id: str) -> Dict[str, float]:
        source_id = _safe_str(source_id)
        target_id = _safe_str(target_id)
        source = self.edges.setdefault(source_id, {})
        rec = source.setdefault(target_id, {key: 0.0 for key in _KEYS})
        return rec

    def update(self, source_id: str, target_id: str, key: str, delta: float) -> float:
        """Update a reputation dimension by delta.

        Returns the new clamped value.
        """
        if key not in _KEYS:
            return 0.0
        rec = self._ensure(source_id, target_id)
        rec[key] = _clamp(rec.get(key, 0.0) + _safe_float(delta))
        self._trim_source(_safe_str(source_id))
        return rec[key]

    def get(self, source_id: str, target_id: str) -> Dict[str, float]:
        source_id = _safe_str(source_id)
        target_id = _safe_str(target_id)
        return dict((self.edges.get(source_id) or {}).get(target_id) or {key: 0.0 for key in _KEYS})

    def _trim_source(self, source_id: str):
        """Keep only the top N targets by absolute reputation sum."""
        source = self.edges.get(source_id) or {}
        items = list(source.items())
        items.sort(
            key=lambda item: (
                -(abs(item[1].get("trust", 0.0)) + abs(item[1].get("hostility", 0.0)) + abs(item[1].get("fear", 0.0)) + abs(item[1].get("respect", 0.0))),
                item[0],
            )
        )
        self.edges[source_id] = dict(items[:_MAX_TARGETS_PER_SOURCE])

    def to_dict(self) -> Dict[str, Any]:
        return {source_id: {target_id: dict(rec) for target_id, rec in targets.items()} for source_id, targets in self.edges.items()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None):
        return cls(data or {})