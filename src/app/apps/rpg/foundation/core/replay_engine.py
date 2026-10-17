"""PHASE 2 — REPLAY ENGINE (PATCHED + PHASE 2.5 ENHANCEMENTS)

Provides deterministic replay of game state using event history.

Core capabilities:
- Replay full session from events
- Replay up to specific tick
- Enable time-travel debugging
- Foundation for save/load system
- Hybrid replay (snapshot + events) for performance

PHASE 2 FIXES (rpg-design.txt):
- Fix #1 (event_bus.py): emit(replay=True) prevents history duplication
- Fix #2: ReplayEngine no longer calls load_history() — history builds naturally
- Fix #3: Replay advances loop._tick_count to prevent tick collision
- Fix #4: Removed load_history() from replay path entirely
- Fix #5: Events are dispatched to system handle_event() consumers so
          replay actually reconstructs game state instead of just logging

PHASE 2.5 ENHANCEMENTS:
- Deterministic event ordering by (tick, timestamp, event_id)
- Hybrid replay: Load from nearest snapshot + replay events after
- Support for branching timelines via root_event_id

DESIGN RULES:
- Replay MUST use fresh systems, not reused ones (factory pattern)
- Replay MUST advance tick to prevent future tick collisions
- Replay MUST dispatch to systems so state is actually reconstructed
- Events are sorted deterministically for reproducible replay
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, TypeVar, cast

from .event_bus import Event

T = TypeVar("T")


class EventConsumer(Protocol):
    """Protocol for systems that can consume replayed events.

    PHASE 2 FIX #5: Without this, replay emits events but nothing
    processes them — replay does not reconstruct game state.

    Systems that implement this protocol:
    - World: updates terrain, resources, time of day
    - NPC System: updates NPC positions, relationships, goals
    - Story Director: updates narrative state, quest progress
    """

    def handle_event(self, event: Event) -> None:
        """Process a replayed event.

        Args:
            event: The event to process during replay.
        """
        ...


@dataclass
class ReplayConfig:
    """Configuration for replay behavior.

    Attributes:
        dispatch_to_systems: If True, dispatch events to system handle_event().
                           Default True. Without this, replay only logs events.
        advance_ticks: If True, advance loop._tick_count during replay.
                      Default True. Without this, future ticks collide.
        use_load_history: DEPRECATED. If True, loads history into EventBus.
                         Default False. History builds naturally from dispatch.
    """

    dispatch_to_systems: bool = True
    advance_ticks: bool = True
    use_load_history: bool = False  # PHASE 2 FIX #4: Default False, removed from path


class ReplayEngine:
    """Replays event streams to reconstruct game state.

    This engine provides deterministic replay capability by feeding
    recorded events back into a fresh game loop instance. This enables:
    - Full session replay from event history
    - Tick-based time-travel debugging
    - Save/load via event logs (no full state serialization needed)
    - Foundation for branching timelines

    PHASE 2 FIX #2: Requires a factory that returns a COMPLETELY FRESH
    game loop with new system instances. Reusing systems (world, npc_system,
    etc.) causes state leaks and breaks determinism.

    Example:
        def fresh_loop_factory():
            return make_game_loop(
                intent_parser=IntentParser(),    # NEW instance
                world=World(),                   # NEW instance
                npc_system=NPCSystem(),          # NEW instance
                story_director=StoryDirector(),  # NEW instance
                scene_renderer=Renderer(),       # NEW instance
                event_bus=EventBus(),            # NEW instance
            )

        engine = ReplayEngine(fresh_loop_factory)
        loop = engine.replay(saved_events)  # Full replay
        loop = engine.replay(saved_events, up_to_tick=50)  # Partial replay
    """

    def __init__(
        self,
        game_loop_factory: Callable[[], T],
        config: Optional[ReplayConfig] = None,
    ):
        """Initialize the ReplayEngine.

        Args:
            game_loop_factory: Callable that returns a FRESH game loop instance with
                              NEW system instances. Reusing systems breaks determinism.
            config: Optional replay configuration. Defaults to dispatching to
                   systems and advancing ticks.
        """
        self._factory = game_loop_factory
        self._config = config or ReplayConfig()

    def _assert_replay_safe(self, loop: Any) -> None:
        """
        Hook for future strict replay compliance checks.

        Subsystems should avoid fresh side effects in replay mode.
        This method is intentionally lightweight for now and can be extended
        later to enforce replay-safe subsystem capabilities.

        Args:
            loop: Freshly created game loop instance.
        """
        return None

    def replay(
        self,
        events: List[Event],
        up_to_tick: Optional[int] = None,
        branch_leaf_id: Optional[str] = None,
        mode: str = "normal",
    ) -> Any:
        """Replay events into a fresh game loop.

        PHASE 2 FIX #4: No longer calls load_history(). History builds naturally
        from event dispatch during replay.

        PHASE 2 FIX #3: Advances loop._tick_count during replay to prevent
        future tick collisions.

        PHASE 2.5 — DETERMINISTIC ORDERING + HYBRID REPLAY:
        - Events are sorted by (tick, timestamp, event_id) for deterministic ordering
        - Nearest snapshot is loaded first if available, events before snapshot are skipped
        - This prevents O(n) replay from tick 0 for large event histories

        PHASE 3 — BRANCH SELECTION:
        - If branch_leaf_id is provided, only events on that branch are replayed
        - Branch is reconstructed by following parent links from leaf to root
        - This enables "what if I did something different?" time-travel

        PHASE 5.1.5 — FIX #3: Deterministic replay mode.
        When mode="deterministic", the replay engine:
        - Disables LLM calls (prevents non-deterministic AI responses)
        - Freezes time (prevents timestamp-based randomness)
        - Uses recorded outputs only (pure event sourcing)

        Args:
            events: Full event history to replay.
            up_to_tick: Optional tick cutoff. If provided, only events
                       with tick <= up_to_tick will be replayed.
            branch_leaf_id: Optional event ID of the leaf node in a branch.
                           If provided, only events on the path from root
                           to this leaf will be replayed, enabling branching
                           timeline exploration.
            mode: Replay mode. "normal" (default) or "deterministic".
                 Deterministic mode disables LLM, freezes time, and uses
                 recorded outputs for pure deterministic replay.

        Returns:
            Reconstructed game loop with state replayed from events.

        Raises:
            ValueError: If events list is empty.
        """
        if not events:
            raise ValueError("Cannot replay empty event list")

        # PHASE 3 — BRANCH SELECTION: Filter events to specific branch
        if branch_leaf_id is not None:
            # Build event map for quick lookup
            event_map = {e.event_id: e for e in events}

            # If the leaf event isn't in our map, try to find by parent chain
            if branch_leaf_id not in event_map:
                raise ValueError(
                    f"Branch leaf event {branch_leaf_id!r} not found in event history"
                )

            # Reconstruct branch by walking parent chain
            branch_ids = self._get_branch_from_events(branch_leaf_id, cast(Any, event_map))
            events = [event_map[eid] for eid in branch_ids if eid in event_map]

        loop = self._factory()

        # Enter replay mode: subsystems should avoid fresh side effects
        if hasattr(loop, "set_mode"):
            loop.set_mode("replay")
        if hasattr(loop, "event_bus") and hasattr(loop.event_bus, "set_replay_mode"):
            loop.event_bus.set_replay_mode(True)
        self._assert_replay_safe(loop)

        # PHASE 5.3 — Enable recorded LLM usage during replay if supported
        for system_name in ("world", "npc_system", "story_director", "scene_renderer"):
            system = getattr(loop, system_name, None)
            if system is not None and hasattr(system, "determinism"):
                system.determinism.use_recorded_llm = True
                system.determinism.replay_mode = True

        # PHASE 5.1.5 — FIX #3: Enforce deterministic replay mode
        if mode == "deterministic":
            self._apply_deterministic_mode(loop)

        # PHASE 2.5 — HYBRID REPLAY: Load from nearest snapshot first
        snapshot_tick = None
        if hasattr(loop, "snapshot_manager") and loop.snapshot_manager is not None:
            # Ensure it has a real nearest_snapshot method (not a mock)
            sm = loop.snapshot_manager
            if hasattr(sm, "nearest_snapshot") and callable(sm.nearest_snapshot):
                try:
                    snapshot_tick = sm.nearest_snapshot(up_to_tick or 0)
                    # Verify it returned an int or None, not a mock
                    if isinstance(snapshot_tick, (int, type(None))):
                        if snapshot_tick is not None:
                            sm.load_snapshot(snapshot_tick, loop)
                    else:
                        snapshot_tick = None
                except (TypeError, AttributeError):
                    snapshot_tick = None

        # PHASE 2.5 — DETERMINISTIC EVENT ORDERING:
        # PHASE 5.2 — FIRST-CLASS TICK (rpg-design.txt Issue #4):
        # Sort events by (event.tick, _seq) for deterministic ordering.
        # Uses first-class tick field instead of payload["tick"].
        events = sorted(
            events,
            key=lambda e: (
                getattr(e, "tick", 0),  # PHASE 5.2: use first-class tick field
                getattr(e, "_seq", 0),
            ),
        )

        try:
            for event in events:
                tick = event.tick if getattr(event, "tick", None) is not None else event.payload.get("tick")

                # PHASE 2.5 — HYBRID REPLAY: Skip events already captured in snapshot
                if snapshot_tick is not None and tick is not None and tick <= snapshot_tick:
                    continue

                if up_to_tick is not None and tick is not None:
                    if tick > up_to_tick:
                        break

                # PHASE 2 FIX #3: Advance tick count during replay
                # Without this, loop._tick_count stays at 0 and future
                # ticks collide with replayed tick values
                if self._config.advance_ticks and tick is not None:
                    cast(Any, loop)._tick_count = max(cast(Any, loop)._tick_count, tick)

                # Feed event back into systems
                self._apply_event(loop, event)
        finally:
            # Exit replay mode — always, even on exception
            if hasattr(loop, "event_bus") and hasattr(loop.event_bus, "set_replay_mode"):
                loop.event_bus.set_replay_mode(False)
            if hasattr(loop, "set_mode"):
                loop.set_mode("live")
            # PHASE 5.3 — Disable recorded LLM usage when exiting replay
            for system_name in ("world", "npc_system", "story_director", "scene_renderer"):
                system = getattr(loop, system_name, None)
                if system is not None and hasattr(system, "determinism"):
                    system.determinism.use_recorded_llm = False
                    system.determinism.replay_mode = False

        return loop

    def _apply_deterministic_mode(self, loop: Any) -> None:
        """Configure loop for pure deterministic replay.

        PHASE 5.1.5 — FIX #3: This ensures replay = original execution.
        
        Without this, replay may:
        - Call LLM with different results
        - Use current time instead of recorded timestamps
        - Generate random outputs
        
        Args:
            loop: Freshly created game loop instance.
        """
        # Disable LLM calls
        if hasattr(loop, "disable_llm"):
            loop.disable_llm()
        elif hasattr(loop, "llm_client"):
            if hasattr(loop.llm_client, "enabled"):
                loop.llm_client.enabled = False

        # Freeze time simulation
        if hasattr(loop, "freeze_time"):
            loop.freeze_time()
        elif hasattr(loop, "frozen_time"):
            loop.frozen_time = True

        # Use recorded outputs mode
        if hasattr(loop, "use_recorded_outputs"):
            loop.use_recorded_outputs()
        elif hasattr(loop, "replay_mode"):
            loop.replay_mode = True

    def _apply_event(self, loop: Any, event: Event) -> None:
        """Apply event to game systems.

        PHASE 2 FIX #1: Uses emit(replay=True) to prevent history duplication.
        PHASE 2 FIX #5: Dispatches to system handle_event() to actually
        reconstruct state instead of just logging events.

        PHASE 4 — REPLAY PARENT PRESERVATION (rpg-design.txt Issue #2):
        During replay, parent_id must be preserved exactly as recorded.
        DO NOT override parent_id or use _last_event_id during replay.

        Args:
            loop: The game loop instance to apply the event to.
            event: The event to replay.
        """
        # PHASE 2 FIX #1: emit with replay=True prevents history duplication
        # PHASE 4 — REPLAY PARENT PRESERVATION:
        # During replay, the event's parent_id must be preserved exactly.
        # The EventBus.emit() with replay=True will:
        # 1. NOT add to history (prevents duplication)
        # 2. Still add to timeline graph (preserves DAG structure)
        # 3. NOT override parent_id (preserves causal chain)
        loop.event_bus.emit(event, replay=True)

        # PHASE 2 FIX #5: Dispatch to system handlers so replay actually
        # reconstructs game state. Without this, replay is just logging.
        if self._config.dispatch_to_systems:
            if hasattr(loop, "world") and hasattr(loop.world, "handle_event"):
                loop.world.handle_event(event)

            if hasattr(loop, "npc_system") and hasattr(loop.npc_system, "handle_event"):
                loop.npc_system.handle_event(event)

            if hasattr(loop, "story_director") and hasattr(loop.story_director, "handle_event"):
                loop.story_director.handle_event(event)

    def _get_branch_from_events(
        self, leaf_event_id: str, event_map: Dict[str, Event]
    ) -> List[str]:
        """Reconstruct branch path by walking parent links in events.

        PHASE 3 — BRANCH SELECTION:
        Traverses the parent_id chain from leaf back to root,
        returning the ordered list of event IDs.

        Args:
            leaf_event_id: The leaf event to start tracing from.
            event_map: Map of event_id -> Event for quick lookup.

        Returns:
            List of event IDs from root to leaf (chronological order).
        """
        chain: List[str] = []
        current: Optional[str] = leaf_event_id
        visited: set[str] = set()

        while current is not None:
            if current in visited:
                break  # cycle guard
            visited.add(current)
            chain.append(current)
            event = event_map.get(current)
            current = event.parent_id if event else None

        return list(reversed(chain))
