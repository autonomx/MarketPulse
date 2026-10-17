"""PHASE 4 — TIMELINE QUERY API + BRANCH EVALUATION

Provides querying and evaluation capabilities for the timeline graph.
This module addresses the remaining critical issues from rpg-design.txt:
- Timeline Query API (get_events_by_tick, get_events_by_actor, get_causal_chain)
- Branch Evaluation Engine (score branches, choose best future path)
- Intent-level events support (npc_intent, belief_update, goal_change)
- Partial Replay / Simulation Mode (fast forward without rendering)

USAGE:
    engine = TimelineQueryEngine(event_bus, evaluator)
    
    # Query API
    events = engine.get_events_by_tick(5)
    events = engine.get_events_by_actor("player")
    chain = engine.get_causal_chain("e3")
    
    # Branch Evaluation
    score = engine.evaluate_branch(branch_events)
    best = engine.find_best_branch(candidate_branches)
    
    # Partial Replay (Simulation Mode)
    result = engine.simulate_branch(events, fast_forward=True)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, cast

from .event_bus import Event, EventBus, EventContext

# Re-export EventContext for backwards compatibility with tests
__all__ = [
    "BranchEvaluator",
    "BranchScore",
    "TimelineSnapshot",
    "TimelineQueryEngine",
    "DefaultBranchEvaluator",
    "EventContext",
    "create_intent_event",
]


class BranchEvaluator(Protocol):
    """Protocol for branch evaluation implementations.

    Branch evaluators score alternate timeline branches to help the AI
    determine the best narrative path forward.
    """

    def evaluate(self, events: List[Event]) -> float:
        """Evaluate a branch and return a score.

        Args:
            events: List of events in the branch.

        Returns:
            Score for the branch (higher is better).
        """
        ...


@dataclass
class BranchScore:
    """Score for a single branch.

    Attributes:
        branch_id: The leaf event ID identifying this branch.
        score: Numeric score (higher is better).
        event_count: Number of events in the branch.
        fork_point_id: The event ID where this branch diverges.
        causal_depth: Depth of the causal chain from root to leaf.
    """
    branch_id: str
    score: float
    event_count: int
    fork_point_id: Optional[str] = None
    causal_depth: int = 0


@dataclass
class TimelineSnapshot:
    """Snapshot of timeline state for save/load and debugging.

    Addresses rpg-design.txt Issue #5: Snapshot must include timeline state.
    Previously snapshots only saved world/NPC state, not:
    - timeline graph
    - seen event IDs
    - last_event_id

    This class captures all timeline state needed to reconstruct the DAG.

    Attributes:
        tick: The game tick when snapshot was taken.
        edges: List of (event_id, parent_id) tuples representing the DAG.
        seen_event_ids: Set of event IDs seen (for deduplication).
        fork_points: Dictionary of fork point -> [children].
        roots: List of root event IDs.
        labels: Optional event labels.
        annotations: Optional event annotations.
    """
    tick: int
    edges: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    seen_event_ids: Optional[set] = None
    fork_points: Dict[str, List[str]] = field(default_factory=dict)
    roots: List[str] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.seen_event_ids is None:
            self.seen_event_ids = set()


class TimelineQueryEngine:
    """Provides query and evaluation capabilities for the timeline graph.

    Addresses multiple critical issues from rpg-design.txt:
    1. Timeline Query API for debugging and AI reasoning
    2. Branch Evaluation Engine for choosing best narrative paths
    3. Partial Replay / Simulation Mode for fast future simulation
    4. Intent-level event support (npc_intent, belief_update, goal_change)

    Attributes:
        event_bus: The EventBus to query.
        evaluator: Optional BranchEvaluator for scoring branches.
    """

    def __init__(
        self,
        event_bus: EventBus,
        evaluator: Optional[BranchEvaluator] = None,
    ) -> None:
        """Initialize the query engine.

        Args:
            event_bus: The EventBus to query.
            evaluator: Optional BranchEvaluator for branch scoring.
                      If not provided, a default evaluator is used.
        """
        self.event_bus = event_bus
        self._evaluator = evaluator or DefaultBranchEvaluator()

    # -------------------------------------------------------
    # TIMELINE QUERY API (rpg-design.txt - Missing Features)
    # -------------------------------------------------------

    def get_events_by_tick(self, tick: int) -> List[Event]:
        """Get all events emitted during a specific tick.

        Needed for: debugging, AI reasoning, replay accuracy.

        Args:
            tick: The tick number to query.

        Returns:
            List of events with matching tick in payload.
        """
        results = []
        for event in self.event_bus.history():
            event_tick = event.payload.get("tick")
            if event_tick == tick:
                results.append(event)
        return results

    def get_events_by_actor(self, actor_id: str) -> List[Event]:
        """Get all events related to a specific actor.

        Needed for: understanding an actor's history, AI reasoning.

        Args:
            actor_id: The actor identifier (e.g., "npc_1", "player").

        Returns:
            List of events where actor_id appears in payload.
        """
        results = []
        actor_keys = ["actor_id", "npc_id", "player_id", "target_id", "actor"]
        for event in self.event_bus.history():
            # Check if actor_id appears in any common payload key
            for key in actor_keys:
                if event.payload.get(key) == actor_id or event.source == actor_id:
                    results.append(event)
                    break
        return results

    def get_causal_chain(self, event_id: str) -> List[str]:
        """Get the full causal chain from root to the given event.

        Needed for: debugging, AI reasoning, understanding event origins.

        Args:
            event_id: The event to trace back from.

        Returns:
            List of event IDs from root to the given event.
        """
        return self.event_bus.timeline.get_branch(event_id)

    def get_tick_groups(self) -> Dict[int, List[str]]:
        """Group all events by tick number.

        Returns:
            Dictionary mapping tick numbers to lists of event IDs.
        """
        groups: Dict[int, List[str]] = {}
        for event in self.event_bus.history():
            tick = event.payload.get("tick")
            if tick is not None:
                if tick not in groups:
                    groups[tick] = []
                groups[tick].append(cast(str, event.event_id))
        return groups

    # -------------------------------------------------------
    # BRANCH EVALUATION ENGINE (rpg-design.txt - Killer Feature)
    # -------------------------------------------------------

    def evaluate_branch(self, events: List[Event]) -> float:
        """Evaluate a branch and return a score.

        This is the killer feature for "what if" simulation:
        the AI evaluates multiple branches and chooses the best path.

        Args:
            events: Events in the candidate branch.

        Returns:
            Score for the branch (higher is better).
        """
        return self._evaluator.evaluate(events)

    def find_best_branch(
        self,
        candidate_branches: Dict[str, List[Event]],
    ) -> BranchScore:
        """Find the best branch among multiple candidates.

        Needed for: AI simulation, choosing best narrative path.

        Args:
            candidate_branches: Dictionary mapping branch_leaf_id to events.

        Returns:
            BranchScore for the highest-scoring branch.
        """
        scores = []
        for branch_id, events in candidate_branches.items():
            score = self._evaluator.evaluate(events)
            depth = 0
            if events:
                chain = self.event_bus.timeline.get_branch(cast(str, events[-1].event_id))
                depth = len(chain)

            # Find fork point (first event with multiple children in chain)
            fork_point = None
            if len(chain := self.event_bus.timeline.get_branch(cast(str, events[-1].event_id))) > 1:
                for eid in chain:
                    node = self.event_bus.timeline.get_node(eid)
                    if node and len(node.children) > 1:
                        fork_point = eid
                        break

            scores.append(BranchScore(
                branch_id=branch_id,
                score=score,
                event_count=len(events),
                fork_point_id=fork_point,
                causal_depth=depth,
            ))

        if not scores:
            return BranchScore(branch_id="", score=0.0, event_count=0)

        return max(scores, key=lambda s: s.score)

    # -------------------------------------------------------
    # PARTIAL REPLAY / SIMULATION MODE (rpg-design.txt - Missing)
    # -------------------------------------------------------

    def simulate_branch(
        self,
        events: List[Event],
        fast_forward: bool = True,
    ) -> Dict[str, Any]:
        """Simulate a branch without full rendering.

        Needed for: fast simulation of 100+ futures, AI planning.
        This is the "partial replay" mode from rpg-design.txt.

        When fast_forward=True:
        - No scene rendering
        - Minimal state updates
        - Just process events for their side effects

        Args:
            events: Events to simulate.
            fast_forward: If True, skip rendering for speed.

        Returns:
            Dictionary with simulation results.
        """
        result: dict[str, Any] = {
            "events_processed": 0,
            "final_tick": None,
            "branch_score": 0.0,
            "causal_chains": [],
        }

        # Sort events for deterministic simulation
        sorted_events = sorted(
            events,
            key=lambda e: (
                e.payload.get("tick", 0),
                e.timestamp or 0,
                e.event_id or "",
            ),
        )

        for event in sorted_events:
            result["events_processed"] += 1
            tick = event.payload.get("tick")
            if tick is not None:
                result["final_tick"] = max(
                    result["final_tick"] or 0, tick
                )

            # Build causal chain for leaf events
            if not self.event_bus.timeline.get_node(cast(str, event.event_id)):
                self.event_bus.timeline.add_event(
                    cast(str, event.event_id), event.parent_id
                )

        if sorted_events:
            # Score the branch
            result["branch_score"] = self._evaluator.evaluate(sorted_events)

            # Get causal chain for the last event
            last_event = sorted_events[-1]
            try:
                chain = self.event_bus.timeline.get_branch(cast(str, last_event.event_id))
                result["causal_chains"] = chain
            except KeyError:
                pass

        return result

    # -------------------------------------------------------
    # SNAPSHOT CAPTURE (rpg-design.txt Issue #5)
    # -------------------------------------------------------

class DefaultBranchEvaluator:
    """Default branch evaluation implementation.

    Scores branches based on:
    - Event count (more events = richer narrative)
    - Branching factor (more forks = more interesting story paths)
    - Actor diversity (more unique actors = more dynamic story)
    """

    def evaluate(self, events: List[Event]) -> float:
        """Evaluate a branch and return a score.

        Args:
            events: List of events in the branch.

        Returns:
            Score from 0.0 to 1.0 (higher is better).
        """
        if not events:
            return 0.0

        # Factor 1: Event count (normalized, max 50 events)
        event_score = min(len(events) / 50.0, 1.0)

        # Factor 2: Actor diversity
        actors = set()
        for e in events:
            if e.source:
                actors.add(e.source)
            for key in ["actor_id", "npc_id", "player_id", "target_id", "actor"]:
                val = e.payload.get(key)
                if val:
                    actors.add(str(val))
        actor_diversity = min(len(actors) / 10.0, 1.0)

        # Factor 3: Event type diversity
        event_types = set(e.type for e in events)
        type_diversity = min(len(event_types) / 8.0, 1.0)

        # Weighted combination
        score = (
            0.3 * event_score +
            0.4 * actor_diversity +
            0.3 * type_diversity
        )

        return min(max(score, 0.0), 1.0)


def create_intent_event(
    event_type: str,
    actor_id: str,
    intent_data: Dict[str, Any],
    parent_id: Optional[str] = None,
    source: Optional[str] = None,
) -> Event:
    """Create an intent-level event.

    Addresses rpg-design.txt Issue #6: Intent-Level Events.
    Instead of mechanical events like "npc_move", create intent-level events:
    - "npc_intent" — what an NPC intends to do
    - "belief_update" — when beliefs change
    - "goal_change" — when goals shift

    Args:
        event_type: Type of intent event (npc_intent, belief_update, goal_change).
        actor_id: The actor this intent relates to.
        intent_data: Dictionary with intent details.
        parent_id: Optional parent event ID.
        source: Optional source system identifier.

    Returns:
        Event with proper intent-level structure.
    """
    return Event(
        type=event_type,
        payload={
            "actor_id": actor_id,
            "intent": intent_data,
        },
        source=source,
        parent_id=parent_id,
    )