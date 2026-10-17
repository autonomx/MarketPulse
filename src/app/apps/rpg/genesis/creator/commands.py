from __future__ import annotations

from typing import Any



class GMCommandProcessor:
    # ------------------------------------------------------------------
    # Entity validation helpers
    # ------------------------------------------------------------------
    def _location_exists(self, coherence_core: Any, location_id: str) -> bool:
        facts = coherence_core.get_known_facts(location_id)
        return bool(facts and facts.get("facts"))

    # ------------------------------------------------------------------
    # Legacy command handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 7.1 targeted command handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 7.2 gameplay-control command handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 7.8 arc-control command handlers
    # ------------------------------------------------------------------

