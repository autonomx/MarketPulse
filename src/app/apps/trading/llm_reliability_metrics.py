from __future__ import annotations

"""Provider/model-specific reliability accounting for trading research LLM arms."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field


class LLMIdentityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str | None = None
    scheduled_opportunities: int = Field(default=0, ge=0)
    provider_attempts: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    timeouts: int = Field(default=0, ge=0)
    malformed_responses: int = Field(default=0, ge=0)
    transport_failures: int = Field(default=0, ge=0)
    circuit_open_suppressions: int = Field(default=0, ge=0)
    decisions_emitted: int = Field(default=0, ge=0)
    missed_evaluations: int = Field(default=0, ge=0)
    total_provider_latency_ms: Decimal = Decimal("0")
    total_end_to_end_latency_ms: Decimal = Decimal("0")

    @property
    def mean_provider_latency_ms(self) -> Decimal | None:
        if not self.successes:
            return None
        return self.total_provider_latency_ms / Decimal(self.successes)


class LLMReliabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = "trading-llm-reliability-v1"
    identities: tuple[LLMIdentityMetrics, ...]


@dataclass
class _MutableMetrics:
    scheduled_opportunities: int = 0
    provider_attempts: int = 0
    successes: int = 0
    timeouts: int = 0
    malformed_responses: int = 0
    transport_failures: int = 0
    circuit_open_suppressions: int = 0
    decisions_emitted: int = 0
    missed_evaluations: int = 0
    total_provider_latency_ms: Decimal = Decimal("0")
    total_end_to_end_latency_ms: Decimal = Decimal("0")


class LLMReliabilityLedger:
    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: dict[tuple[str, str | None], _MutableMetrics] = defaultdict(
            _MutableMetrics
        )

    def _row(self, provider: str, model: str | None) -> _MutableMetrics:
        return self._rows[(provider or "unknown", model)]

    def scheduled(self, provider: str, model: str | None, count: int = 1) -> None:
        with self._lock:
            self._row(provider, model).scheduled_opportunities += max(0, count)

    def attempt(self, provider: str, model: str | None) -> None:
        with self._lock:
            self._row(provider, model).provider_attempts += 1

    def success(
        self,
        provider: str,
        model: str | None,
        *,
        decisions: int,
        provider_latency_ms: Decimal,
        end_to_end_latency_ms: Decimal | None = None,
    ) -> None:
        with self._lock:
            row = self._row(provider, model)
            row.successes += 1
            row.decisions_emitted += max(0, decisions)
            row.total_provider_latency_ms += max(Decimal("0"), provider_latency_ms)
            if end_to_end_latency_ms is not None:
                row.total_end_to_end_latency_ms += max(
                    Decimal("0"), end_to_end_latency_ms
                )

    def failure(
        self,
        provider: str,
        model: str | None,
        *,
        kind: str,
        missed: int = 1,
    ) -> None:
        with self._lock:
            row = self._row(provider, model)
            if kind == "timeout":
                row.timeouts += 1
            elif kind == "malformed":
                row.malformed_responses += 1
            elif kind == "circuit_open":
                row.circuit_open_suppressions += 1
            else:
                row.transport_failures += 1
            row.missed_evaluations += max(0, missed)

    def snapshot(self) -> LLMReliabilitySnapshot:
        with self._lock:
            identities = tuple(
                LLMIdentityMetrics(
                    provider=provider,
                    model=model,
                    **vars(row),
                )
                for (provider, model), row in sorted(
                    self._rows.items(),
                    key=lambda item: (item[0][0], item[0][1] or ""),
                )
            )
        return LLMReliabilitySnapshot(identities=identities)


def classify_llm_failure(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".casefold()
    if "circuit" in text and "open" in text:
        return "circuit_open"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    if (
        "invalid_json" in text
        or "validationerror" in text
        or "malformed" in text
        or "missing_decisions" in text
    ):
        return "malformed"
    return "transport"


__all__ = [
    "LLMIdentityMetrics",
    "LLMReliabilityLedger",
    "LLMReliabilitySnapshot",
    "classify_llm_failure",
]
