"""Canonical coherence state for RPG narrative/world consistency.

Phase 6.0 — Coherence Core
"""

from .core import CoherenceCore
from .detector import ContradictionDetector
from .models import (
    CoherenceMutation,
    CoherenceState,
    CoherenceUpdateResult,
    CommitmentRecord,
    ConsequenceRecord,
    ContradictionRecord,
    EntityCoherenceView,
    FactRecord,
    SceneAnchor,
    ThreadRecord,
)

__all__ = [
    "CoherenceCore",
    "CoherenceState",
    "FactRecord",
    "ThreadRecord",
    "CommitmentRecord",
    "SceneAnchor",
    "ConsequenceRecord",
    "ContradictionRecord",
    "EntityCoherenceView",
    "CoherenceMutation",
    "CoherenceUpdateResult",
    "ContradictionDetector",
]
