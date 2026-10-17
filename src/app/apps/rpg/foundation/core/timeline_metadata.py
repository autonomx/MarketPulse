"""PHASE 3 — TIMELINE METADATA (PATCH 7)

Optional metadata layer for the timeline graph.
Provides labels, annotations, and developer notes for events
to support debugging and narrative context.

USAGE:
    meta = TimelineMetadata()
    meta.label("e1", "Player arrives at castle")
    meta.annotate("e1", "First time player enters the scene")
    meta.get_label("e1")  # "Player arrives at castle"
    meta.get_note("e1")   # "First time player enters the scene"
"""

from __future__ import annotations

from typing import Dict, Optional


class TimelineMetadata:
    """Metadata store for timeline events.

    Provides optional labeling and annotation capabilities
    on top of the raw TimelineGraph structure.

    Example:
        meta = TimelineMetadata()
        meta.label("e1", "Player arrives at castle")
        meta.annotate("e2", "Critical decision point — guard fight or bribe")

        # Later during debugging:
        print(meta.get_label("e2"))  # quick context
        print(meta.get_note("e2"))   # developer notes
    """

    def __init__(self) -> None:
        """Initialize empty metadata store."""
        self.labels: Dict[str, str] = {}   # event_id -> human-readable label
        self.notes: Dict[str, str] = {}    # event_id -> developer/ai note

    def label(self, event_id: str, text: str) -> None:
        """Assign a human-readable label to an event.

        Args:
            event_id: The event identifier.
            text: Short descriptive label.
        """
        self.labels[event_id] = text

    def annotate(self, event_id: str, note: str) -> None:
        """Attach a developer or AI note to an event.

        Args:
            event_id: The event identifier.
            note: Free-form annotation text.
        """
        self.notes[event_id] = note

    def get_label(self, event_id: str) -> Optional[str]:
        """Retrieve the label for an event.

        Args:
            event_id: The event identifier.

        Returns:
            The label string or None if not set.
        """
        return self.labels.get(event_id)

    def get_note(self, event_id: str) -> Optional[str]:
        """Retrieve the annotation note for an event.

        Args:
            event_id: The event identifier.

        Returns:
            The note string or None if not set.
        """
        return self.notes.get(event_id)

    def clear(self) -> None:
        """Remove all metadata."""
        self.labels.clear()
        self.notes.clear()

    def __repr__(self) -> str:
        return (
            f"TimelineMetadata(labels={len(self.labels)}, notes={len(self.notes)})"
        )