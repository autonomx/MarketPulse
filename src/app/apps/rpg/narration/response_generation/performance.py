from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from .contracts import ResponseMode
from .profiles import ResponseGenerationProfile


T = TypeVar("T")


@dataclass
class VersionedResponseCache(Generic[T]):
    values: dict[str, T] = field(default_factory=dict)

    def key(
        self,
        namespace: str,
        identity: str,
        *versions: str,
    ) -> str:
        payload = json.dumps(
            [namespace, identity, *versions],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_or_create(
        self,
        namespace: str,
        identity: str,
        versions: tuple[str, ...],
        factory: Callable[[], T],
    ) -> tuple[T, bool]:
        key = self.key(namespace, identity, *versions)
        if key in self.values:
            return self.values[key], True
        value = factory()
        self.values[key] = value
        return value, False

@dataclass(frozen=True)
class BlockingPathDecision:
    action: str
    reason: str
    use_provider: bool
    use_hermes: bool
    cacheable: bool
    blocking_budget_ms: int


def blocking_path_decision(
    mode: ResponseMode,
    profile: ResponseGenerationProfile,
    *,
    supported_mechanic: bool,
    recovery_needed: bool,
    cache_hit: bool = False,
) -> BlockingPathDecision:
    if cache_hit:
        return BlockingPathDecision(
            "cache",
            "versioned response context cache hit",
            False,
            False,
            True,
            50,
        )
    if mode is ResponseMode.UTILITY or supported_mechanic and not profile.use_provider:
        return BlockingPathDecision(
            "deterministic",
            "known utility or mechanic response avoids heavy generation",
            False,
            False,
            True,
            profile.blocking_budget_ms,
        )
    if recovery_needed:
        return BlockingPathDecision(
            "recover",
            "local recovery first with bounded optional Hermes research",
            profile.use_provider,
            profile.allow_hermes,
            False,
            profile.blocking_budget_ms,
        )
    return BlockingPathDecision(
        "generate",
        "bounded provider generation for presentation",
        profile.use_provider,
        False,
        mode not in {ResponseMode.COMBAT, ResponseMode.MAJOR_BEAT},
        profile.blocking_budget_ms,
    )


@dataclass
class LatencyTrace:
    stages_ms: dict[str, float] = field(default_factory=dict)
    first_approved_delivery_ms: float | None = None

    def record(self, stage: str, duration_ms: float) -> None:
        self.stages_ms[stage] = round(max(0.0, float(duration_ms)), 3)

    @property
    def total_ms(self) -> float:
        return round(sum(self.stages_ms.values()), 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stages_ms": dict(self.stages_ms),
            "total_ms": self.total_ms,
            "first_approved_delivery_ms": self.first_approved_delivery_ms,
        }


@dataclass(frozen=True)
class LatencyBenchmark:
    sample_count: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    budget_ms: float
    passed: bool


def evaluate_latency_benchmark(
    samples_ms: tuple[float, ...],
    *,
    p95_budget_ms: float,
) -> LatencyBenchmark:
    values = tuple(sorted(max(0.0, float(value)) for value in samples_ms))
    if not values:
        return LatencyBenchmark(0, 0.0, 0.0, 0.0, p95_budget_ms, False)
    p50 = _percentile(values, 50)
    p95 = _percentile(values, 95)
    return LatencyBenchmark(
        sample_count=len(values),
        p50_ms=p50,
        p95_ms=p95,
        max_ms=round(values[-1], 3),
        budget_ms=float(p95_budget_ms),
        passed=p95 <= p95_budget_ms,
    )


def _percentile(values: tuple[float, ...], percentile: int) -> float:
    if len(values) == 1:
        return round(values[0], 3)
    rank = (percentile / 100.0) * (len(values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(values[lower], 3)
    weight = rank - lower
    return round(
        values[lower] * (1.0 - weight) + values[upper] * weight,
        3,
    )
