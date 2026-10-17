"""Request-scoped deadlines shared by Chat routing entry points."""
from __future__ import annotations

import logging

import math
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)


def _provider_key(value: str | None) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("llm:") else text


def provider_turn_deadline(
    provider_id: str | None,
    *,
    session_provider_id: str | None = None,
    existing_deadline_at: float | None = None,
) -> float | None:
    """Return an absolute monotonic deadline for one Chat turn.

    An outer request may pass an existing deadline. Otherwise the configured
    provider timeout becomes the turn budget. Missing or invalid provider
    configuration returns ``None`` so parser construction can use its
    provider-independent safety fallback.
    """

    if existing_deadline_at is not None:
        try:
            deadline = float(existing_deadline_at)
        except (TypeError, ValueError):
            deadline = math.nan
        if math.isfinite(deadline):
            return deadline

    resolved_id = _provider_key(provider_id or session_provider_id)
    if not resolved_id:
        return None
    try:
        from app.providers import service as provider_service

        provider = provider_service.get_provider(resolved_id)
        configured: Any = getattr(getattr(provider, "config", None), "timeout", None)
        timeout = float(configured)
    except (TypeError, ValueError, AttributeError):
        return None
    except Exception:
        # Provider discovery is an optional input to routing. A registry or
        # configuration failure must not turn a Chat turn into a 500 before
        # SemanticTask v2 can apply its deterministic fail-closed behavior.
        logger.debug("suppressed error in %s", "provider_turn_deadline", exc_info=True)
        return None
    if not math.isfinite(timeout) or timeout <= 0:
        return None
    return monotonic() + timeout


def remaining_turn_seconds(deadline_at: float | None) -> float | None:
    """Return the positive remaining seconds for an absolute deadline."""

    if deadline_at is None:
        return None
    try:
        remaining = float(deadline_at) - monotonic()
    except (TypeError, ValueError):
        return None
    return remaining if remaining > 0 else 0.0


__all__ = ["provider_turn_deadline", "remaining_turn_seconds"]
