from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HermesCandidate:
    name: str
    target: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    risk: str = "review_required"
    note: str = "Preview only."


def hermes_candidate_payload(candidate: HermesCandidate) -> dict[str, Any]:
    return {"ok": True, "candidate": asdict(candidate), "preview_only": True}


def hermes_demo_candidate(note: str = "ready") -> dict[str, Any]:
    clean = str(note or "ready")[:80]
    return hermes_candidate_payload(
        HermesCandidate(
            name="demo_note",
            target="local_preview",
            before={"note": "ready"},
            after={"note": clean},
        )
    )
