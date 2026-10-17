"""Phase 6.5 — Rumor System.

Deterministic rumor generation, spread, and cooling.
Rumors spawn from sim events and propagate each tick with bounded reach.
"""

from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

_MAX_RUMORS = 64
_MAX_NEW_RUMORS_PER_TICK = 8


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class RumorSystem:
    """Deterministic rumor system with bounded entries and cooling.

    Rumors have heat (lifespan) and reach (how far they spread).
    """

    def __init__(self, rumors: List[Dict[str, Any]] | None = None):
        self.rumors: List[Dict[str, Any]] = []
        for item in rumors or []:
            if isinstance(item, dict):
                self.rumors.append({
                    "rumor_id": _safe_str(item.get("rumor_id")),
                    "type": _safe_str(item.get("type")),
                    "subject_id": _safe_str(item.get("subject_id")),
                    "source_id": _safe_str(item.get("source_id")),
                    "location_id": _safe_str(item.get("location_id")),
                    "faction_id": _safe_str(item.get("faction_id")),
                    "text": _safe_str(item.get("text")),
                    "reach": int(item.get("reach", 1) or 1),
                    "credibility": max(0.0, min(1.0, _safe_float(item.get("credibility"), 0.5))),
                    "heat": int(item.get("heat", 1) or 1),
                    "tick": int(item.get("tick", 0) or 0),
                    "status": _safe_str(item.get("status")) or "active",
                })
        self._trim()

    def spawn_from_events(self, events: List[Dict[str, Any]], tick: int) -> List[Dict[str, Any]]:
        """Spawn rumors from simulation events.

        Returns list of newly created rumor dicts.
        """
        created: List[Dict[str, Any]] = []
        idx = 0
        for event in events or []:
            if not isinstance(event, dict):
                continue
            typ = _safe_str(event.get("type"))
            if typ not in {"player_support", "player_escalation", "betrayal", "social_shock", "trust_collapse"}:
                continue
            rumor = {
                "rumor_id": f"rumor:{int(tick)}:{idx}",
                "type": typ,
                "subject_id": _safe_str(event.get("target_id")) or _safe_str(event.get("subject_id")) or "unknown",
                "source_id": _safe_str(event.get("source_id")) or _safe_str(event.get("actor")),
                "location_id": _safe_str(event.get("location_id")),
                "faction_id": _safe_str(event.get("faction_id")),
                "text": _safe_str(event.get("summary")) or typ,
                "reach": 1,
                "credibility": 0.7 if _safe_str(event.get("actor")) == "player" else 0.6,
                "heat": 2,
                "tick": int(tick),
                "status": "active",
            }
            created.append(rumor)
            idx += 1
            if len(created) >= _MAX_NEW_RUMORS_PER_TICK:
                break
        self.rumors.extend(created)
        self._trim()
        return [dict(item) for item in created]

    def advance(self):
        """Advance all rumors by one tick. Heat decreases, reach spreads until cold."""
        for item in self.rumors:
            if item.get("status") != "active":
                continue
            heat = int(item.get("heat", 0) or 0)
            reach = int(item.get("reach", 0) or 0)
            if heat > 0:
                item["heat"] = heat - 1
                item["reach"] = min(3, reach + 1)
            else:
                item["reach"] = max(0, reach - 1)
                if item["reach"] == 0:
                    item["status"] = "cold"
        self._trim()

    def active(self, limit: int = 8):
        """Return active rumors sorted by heat descending then reach descending."""
        items = [dict(item) for item in self.rumors if item.get("status") == "active"]
        items.sort(key=lambda item: (-int(item.get("heat", 0) or 0), -int(item.get("reach", 0) or 0), item.get("rumor_id", "")))
        return items[:max(0, limit)]

    def _trim(self):
        """Sort rumors by tick descending and trim to max capacity."""
        self.rumors.sort(key=lambda item: (-int(item.get("tick", 0) or 0), item.get("rumor_id", "")))
        self.rumors = self.rumors[:_MAX_RUMORS]

    def to_dict(self):
        return [dict(item) for item in self.rumors]

    @classmethod
    def from_dict(cls, data):
        return cls(data or [])