"""Public entry points for the World Forge provider."""

from dataclasses import replace
from threading import Lock
from typing import Any, Callable, Mapping

from app.providers.base import BaseProvider
from app.providers.registry import get_provider
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_default import ReferenceSafeWorldForgeGenerator
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from app.apps.rpg.genesis.worlds.providers.world_forge_foundation import (
    WorldForgeDossier,
    WorldForgeEntityRegistryItem,
    WorldForgeEntityRegistryResponse,
    WorldForgeProviderConfig,
    WorldForgeTopicResponse,
    _LOGGER,
    _entity_registry_contract,
    _entity_registry_payload,
    _entity_registry_system_prompt,
    _model_rows,
    _payload,
    _system_prompt,
    _token_estimate,
    _topic_contract,
)
from app.apps.rpg.genesis.worlds.providers.world_forge_generator import (
    ProviderWorldForgeTopicGenerator,
)


__all__ = [
    "FallbackWorldForgeTopicGenerator",
    "ProviderWorldForgeTopicGenerator",
    "UnavailableWorldForgeTopicGenerator",
    "WorldForgeDossier",
    "WorldForgeEntityRegistryItem",
    "WorldForgeEntityRegistryResponse",
    "WorldForgeProviderConfig",
    "WorldForgeTopicResponse",
    "_entity_registry_contract",
    "_entity_registry_payload",
    "_entity_registry_system_prompt",
    "_model_rows",
    "_payload",
    "_system_prompt",
    "_token_estimate",
    "_topic_contract",
    "attach_world_forge_progress_callback",
    "build_production_world_forge_generator",
]


def attach_world_forge_progress_callback(
    generator: Any,
    callback: Callable[[Mapping[str, Any]], None],
) -> Callable[[], None]:
    """Attach a checkpoint callback through production generator wrappers.

    The production route can wrap a provider generator in validation, reference-safe,
    and fallback adapters.  This keeps batch checkpoints independent of that wiring.
    """

    configured: list[
        tuple[ProviderWorldForgeTopicGenerator, Callable[[Mapping[str, Any]], None] | None]
    ] = []
    visited: set[int] = set()

    def visit(value: Any) -> None:
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)
        if isinstance(value, ProviderWorldForgeTopicGenerator):
            configured.append((value, value.set_progress_callback(callback)))
            return
        nested = getattr(value, "generator", None)
        if nested is not None:
            visit(nested)
        for nested_generator in getattr(value, "generators", ()):
            visit(nested_generator)

    visit(generator)

    def detach() -> None:
        for provider, previous in configured:
            provider.set_progress_callback(previous)

    return detach


class UnavailableWorldForgeTopicGenerator:
    """Fail launch-required generation instead of publishing placeholder canon."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def generate(self, node: CampaignTopicNode, **kwargs: Any) -> GeneratedTopic:
        raise RuntimeError(f"{self.reason}: {node.topic_id}")


class FallbackWorldForgeTopicGenerator:
    """Stick to the first healthy generator after a provider failure."""

    def __init__(self, generators: tuple[WorldForgeTopicGenerator, ...]) -> None:
        if not generators:
            raise ValueError("at least one World Forge generator is required")
        self.generators = generators
        self._active_index = 0
        self._lock = Lock()

    def generate(
        self,
        node: CampaignTopicNode,
        **kwargs: Any,
    ) -> GeneratedTopic:
        with self._lock:
            start_index = self._active_index
        last_error: Exception | None = None
        for index in range(start_index, len(self.generators)):
            try:
                topic = self.generators[index].generate(node, **kwargs)
                with self._lock:
                    self._active_index = max(self._active_index, index)
                return topic
            except Exception as exc:
                last_error = exc
                with self._lock:
                    self._active_index = max(self._active_index, index + 1)
        assert last_error is not None
        raise last_error


def build_production_world_forge_generator(
    config: WorldForgeProviderConfig | None = None,
    *,
    provider_factory: Callable[
        [str, Mapping[str, Any] | None], BaseProvider | None
    ]
    | None = None,
) -> WorldForgeTopicGenerator:
    """Resolve the one production World Forge generator for every topic job."""

    resolved = config or WorldForgeProviderConfig.from_environment()
    settings_routed = config is None or resolved.mode == "auto"
    fallback_behavior = "fail"
    if settings_routed and resolved.mode not in {
        "offline",
        "deterministic",
        "test",
        "disabled",
    }:
        try:
            from app.settings.effective_defaults import (
                effective_llm_route,
                load_effective_profile,
            )

            profile = load_effective_profile()
            provider_id, model_id = effective_llm_route(
                profile,
                "rpg",
                "rpg.world_forge.generate",
            )
            global_settings = getattr(profile, "global_settings", None)
            routing = getattr(global_settings, "routing", None)
            fallback_behavior = str(
                getattr(routing, "fallback_behavior", "fail") or "fail"
            ).strip().casefold()
            provider_key = str(provider_id or "").strip()
            if provider_key.startswith("llm:"):
                provider_key = provider_key.split(":", 1)[1]
            model_key = str(model_id or "").strip()
            model_parts = model_key.split(":", 2)
            if len(model_parts) == 3 and model_parts[0] == "llm":
                model_key = model_parts[2]
            resolved = replace(
                resolved,
                provider=provider_key,
                model=model_key,
            )
        except Exception:
            _LOGGER.warning(
                "world_forge_provider_settings_resolution_failed",
                exc_info=True,
            )
    if not resolved.live_enabled:
        return UnavailableWorldForgeTopicGenerator(
            "World Forge requires a configured provider; deterministic lore is disabled"
        )
    provider_ids = [resolved.provider]
    if (
        settings_routed
        and fallback_behavior == "next-available"
        and resolved.provider != "lmstudio"
    ):
        provider_ids.append("lmstudio")

    generators: list[WorldForgeTopicGenerator] = []
    initialization_errors: list[str] = []
    for provider_id in provider_ids:
        candidate = replace(
            resolved,
            provider=provider_id,
            model=resolved.model if provider_id == resolved.provider else "",
        )
        try:
            if provider_factory is not None:
                provider = provider_factory(
                    provider_id,
                    {
                        "api_key": candidate.api_key,
                        "base_url": candidate.base_url,
                        "model": candidate.model or None,
                        "timeout": candidate.timeout_seconds,
                        "max_retries": candidate.max_retries,
                    },
                )
            elif settings_routed:
                from app.providers.service import get_provider as get_runtime_provider

                provider = get_runtime_provider(provider_id)
            else:
                provider = get_provider(
                    provider_id,
                    {
                        "api_key": candidate.api_key,
                        "base_url": candidate.base_url,
                        "model": candidate.model or None,
                        "timeout": candidate.timeout_seconds,
                        "max_retries": candidate.max_retries,
                    },
                )
        except Exception as exc:
            initialization_errors.append(f"{provider_id}: {exc}")
            continue
        if provider is None:
            initialization_errors.append(f"{provider_id}: unavailable")
            continue
        generators.append(ProviderWorldForgeTopicGenerator(provider, candidate))

    if not generators:
        return UnavailableWorldForgeTopicGenerator(
            "configured World Forge providers could not initialize: "
            + "; ".join(initialization_errors)
        )
    generator: WorldForgeTopicGenerator = generators[0]
    if len(generators) > 1:
        generator = FallbackWorldForgeTopicGenerator(tuple(generators))
    return ReferenceSafeWorldForgeGenerator(generator)
