"""PHASE 5.6 — LLM Boundary Hardening

Central deterministic boundary for all LLM access.

Rules:
- No subsystem should call a raw LLM client directly.
- All LLM access must pass through LLMGateway.
- In live mode, calls may be recorded.
- In replay/simulation mode, recorded outputs must be used.
- If replay requests a missing output, fail hard.
- prompt/response records become snapshot-able engine state
- effect policy and LLM policy become aligned
- validation can prove whether a subsystem is replay-safe
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .determinism import DeterminismConfig
from .effects import EffectManager
from .llm_recording import DeterministicLLMClient, LLMRecorder


@dataclass
class LLMCallSpec:
    """Structured description of an LLM call."""
    method: str
    prompt: Any
    context: Optional[Dict[str, Any]] = None
    model: Optional[str] = None


class LLMGateway:
    """Single deterministic gateway for all LLM-backed work."""

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        recorder: Optional[LLMRecorder] = None,
        determinism: Optional[DeterminismConfig] = None,
        effect_manager: Optional[EffectManager] = None,
    ):
        self.inner_client = llm_client
        self.recorder = recorder or LLMRecorder()
        self.determinism = determinism or DeterminismConfig()
        self.effect_manager = effect_manager

        self.client = None
        if llm_client is not None:
            self.client = DeterministicLLMClient(
                inner_client=llm_client,
                recorder=self.recorder,
                determinism=self.determinism,
                effect_manager=self.effect_manager,
            )

    def set_mode(self, mode: str) -> None:
        """Apply mode-sensitive deterministic behavior."""
        if mode in ("replay", "simulation"):
            self.determinism.replay_mode = True
            self.determinism.use_recorded_llm = True
        else:
            self.determinism.replay_mode = False
            # Do not force record_llm here; caller controls that.
            self.determinism.use_recorded_llm = False

    def set_effect_manager(self, effect_manager: EffectManager) -> None:
        self.effect_manager = effect_manager
        if self.client is not None:
            self.client.effect_manager = effect_manager

    def set_llm_recorder(self, recorder: LLMRecorder) -> None:
        self.recorder = recorder
        if self.client is not None:
            self.client.recorder = recorder

    def set_determinism(self, determinism: DeterminismConfig) -> None:
        self.determinism = determinism
        if self.client is not None:
            self.client.det = determinism

    def call(
        self,
        method: str,
        prompt: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute an LLM call through the deterministic boundary."""
        if self.client is None:
            raise RuntimeError("LLMGateway has no LLM client configured.")

        if method == "complete":
            return self.client.complete(prompt, context=context)
        if method == "chat":
            return self.client.chat(prompt, context=context)
        if method == "generate":
            return self.client.generate(prompt, context=context)

        raise ValueError(f"Unsupported LLM method: {method}")