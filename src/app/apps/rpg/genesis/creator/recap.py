from __future__ import annotations

from typing import Any


class RecapBuilder:
    def build_canon_summary(self, coherence_core: Any, creator_canon_state: Any | None = None) -> dict:
        canon_facts = []
        if creator_canon_state is not None:
            canon_facts = [f.to_dict() for f in creator_canon_state.list_facts()]
        return {
            "canon_facts": canon_facts,
            "scene_summary": coherence_core.get_scene_summary(),
        }

