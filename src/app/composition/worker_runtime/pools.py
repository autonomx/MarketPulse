"""Validated resource-class pools for standalone durable job workers."""
from __future__ import annotations

from dataclasses import dataclass

from app.jobs.models import ResourceClass


DEFAULT_POOLS = "llm=2,image=1,tts=1,research=2,cpu=4,stt=1"
RESOURCE_POOLS: dict[str, tuple[str, ...]] = {
    "llm": (ResourceClass.GPU_LLM.value,),
    "image": (ResourceClass.GPU_IMAGE.value,),
    "tts": (
        ResourceClass.GPU_TTS.value,
        ResourceClass.GPU_TTS_REALTIME.value,
        ResourceClass.GPU_TTS_PREVIEW.value,
        ResourceClass.GPU_TTS_OFFLINE.value,
    ),
    "stt": (ResourceClass.GPU_STT.value,),
    "research": (ResourceClass.NETWORK.value,),
    "cpu": (
        ResourceClass.CPU.value,
        ResourceClass.RPG_CAMPAIGN_GENESIS.value,
        ResourceClass.RPG_WORLD_GENERATION.value,
        ResourceClass.RPG_MAP_MATERIALIZATION.value,
    ),
}


@dataclass(frozen=True, slots=True)
class WorkerPoolConfig:
    name: str
    concurrency: int
    resource_classes: tuple[str, ...]


def parse_pools(value: str = DEFAULT_POOLS) -> tuple[WorkerPoolConfig, ...]:
    """Parse name=concurrency entries; pool names own fixed resource classes."""
    if not value.strip():
        raise ValueError("at least one job worker pool is required")
    result: list[WorkerPoolConfig] = []
    seen: set[str] = set()
    for raw in value.split(","):
        entry = raw.strip()
        if not entry or entry.count("=") != 1:
            raise ValueError(f"invalid job worker pool entry: {entry!r}")
        name, raw_concurrency = (part.strip() for part in entry.split("=", 1))
        if name not in RESOURCE_POOLS:
            raise ValueError(f"unknown job worker pool: {name}")
        if name in seen:
            raise ValueError(f"duplicate job worker pool: {name}")
        try:
            concurrency = int(raw_concurrency)
        except ValueError as exc:
            raise ValueError(f"pool concurrency must be an integer: {name}") from exc
        if not 1 <= concurrency <= 64:
            raise ValueError(f"pool concurrency must be between 1 and 64: {name}")
        result.append(
            WorkerPoolConfig(name, concurrency, RESOURCE_POOLS[name])
        )
        seen.add(name)
    return tuple(result)


__all__ = ["DEFAULT_POOLS", "RESOURCE_POOLS", "WorkerPoolConfig", "parse_pools"]

