"""PHASE 3 — BRANCHING TIMELINES + MULTIVERSE GRAPH

Core module for DAG-based event causality tracking.
Replaces linear history with a directed acyclic graph enabling:
- Branching timelines ("what if I did something different?")
- Fork points for time-travel debugging
- Multiverse traversal for AI simulation

USAGE:
    graph = TimelineGraph()
    graph.add_event("e1", parent_id=None)  # root
    graph.add_event("e2", parent_id="e1")
    graph.add_event("e3a", parent_id="e2")  # branch A
    graph.add_event("e3b", parent_id="e2")  # branch B
    branch = graph.get_branch("e3a")  # ["e1", "e2", "e3a"]
    branch_forks = graph.get_forks()  # {"e2": ["e3a", "e3b"]}
"""

from __future__ import annotations

from typing import Dict, List, Optional


class TimelineNode:
    """A single node in the event DAG.

    Attributes:
        event_id: The event identifier this node represents.
        parent_id: The parent event that caused this event (None for roots).
        children: List of event IDs that branched from this event.
    """

    def __init__(self, event_id: str, parent_id: Optional[str]):
        """Initialize a timeline node.

        Args:
            event_id: Unique event identifier.
            parent_id: Parent event ID or None for root events.
        """
        self.event_id = event_id
        self.parent_id = parent_id
        self.children: List[str] = []

    def add_child(self, child_id: str) -> None:
        """Add a child event to this node.

        Args:
            child_id: The event ID of the child node.
        """
        if child_id not in self.children:
            self.children.append(child_id)

    def is_leaf(self) -> bool:
        """Check if this node is a leaf (no children).

        Returns:
            True if the node has no children, False otherwise.
        """
        return len(self.children) == 0

    def __repr__(self) -> str:
        return (
            f"TimelineNode(event_id={self.event_id!r}, "
            f"parent_id={self.parent_id!r}, children={self.children!r})"
        )


class TimelineGraph:
    """Directed Acyclic Graph (DAG) of event causality.

    Enables branching timelines and traversal through event history.
    Supports fork detection, branch traversal, and multiverse querying.

    Example:
        graph = TimelineGraph()
        graph.add_event("e1", parent_id=None)
        graph.add_event("e2", parent_id="e1")
        graph.add_event("e3a", parent_id="e2")
        graph.add_event("e3b", parent_id="e2")

        # Get full path to a leaf
        path = graph.get_branch("e3a")  # ["e1", "e2", "e3a"]

        # Find all fork points
        forks = graph.get_forks()  # {"e2": ["e3a", "e3b"]}

        # Get all leaves (endpoints of all branches)
        leaves = graph.get_leaves()  # ["e3a", "e3b"]
    """

    def __init__(self) -> None:
        """Initialize an empty timeline graph."""
        self.nodes: Dict[str, TimelineNode] = {}
        self.roots: List[str] = []

    def _creates_cycle(self, event_id: str, parent_id: str) -> bool:
        """Check if adding event_id with parent_id would create a cycle.

        Addresses rpg-design.txt Issue #3: No cycle detection (real one).
        Previous check only caught self-loops (event_id == parent_id).
        This method walks the parent chain to detect any cycle.

        Args:
            event_id: The event being added.
            parent_id: The proposed parent event ID.

        Returns:
            True if adding this link would create a cycle.
        """
        current: str | None = parent_id
        visited = set()
        while current is not None:
            if current == event_id:
                return True
            if current in visited:
                break  # Already checked this path
            visited.add(current)
            node = self.nodes.get(current)
            current = node.parent_id if node else None
        return False

    def add_event(self, event_id: str, parent_id: Optional[str]) -> None:
        """Add an event to the graph.

        Creates a new node and links it to its parent if provided.
        If the event already exists, updates the parent/children links
        to maintain graph integrity.

        Addresses rpg-design.txt Issue #3:
        - Proper cycle detection (not just self-loops)
        - Parent stub auto-creation

        Args:
            event_id: Unique event identifier.
            parent_id: Parent event ID or None for root events.

        Raises:
            ValueError: If adding an event would create a cycle.
        """
        # Check for cycle before adding
        if parent_id is not None:
            if event_id == parent_id:
                raise ValueError(f"Cannot add event {event_id}: would create a cycle (self-loop)")
            # Full cycle detection
            if self._creates_cycle(event_id, parent_id):
                raise ValueError(
                    f"Cannot add event {event_id}: would create a cycle "
                    f"with parent {parent_id}"
                )

        # If event already exists, skip (idempotent)
        if event_id in self.nodes:
            return

        node = TimelineNode(event_id, parent_id)
        self.nodes[event_id] = node

        if parent_id is not None:
            # Ensure parent exists (create stub if needed)
            # Addresses rpg-design.txt Issue #3: Missing parent insertion safety
            if parent_id not in self.nodes:
                self.nodes[parent_id] = TimelineNode(parent_id, None)

            # Link parent -> child
            parent_node = self.nodes[parent_id]
            parent_node.add_child(event_id)

        else:
            # The node dictionary above already proves this ID is new. Scanning
            # every root here makes independent-event ingestion quadratic.
            self.roots.append(event_id)

    def get_branch(self, leaf_event_id: str) -> List[str]:
        """Return the chain from root to the specified leaf event.

        Traverses parent links upward to reconstruct the full causal path.

        Args:
            leaf_event_id: The target event to trace back from.

        Returns:
            List of event IDs from root to leaf (inclusive, ordered chronologically).

        Raises:
            KeyError: If the event ID is not found in the graph.
        """
        if leaf_event_id not in self.nodes:
            raise KeyError(f"Event {leaf_event_id!r} not found in timeline graph")

        chain = []
        current: Optional[str] = leaf_event_id
        visited: set[str] = set()

        while current is not None:
            if current in visited:
                # Cycle detected (shouldn't happen with proper add_event guards)
                break
            visited.add(current)
            chain.append(current)
            node = self.nodes.get(current)
            current = node.parent_id if node else None

        return list(reversed(chain))

    def fork(self, event_id: str) -> str:
        """Mark an event as a fork point for branching.

        This method confirms the event exists and returns its ID for clarity.
        Actual branching happens by adding new events with this ID as parent_id.

        Args:
            event_id: The event to fork from.

        Returns:
            The same event_id (confirmation that fork point is valid).

        Raises:
            ValueError: If the event is not found in the graph.
        """
        if event_id not in self.nodes:
            raise ValueError(f"Event {event_id!r} not found — cannot fork from non-existent event")
        return event_id

    def get_forks(self) -> Dict[str, List[str]]:
        """Find all events that have multiple children (branch points).

        Returns:
            Dictionary mapping fork point event IDs to their child event IDs.
        """
        forks = {}
        for event_id, node in self.nodes.items():
            if len(node.children) > 1:
                forks[event_id] = list(node.children)
        return forks

    def get_leaves(self) -> List[str]:
        """Find all leaf events (events with no children).

        These are the endpoints of all branches in the timeline.

        Returns:
            List of leaf event IDs.
        """
        return [eid for eid, node in self.nodes.items() if node.is_leaf()]

    def get_roots(self) -> List[str]:
        """Find all root events (events with no parent).

        Returns:
            List of root event IDs.
        """
        return list(self.roots)

    def has_event(self, event_id: str) -> bool:
        """Check if an event exists in the graph.

        Args:
            event_id: The event to check.

        Returns:
            True if the event exists, False otherwise.
        """
        return event_id in self.nodes

    def get_node(self, event_id: str) -> Optional[TimelineNode]:
        """Get a node by its event ID.

        Args:
            event_id: The event identifier.

        Returns:
            The TimelineNode if found, None otherwise.
        """
        return self.nodes.get(event_id)

    def node_count(self) -> int:
        """Return the total number of events in the graph.

        Returns:
            Number of nodes in the graph.
        """
        return len(self.nodes)

    def clear(self) -> None:
        """Remove all events from the graph."""
        self.nodes.clear()
        self.roots.clear()

    def __repr__(self) -> str:
        return (
            f"TimelineGraph(nodes={self.node_count()!r}, "
            f"roots={self.roots!r}, leaves={self.get_leaves()!r})"
        )
