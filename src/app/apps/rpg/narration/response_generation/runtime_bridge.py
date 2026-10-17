from __future__ import annotations

from typing import Any, Mapping

from .strict_pipeline import StrictRpgProductionResponsePipeline


_PIPELINE = StrictRpgProductionResponsePipeline()


def build_runtime_narration_payload(
    provider: Any = None,
    player_action: str = "",
    simulation_state: Mapping[str, Any] | None = None,
    turn_contract: Mapping[str, Any] | None = None,
    prefer_provider: bool = True,
    max_tokens: int | None = None,
    max_provider_attempts: int | None = None,
) -> dict[str, Any]:
    """Canonical runtime entry point used by every normal apply-turn path."""

    return _PIPELINE.build_runtime_payload(
        provider=provider,
        player_action=player_action,
        simulation_state=simulation_state,
        turn_contract=turn_contract,
        prefer_provider=prefer_provider,
        max_tokens=max_tokens,
        max_provider_attempts=max_provider_attempts,
    )


__all__ = ["build_runtime_narration_payload"]
