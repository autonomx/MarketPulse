"""Phase 6.5 — Alliance System.

Deterministic alliance tracking between factions.
Alliances can be created, strengthened, weakened, or broken.
"""

from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

_MAX_ALLIANCES = 32


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _alliance_id(member_ids: List[str]) -> str:
    """Build a canonical alliance ID from member IDs."""
    members = sorted(_safe_str(m) for m in member_ids if _safe_str(m))
    return "ally:" + ":".join(members[:4])


class AllianceSystem:
    """Deterministic alliance tracker with bounded entries.

    Each alliance has a strength (0-1), status, and reason.
    """

    def __init__(self, alliances: List[Dict[str, Any]] | None = None):
        self.alliances: List[Dict[str, Any]] = []
        for item in alliances or []:
            if isinstance(item, dict):
                self.alliances.append({
                    "alliance_id": _safe_str(item.get("alliance_id")),
                    "member_ids": sorted([_safe_str(m) for m in (item.get("member_ids") or []) if _safe_str(m)]),
                    "strength": max(0.0, min(1.0, _safe_float(item.get("strength"), 0.0))),
                    "status": _safe_str(item.get("status")) or "active",
                    "reason": _safe_str(item.get("reason")),
                })
        self._trim()

    def propose_or_strengthen(self, member_ids: List[str], reason: str, delta: float = 0.1):
        """Propose a new alliance or strengthen an existing one.

        Returns the updated/created alliance dict, or None if insufficient members.
        """
        member_ids = sorted([_safe_str(m) for m in member_ids if _safe_str(m)])
        if len(member_ids) < 2:
            return None
        alliance_id = _alliance_id(member_ids)
        for item in self.alliances:
            if item["alliance_id"] == alliance_id:
                item["strength"] = min(1.0, item["strength"] + _safe_float(delta, 0.1))
                item["status"] = "active"
                if reason:
                    item["reason"] = _safe_str(reason)
                self._trim()
                return dict(item)
        created = {
            "alliance_id": alliance_id,
            "member_ids": member_ids,
            "strength": max(0.0, min(1.0, _safe_float(delta, 0.1))),
            "status": "active",
            "reason": _safe_str(reason),
        }
        self.alliances.append(created)
        self._trim()
        return dict(created)

    def _trim(self):
        """Sort alliances by strength and trim to max capacity."""
        self.alliances.sort(key=lambda item: (-item.get("strength", 0.0), item.get("alliance_id", "")))
        self.alliances = self.alliances[:_MAX_ALLIANCES]

    def to_dict(self):
        return [dict(item) for item in self.alliances]

    @classmethod
    def from_dict(cls, data):
        return cls(data or [])