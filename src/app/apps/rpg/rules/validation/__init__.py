"""PHASE 5.1 — Validation Layer

Provides deterministic verification guarantees for the RPG simulation:

- State Hashing: Deterministic fingerprint of game state
- Determinism Validator: Same input -> same output verification
- Replay Validator: Replay reconstructs exact same state as live
- Simulation Parity Validator: Simulation matches real execution

After this phase:
    Capability          Before      After
    Determinism         ???         Guaranteed
    Replay correctness  Warning     Verified
    Simulation trust    No          Proven
    Debugging           Hard        Precise
"""

from .determinism import DeterminismValidator
from .replay_validator import ReplayValidator
from .state_hash import compute_state_hash, stable_serialize

__all__ = [
    "compute_state_hash",
    "stable_serialize",
    "DeterminismValidator",
    "ReplayValidator",
]