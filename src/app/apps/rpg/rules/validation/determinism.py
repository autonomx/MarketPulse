"""PHASE 5.1 — Determinism Validator

Ensures identical runs produce identical results.

This validator addresses the core requirement from rpg-design.txt:
- Determinism: same input -> same output

The validation strategy:
1. Create two identical game loops from the same factory
2. Feed both loops the same events
3. Run both loops for the same number of ticks
4. Compare state hashes

If hashes match, determinism is proven for the given inputs.
If hashes differ, the system has non-deterministic behavior.

KNOWN SOURCES OF NON-DETERMINISM:
- LLM randomness (temperature > 0, random seeds)
- Timestamps (wall clock time)
- unordered dicts / sets in state
- UUID randomness in event IDs
- Random number generators without fixed seeds

FIXES (from rpg-design.txt):
- temperature = 0, seed = fixed
- inject deterministic clock
- always sort dicts/sets
- deterministic ID generator in test mode
"""

from typing import Any, Callable, Dict, List

from .state_hash import compute_state_hash


class DeterminismValidator:
    """Ensures identical runs produce identical results.

    Usage:
        validator = DeterminismValidator(fresh_loop_factory)
        result = validator.run_twice_and_compare(sample_events)
        assert result["match"], "Non-deterministic behavior detected!"

    Attributes:
        engine_factory: Callable that returns a fresh game loop instance.
    """

    def __init__(self, engine_factory: Callable[[], Any]):
        """Initialize with a factory for creating fresh game loops.

        Args:
            engine_factory: Callable that returns a complete game loop
                           with all subsystems initialized.
        """
        self.engine_factory = engine_factory

    def run_twice_and_compare(
        self,
        events: List[Any],
        num_ticks: int = 10,
    ) -> Dict[str, Any]:
        """Run identical game loops twice and compare results.

        Args:
            events: List of events to emit in both loops.
            num_ticks: Number of ticks to run. Default 10.

        Returns:
            Dictionary with:
            - match: bool - whether hashes are identical
            - hash1: str - hash from first run
            - hash2: str - hash from second run
        """
        loop1 = self.engine_factory()
        loop2 = self.engine_factory()

        # Emit same events to both loops
        for e in events:
            loop1.event_bus.emit(e)
            loop2.event_bus.emit(e)

        # Run same number of ticks
        for _ in range(num_ticks):
            loop1.tick()
            loop2.tick()

        hash1 = compute_state_hash(loop1)
        hash2 = compute_state_hash(loop2)

        return {
            "match": hash1 == hash2,
            "hash1": hash1,
            "hash2": hash2,
        }

