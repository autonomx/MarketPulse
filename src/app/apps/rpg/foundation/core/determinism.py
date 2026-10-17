"""Deterministic identity primitives for the RPG event system.

This module provides:
- DeterminismConfig: Configuration for deterministic execution
- SeededRNG: Per-engine seeded RNG (never use module-level random directly)
- stable_json: Deterministic JSON serialization
- compute_deterministic_event_id: SHA256-based deterministic event IDs

DESIGN RULES:
- Event identity is derived from causal history, not process-global state
- All replay/live/sandbox engines must use the same seed for equivalence
- Module-level random should NEVER be used for game logic
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from app.runtime.clock import current_turn_context

# Bump this only when intentionally changing deterministic event identity rules.
IDENTITY_VERSION = 1


def rng_seed_from_session_id(session_id: str) -> int:
    """Derive the stable 64-bit RNG seed used to upcast pre-seed sessions."""

    session_id = str(session_id)
    if not session_id.strip():
        raise ValueError("session_id is required to derive the RPG RNG seed")
    return int.from_bytes(hashlib.sha256(session_id.encode("utf-8")).digest()[:8], "big")


def rng_for(
    session_seed: int,
    turn_index: int,
    purpose: str,
    sub_index: int = 0,
) -> random.Random:
    """Create a replay-stable RNG stream for one named turn decision."""

    if (
        not isinstance(turn_index, int)
        or isinstance(turn_index, bool)
        or not isinstance(sub_index, int)
        or isinstance(sub_index, bool)
        or turn_index < 0
        or sub_index < 0
    ):
        raise ValueError("turn_index and sub_index must be non-negative")
    if not isinstance(session_seed, int) or isinstance(session_seed, bool) or not 0 <= session_seed < 2**64:
        raise ValueError("session_seed must be an unsigned 64-bit integer")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose is required for a deterministic RPG RNG stream")
    material = f"{session_seed}:{turn_index}:{purpose}:{sub_index}".encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return random.Random(seed)


def rng_for_current_turn(purpose: str, sub_index: int = 0) -> random.Random:
    """Build a named decision stream from the active turn's immutable input."""

    context = current_turn_context()
    if (
        context is None
        or context.session_seed is None
        or context.turn_index is None
    ):
        raise RuntimeError("text RNG requires a seeded RPG turn context")
    return rng_for(context.session_seed, context.turn_index, purpose, sub_index)


def turn_rng_identity(session: Any, fallback_turn_index: int) -> tuple[int, int]:
    """Resolve the durable seed and turn number for a pipeline invocation."""

    context = current_turn_context()
    session_data = session if isinstance(session, dict) else {}
    state = session_data.get("simulation_state")
    state = state if isinstance(state, dict) else session_data
    if context is not None and context.session_seed is not None:
        session_seed: Any = context.session_seed
    else:
        session_seed = state.get("rng_seed")
        if (
            not isinstance(session_seed, int)
            or isinstance(session_seed, bool)
            or not 0 <= session_seed < 2**64
        ):
            session_id = (
                session_data.get("session_id")
                or session_data.get("id")
                or state.get("session_id")
                or state.get("id")
            )
            session_seed = rng_seed_from_session_id(str(session_id)) if session_id else 0
    if context is not None and context.turn_index is not None:
        turn_index: Any = context.turn_index
    else:
        turn_index = state.get("turn_index")
        if not isinstance(turn_index, int) or isinstance(turn_index, bool):
            turn_index = fallback_turn_index
    return session_seed, turn_index


def stable_sub_index(value: Any) -> int:
    """Derive a stable stream index from a JSON-safe decision subject."""

    digest = hashlib.sha256(stable_json(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def deterministic_turn_uuid(
    session_id: str,
    turn_index: int,
    purpose: str,
    counter: int = 0,
) -> str:
    """Return a stable UUID for an event created by a turn decision."""

    if (
        not isinstance(turn_index, int)
        or isinstance(turn_index, bool)
        or not isinstance(counter, int)
        or isinstance(counter, bool)
        or turn_index < 0
        or counter < 0
    ):
        raise ValueError("turn_index and counter must be non-negative")
    if not str(session_id).strip():
        raise ValueError("session_id is required for deterministic RPG event IDs")
    session_namespace = uuid.uuid5(uuid.NAMESPACE_URL, session_id)
    return str(uuid.uuid5(session_namespace, f"{turn_index}:{purpose}:{counter}"))


@dataclass
class DeterminismConfig:
    """Configuration for deterministic execution.

    Attributes:
        seed: Random seed for seeded RNG.
        strict_replay: If True, fail hard on missing replay data.
        replay_mode: If True, the system is in replay mode (no fresh side effects).
        record_llm: If True, record LLM prompt/response pairs during live runs.
        use_recorded_llm: If True, use recorded LLM responses instead of calling LLM.
        record_tools: If True, record tool/runtime call results during live runs.
        use_recorded_tools: If True, use recorded tool results instead of live calls.
        record_host: If True, record host/runtime boundary results during live runs.
        use_recorded_host: If True, use recorded host/runtime results instead of live calls.
    """
    seed: int = 0
    strict_replay: bool = True
    replay_mode: bool = False
    record_llm: bool = False
    use_recorded_llm: bool = False
    record_tools: bool = False
    use_recorded_tools: bool = False
    record_host: bool = False
    use_recorded_host: bool = False


class SeededRNG:
    """Per-engine seeded RNG. Never use module-level random directly."""

    def __init__(self, seed: int = 0):
        self._seed = seed
        self._rng = random.Random(seed)

    @property
    def seed(self) -> int:
        return self._seed

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def random(self) -> float:
        return self._rng.random()

    def choice(self, seq):
        if not seq:
            raise IndexError("Cannot choose from empty sequence")
        return seq[self._rng.randrange(len(seq))]

    def shuffle(self, x) -> None:
        self._rng.shuffle(x)

    def getstate(self):
        """Expose underlying RNG state for snapshots."""
        return self._rng.getstate()

    def setstate(self, state) -> None:
        """Restore underlying RNG state from snapshots."""
        self._rng.setstate(state)

    def serialize_state(self) -> dict[str, Any]:
        return {"state": self._rng.getstate(), "seed": self._seed}

    def deserialize_state(self, state: dict[str, Any]) -> None:
        self._rng.setstate(state["state"])


def stable_json(obj: Any) -> str:
    """Deterministic JSON serialization."""
    def normalize(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: normalize(v[k]) for k in sorted(v)}
        if isinstance(v, list):
            return [normalize(x) for x in v]
        if isinstance(v, set):
            return [normalize(x) for x in sorted(v, key=lambda i: repr(i))]
        if isinstance(v, float):
            return round(v, 6)
        if hasattr(v, "__dict__"):
            return normalize(vars(v))
        return v

    return json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"))


def compute_deterministic_event_id(
    *,
    seed: int,
    event_type: str,
    payload: dict[str, Any],
    source: Optional[str],
    parent_id: Optional[str],
    tick: Optional[int],
    seq: int,
) -> str:
    """
    Deterministic event identity derived from causal input, not process-global state.

    Identity is execution-path based, not semantic-equivalence based.
    That means:
    same seed + same canonical payload + same parent + same tick + same seq
    => same event_id
    """
    data = {
        "v": IDENTITY_VERSION,
        "seed": seed,
        "type": event_type,
        "payload": payload,
        "source": source,
        "parent_id": parent_id,
        "tick": tick,
        "seq": seq,
    }
    digest = hashlib.sha256(stable_json(data).encode()).hexdigest()
    return f"evt_{digest[:20]}"
