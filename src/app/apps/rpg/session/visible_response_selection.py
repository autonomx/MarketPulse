"""Select the canonical visible response for an RPG turn."""
from __future__ import annotations

from .visible_response_core import (
    _safe_dict as _safe_dict,
    _safe_str as _safe_str,
    _select_final_visible_presentation as _select_final_visible_presentation_core,
)


def _select_final_visible_presentation(
    final_result,
    *,
    runtime_narration_payload,
    prior_narration,
    prior_npc,
    prior_llm_called,
):
    final_result = _safe_dict(final_result)
    runtime_narration_payload = _safe_dict(runtime_narration_payload)
    from app.apps.rpg.session.fast_combat_presentation_hook import (
        select_fast_combat_presentation,
    )

    fast_combat = select_fast_combat_presentation(
        final_result,
        runtime_narration_payload=runtime_narration_payload,
    )
    if fast_combat:
        return fast_combat

    canonical_text = _safe_str(runtime_narration_payload.get("narration")).strip()
    if (
        runtime_narration_payload.get("canonical_response_source")
        == "rpg_response_generator_v1"
        and canonical_text
    ):
        return {
            "source": "canonical_runtime_response",
            "narration": canonical_text,
            "npc": _safe_dict(runtime_narration_payload.get("npc")),
            "llm_called": (
                _safe_str(runtime_narration_payload.get("source"))
                == "provider_runtime_narration"
            ),
            "runtime_payload_source": _safe_str(
                runtime_narration_payload.get("source")
            ),
        }

    return _select_final_visible_presentation_core(
        final_result,
        runtime_narration_payload=runtime_narration_payload,
        prior_narration=prior_narration,
        prior_npc=prior_npc,
        prior_llm_called=prior_llm_called,
    )


__all__ = [
    "_select_final_visible_presentation",
]
