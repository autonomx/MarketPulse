from __future__ import annotations

from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


AMBIENT_WAIT_MARKERS = (
    "wait",
    "listen",
    "listen to",
    "idle",
    "observe",
    "watch",
    "linger",
    "pass time",
    "let time pass",
)


SERVICE_REQUEST_MARKERS = (
    "buy",
    "purchase",
    "rent",
    "room to rent",
    "rent a room",
    "rent room",
    "meal",
    "food",
    "rumor",
    "repair",
    "what do you sell",
    "what she sells",
    "what he sells",
    "show me",
    "price",
    "cost",
)


def is_ambient_wait_or_listen_intent(player_input: str) -> bool:
    """Return True for passive wait/listen/observe turns.

    This deliberately excludes service/commercial requests. The important
    false-positive this prevents is:

      "I wait and listen to the room"

    being interpreted as a lodging/room service inquiry.
    """
    lower = _safe_str(player_input).strip().lower()
    if not lower:
        return False

    has_ambient_marker = any(marker in lower for marker in AMBIENT_WAIT_MARKERS)
    if not has_ambient_marker:
        return False

    has_service_marker = any(marker in lower for marker in SERVICE_REQUEST_MARKERS)
    if has_service_marker:
        return False

    return True


def is_room_context_ambient_not_lodging(player_input: str) -> bool:
    lower = _safe_str(player_input).strip().lower()
    if not lower:
        return False
    if "room" not in lower:
        return False
    return is_ambient_wait_or_listen_intent(lower)