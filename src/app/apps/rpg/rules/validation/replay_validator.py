"""PHASE 5.1 — Replay vs Live Validator

Ensures replay reconstructs exact same state as live execution.

This validator addresses the core requirement from rpg-design.txt:
- Replay = Live execution

The validation strategy:
1. Run a live game loop with events
2. Run a fresh loop and replay the same events via ReplayEngine
3. Compare state hashes

If hashes match, replay parity is proven.
If hashes differ, replay is missing or corrupting state.

KNOWN SOURCES OF REPLAY MISMATCH:
- ReplayEngine calling load_history() (deprecated, causes duplication)
- Replay not advancing loop._tick_count (causes tick collision)
- Replay not dispatching to system handle_event() (state not reconstructed)
- Events sorted differently in replay vs live
- Non-deterministic timestamps between runs
"""

from typing import Any, Callable, Dict, List, Optional

from app.apps.rpg.foundation.core.replay_engine import ReplayConfig, ReplayEngine
from .state_hash import compute_state_hash


class ReplayValidator:
    """Ensures replay reconstructs exact same state as live execution.

    Usage:
        validator = ReplayValidator(fresh_loop_factory)
        result = validator.validate(sample_events)
        assert result["match"], "Replay state does not match live state!"

    Attributes:
        engine_factory: Callable that returns a fresh game loop instance.
    """

    def __init__(
        self,
        engine_factory: Callable[[], Any],
        config: Optional[ReplayConfig] = None,
    ):
        """Initialize with a factory for creating fresh game loops.

        Args:
            engine_factory: Callable that returns a complete game loop
                           with all subsystems initialized.
            config: Optional replay configuration. Defaults to
                    ReplayConfig(dispatch_to_systems=True, advance_ticks=True).
        """
        self.engine_factory = engine_factory
        self.config = config or ReplayConfig()

    def validate(self, events: List[Any]) -> Dict[str, Any]:
        """Compare live run vs replay run state.

        Args:
            events: List of events to run in both live and replay modes.

        Returns:
            Dictionary with:
            - match: bool - whether live and replay hashes match
            - live_hash: str - hash from live execution
            - replay_hash: str - hash from replay execution
        """
        # --- Live run ---
        loop_live = self.engine_factory()

        for e in events:
            loop_live.event_bus.emit(e)

        # Run some ticks to let systems process events
        num_ticks = max(1, len(events))
        for _ in range(num_ticks):
            loop_live.tick()

        live_hash = compute_state_hash(loop_live)

        # --- Replay run ---
        loop_replay = self.engine_factory()

        replay_engine = ReplayEngine(
            game_loop_factory=self.engine_factory,
            config=self.config,
        )

        # Use the replay engine with the live events
        replay_engine.replay(events)

        replay_hash = compute_state_hash(loop_replay)

        return {
            "match": live_hash == replay_hash,
            "live_hash": live_hash,
            "replay_hash": replay_hash,
        }

