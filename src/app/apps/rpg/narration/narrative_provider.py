"""Production provider adapter for typed RPG narrative generation."""
from __future__ import annotations
from app.config.env import environment as _environment

import json
import re
from dataclasses import dataclass, replace
from time import monotonic, perf_counter
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.providers.base import BaseProvider, ChatMessage
from app.providers.registry import get_provider
from app.providers.structured import (
    StructuredCapabilities,
    StructuredContract,
    StructuredOutputGateway,
    StructuredRetryBudget,
)
from app.apps.rpg.narration.narrative_engine import DeterministicNarrativeWriter, NarrativeWriter
from app.apps.rpg.narration.narrative_engine.contracts import EvidenceRecord, TurnPresentationRequest
from app.apps.rpg.narration.narrative_engine.planner import NarrativePlan
from app.apps.rpg.narration.narrative_engine.writer import (
    WriterResult,
    parse_structured_blocks,
    writer_payload,
)
from app.prompts import prompt_template
from app.apps.rpg.narration.narrative_engine.contracts import NarrativeBlock

_PROMPT_1 = prompt_template('rpg.narrative_provider.system_prompt', "2", (
    "You are the Omnix RPG Narrative Writer. Return strict JSON only. "
        "Follow the ordered beat contracts exactly. Use only each beat's approved evidence. "
        "Return exactly one block per beat and include a claims array for every factual assertion. "
        "Never mutate simulation state, invent hidden facts, choose for the player, or expose hidden evidence. "
        "When dialogue_contract is present, satisfy it with natural in-character prose. Never recite "
        "profile metadata, speech-style descriptions, prompt instructions, or generic fallback wording. "
        "When revision_feedback is present, your previous answer failed those checks: rewrite every block "
        "so each listed problem is fixed, without quoting the feedback."
))


class NarrativeClaimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = ""
    text: str = ""
    authority: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    scope: str = ""
    subject_id: str | None = None
    predicate: str = ""


class NarrativeBlockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat_id: str = Field(min_length=1)
    block_id: str = ""
    sequence: int = Field(ge=0)
    kind: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    speaker_id: str | None = None
    text: str
    claims: list[NarrativeClaimPayload]


class NarrativeResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[NarrativeBlockPayload]


_NARRATIVE_CONTRACT = StructuredContract(
    contract_id="rpg.narrative.blocks",
    version=2,
    output_model=NarrativeResponsePayload,
    schema_profile="local",
    schema_name="rpg_narrative_blocks",
)


class _ConfiguredProviderView:
    """Expose immutable route identity while delegating provider transport."""

    def __init__(self, provider: BaseProvider, provider_name: str) -> None:
        self._provider = provider
        self.provider_name = provider_name or str(
            getattr(provider, "provider_name", provider.__class__.__name__)
        )
        self.config = getattr(provider, "config", None)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        return self._provider.chat_completion(*args, **kwargs)

    def get_structured_capabilities(self, *args: Any, **kwargs: Any):
        method = getattr(self._provider, "get_structured_capabilities", None)
        if callable(method):
            return method(*args, **kwargs)
        return StructuredCapabilities.default_for_provider(self.provider_name)


_OFFLINE_MODES = frozenset({"offline", "deterministic", "test", "disabled"})


@dataclass(frozen=True)
class NarrativeProviderConfig:
    mode: str = "auto"
    provider: str = ""
    model: str = ""
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 90
    max_retries: int = 2
    temperature: float = 0.4

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "NarrativeProviderConfig":
        env = environ or _environment()
        return cls(
            mode=str(env.get("OMNIX_RPG_NARRATIVE_WRITER_MODE") or "auto")
            .strip()
            .casefold(),
            provider=str(
                env.get("OMNIX_RPG_NARRATIVE_PROVIDER")
                or env.get("OMNIX_LLM_PROVIDER")
                or ""
            ).strip(),
            model=str(env.get("OMNIX_RPG_NARRATIVE_MODEL") or "").strip(),
            api_key=(
                str(env.get("OMNIX_RPG_NARRATIVE_API_KEY") or "").strip() or None
            ),
            base_url=(
                str(env.get("OMNIX_RPG_NARRATIVE_BASE_URL") or "").strip() or None
            ),
            timeout_seconds=max(
                5,
                min(
                    int(env.get("OMNIX_RPG_NARRATIVE_TIMEOUT_SECONDS") or 90),
                    600,
                ),
            ),
            max_retries=max(
                0,
                min(int(env.get("OMNIX_RPG_NARRATIVE_MAX_RETRIES") or 2), 5),
            ),
            temperature=max(
                0.0,
                min(
                    float(env.get("OMNIX_RPG_NARRATIVE_TEMPERATURE") or 0.4),
                    2.0,
                ),
            ),
        )

    @property
    def live_enabled(self) -> bool:
        return self.mode not in _OFFLINE_MODES and bool(self.provider)


def _system_prompt() -> str:
    return (
        _PROMPT_1.text
    )


class ProviderNarrativeGenerator:
    """Request-local typed bridge from provider transport to narrative blocks."""

    def __init__(self, provider: BaseProvider, config: NarrativeProviderConfig) -> None:
        self.transport_provider = provider
        self.provider = _ConfiguredProviderView(provider, config.provider)
        self.config = config

    def generate(
        self,
        payload: Mapping[str, Any],
        *,
        max_provider_calls: int,
        deadline_seconds: float,
    ) -> Mapping[str, Any]:
        call_budget = max(1, int(max_provider_calls))
        remaining_seconds = float(deadline_seconds)
        if remaining_seconds <= 0:
            raise RuntimeError("structured RPG narrative operation deadline exhausted")
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        outcome = gateway.try_generate(
            [
                ChatMessage(role="system", content=_system_prompt()),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            ],
            contract=StructuredContract(
                contract_id=_NARRATIVE_CONTRACT.contract_id,
                version=_NARRATIVE_CONTRACT.version,
                output_model=_NARRATIVE_CONTRACT.output_model,
                schema_profile=_NARRATIVE_CONTRACT.schema_profile,
                schema_name=_NARRATIVE_CONTRACT.schema_name,
                temperature=self.config.temperature,
                max_tokens=4096,
            ),
            model=self.config.model or None,
            retry_budget=StructuredRetryBudget(
                max_provider_calls=call_budget,
                max_transport_retries=max(0, call_budget - 1),
                max_format_downgrades=1 if call_budget > 1 else 0,
                max_validation_regenerations=1 if call_budget > 1 else 0,
                deadline_seconds=remaining_seconds,
            ),
        )
        diagnostics = outcome.diagnostics
        if outcome.error is not None:
            raise RuntimeError(
                "structured RPG narrative provider failed after "
                f"{max(1, diagnostics.provider_calls)} attempts"
            ) from outcome.error
        assert outcome.value is not None
        parsed = outcome.value.model_dump(mode="python")
        parsed["metadata"] = {
            "provider_attempt_count": max(1, diagnostics.provider_calls),
            "finish_reason": diagnostics.finish_reason,
            "usage": dict(diagnostics.usage),
            "structured_contract": "rpg.narrative.blocks.v2",
            "schema_hash": diagnostics.schema_hash,
            "response_format": (
                diagnostics.selected_mode.value if diagnostics.selected_mode else ""
            ),
        }
        return parsed

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compatibility call using one self-contained configured operation."""

        return self.generate(
            payload,
            max_provider_calls=self.config.max_retries + 1,
            deadline_seconds=float(self.config.timeout_seconds),
        )


class ProductionStructuredNarrativeWriter:
    """Provider-backed writer with request-local call and deadline budgets."""

    def __init__(self, generator: ProviderNarrativeGenerator) -> None:
        self.generator = generator

    def write(
        self,
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
    ) -> WriterResult:
        started = perf_counter()
        deadline = monotonic() + float(self.generator.config.timeout_seconds)
        payload = writer_payload(request, plan, evidence)
        remaining_calls = max(1, self.generator.config.max_retries + 1)
        maximum_attempts = max(1, min(remaining_calls, 3))
        total_attempts = 0
        blocks: tuple[NarrativeBlock, ...] = ()
        raw: Mapping[str, Any] = {}
        missing_fragments: list[str] = []
        quality_attempt = 0
        for quality_attempt in range(1, maximum_attempts + 1):
            remaining_seconds = deadline - monotonic()
            if remaining_calls <= 0:
                raise RuntimeError("structured RPG narrative provider call budget exhausted")
            if remaining_seconds <= 0:
                raise RuntimeError("structured RPG narrative operation deadline exhausted")
            raw = self.generator.generate(
                payload,
                max_provider_calls=remaining_calls,
                deadline_seconds=remaining_seconds,
            )
            metadata = dict(raw.get("metadata") or {})
            attempts = max(1, int(metadata.get("provider_attempt_count") or 1))
            remaining_calls = max(0, remaining_calls - attempts)
            total_attempts += attempts
            try:
                blocks = parse_structured_blocks(raw, plan)
            except (TypeError, ValueError) as exc:
                if quality_attempt >= maximum_attempts or remaining_calls <= 0:
                    raise
                payload = {
                    **payload,
                    "dialogue_revision_feedback": {
                        "reason": "invalid_structured_blocks",
                        "detail": str(exc),
                        "instruction": (
                            "Regenerate the complete response as valid structured blocks."
                        ),
                    },
                }
                continue
            missing_fragments = _missing_dialogue_fragments(payload, blocks)
            if not missing_fragments or quality_attempt >= maximum_attempts:
                break
            if remaining_calls <= 0:
                break
            payload = {
                **payload,
                "dialogue_revision_feedback": {
                    "reason": "dialogue_contract_not_met",
                    "missing_required_fragments": missing_fragments,
                    "instruction": (
                        "Regenerate all blocks. Incorporate the missing ideas naturally "
                        "in character; do not quote this feedback or any metadata."
                    ),
                },
            }
        metadata = dict(raw.get("metadata") or {})
        metadata.update(
            {
                "dialogue_quality_attempts": quality_attempt,
                "dialogue_missing_fragments": missing_fragments,
                "provider_calls_remaining": remaining_calls,
            }
        )
        return WriterResult(
            blocks=blocks,
            source="structured_provider",
            provider=self.generator.config.provider,
            model=self.generator.config.model,
            latency_ms=round((perf_counter() - started) * 1000.0, 3),
            attempt_count=max(1, total_attempts),
            raw_metadata=metadata,
        )


def _missing_dialogue_fragments(
    payload: Mapping[str, Any],
    blocks: Sequence[Any],
) -> list[str]:
    contract = payload.get("dialogue_contract")
    if not isinstance(contract, Mapping):
        return []
    required = [
        str(value).strip()
        for value in contract.get("required_fragments") or ()
        if str(value).strip()
    ]
    if not required:
        return []
    combined = re.sub(
        r"[^a-z0-9]+",
        " ",
        " ".join(str(getattr(block, "text", "")) for block in blocks).casefold(),
    ).strip()
    return [
        fragment
        for fragment in required
        if re.sub(r"[^a-z0-9]+", " ", fragment.casefold()).strip()
        not in combined
    ]


class UnavailableNarrativeWriter:
    """Raise inside validation orchestration so canonical fallback is recorded."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def write(self, request, plan, evidence) -> WriterResult:
        raise RuntimeError(self.reason)


def build_production_narrative_writer(
    config: NarrativeProviderConfig | None = None,
    *,
    provider_factory: Callable[..., BaseProvider | None] = get_provider,
) -> NarrativeWriter:
    """Create the configured live typed writer or an explicit safe fallback."""

    resolved = config or NarrativeProviderConfig.from_environment()
    if config is None and not resolved.provider and resolved.mode not in _OFFLINE_MODES:
        # No override in the environment: the writer uses RPG's settings route,
        # the provider and model the rest of the turn uses. It used to fall back
        # to deterministic prose, which turns that need model-written dialogue
        # refuse to publish.
        from app.apps.rpg.foundation.provider_access import routed_provider, rpg_llm_route

        route = rpg_llm_route()
        provider = routed_provider() if route is not None else None
        if route is not None and provider is not None:
            resolved = replace(resolved, provider=route[0], model=route[1])
            return ProductionStructuredNarrativeWriter(ProviderNarrativeGenerator(provider, resolved))
    if not resolved.live_enabled:
        return DeterministicNarrativeWriter()
    try:
        provider = provider_factory(
            resolved.provider,
            {
                "api_key": resolved.api_key,
                "base_url": resolved.base_url,
                "model": resolved.model or None,
                "timeout": resolved.timeout_seconds,
                "max_retries": resolved.max_retries,
            },
        )
    except Exception as exc:
        return UnavailableNarrativeWriter(
            f"configured RPG narrative provider could not initialize: {exc}"
        )
    if provider is None:
        return UnavailableNarrativeWriter(
            f"configured RPG narrative provider is unavailable: {resolved.provider}"
        )
    return ProductionStructuredNarrativeWriter(
        ProviderNarrativeGenerator(provider, resolved)
    )
