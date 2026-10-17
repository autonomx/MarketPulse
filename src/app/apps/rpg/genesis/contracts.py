"""What the genesis context asks of the contexts above it (R-3).

Genesis builds worlds and campaigns and never imports the session pipeline.
The World Forge reads and writes campaign sessions through ``CampaignSessions``,
which its callers (the edge and the session) pass in.
"""
from __future__ import annotations

from typing import Any, Protocol


class CampaignSessions(Protocol):
    """The campaign session store, as the session context runs it."""

    def load(self, session_id: str) -> dict[str, Any] | None:
        """The session with its interaction events replayed, or None."""
        ...

    def save(self, session: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
        ...

    def archive(self, session_id: str) -> dict[str, Any]:
        ...

    def new_game_request(self, payload: dict[str, Any]) -> Any:
        """The session's validated new-game request for a payload."""
        ...

    def create_new_game(self, request: Any) -> dict[str, Any]:
        """Create and save a new game session from a ``new_game_request``."""
        ...

    def build_new_game(self, request: Any) -> dict[str, Any]:
        """Build an unsaved new game session from a ``new_game_request``."""
        ...

    def save_created(self, session: dict[str, Any]) -> dict[str, Any]:
        """Save a session built by ``build_new_game``; returns the creation result."""
        ...
