from __future__ import annotations

import logging

from typing import Any

from app.apps.rpg.session.deferred_narration_guard import suppress_provider_runtime_narration

logger = logging.getLogger(__name__)


def get_runtime_llm_provider() -> Any:
    if suppress_provider_runtime_narration():
        return None
    """Return the centralized app LLM provider if available.

    Keep this tiny and defensive so tests can monkeypatch it easily.
    """
    try:
        from app.apps.rpg.foundation.provider_access import routed_provider

        return routed_provider()
    except Exception:
        logger.debug("suppressed error in %s", "get_runtime_llm_provider", exc_info=True)
        return None
    return None