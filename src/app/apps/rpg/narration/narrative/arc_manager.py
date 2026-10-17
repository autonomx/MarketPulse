"""Arc Manager - Story Arc Lifecycle Management.

TIER 18: Narrative Intelligence - Meta-AI Story Director
"""

from __future__ import annotations

from typing import Any, Dict, List


class ArcManager:
    """Tracks lifecycle of meta-narrative arcs."""

    def __init__(self):
        self.active_arcs: List[Dict[str, Any]] = []
        self.resolved_arcs: List[Dict[str, Any]] = []

    def update(self, story_state):
        for arc in list(self.active_arcs):
            arc["progress"] += 0.05
            if arc["progress"] >= 1.0:
                arc["status"] = "resolved"
                story_state.resolved_arcs.append(arc)
                self.active_arcs.remove(arc)

    def get_summary(self):
        lines = ["Active Arcs:"]
        for a in self.active_arcs:
            lines.append(f"  - {a['id']}: {a['description']} ({int(a['progress']*100)}%)")
        if self.resolved_arcs:
            lines.append("Resolved Arcs:")
            for a in self.resolved_arcs[-5:]:
                lines.append(f"  [x] {a['id']}: {a['description']}")
        return "\n".join(lines)

    def reset(self):
        self.active_arcs.clear()
        self.resolved_arcs.clear()
