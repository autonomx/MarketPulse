"""Resolve the concrete provider route stored with a durable World Forge run."""
from __future__ import annotations

import logging
from app.config.env import environment as _environment

import json
from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib.request import urlopen

from app.providers.registry import get_provider
from app.apps.rpg.genesis.forge.world_forge_default import ReferenceSafeWorldForgeGenerator
from app.apps.rpg.genesis.forge.world_forge_deterministic import DeterministicWorldForgeGenerator
from app.apps.rpg.genesis.forge.world_forge_generation import WorldForgeTopicGenerator
from app.apps.rpg.genesis.worlds.providers.world_forge import (
    UnavailableWorldForgeTopicGenerator,
    WorldForgeProviderConfig,
)

from .generation_recovery_evidence import (
    EvidenceBackedRecoveringWorldForgeTopicGenerator,
)
from .generation_test_mode import deterministic_world_forge_test_mode

logger = logging.getLogger(__name__)

_CONFIGURED_VALUES = {"", "auto", "configured", "settings"}
_DETERMINISTIC_VALUES = {"deterministic", "offline", "reference-safe", "test"}


@dataclass(frozen=True)
class ResolvedWorldForgeRoute:
    provider: str
    model: str
    source: str
    requested_provider: str
    requested_model: str

    @property
    def is_deterministic(self) -> bool:
        return self.provider == "deterministic"


class WorldForgeRouteUnavailableError(ValueError):
    pass


def _provider_key(value: Any) -> str:
    provider = str(value or "").strip()
    if provider.startswith("llm:"):
        provider = provider.split(":", 1)[1]
    return provider.casefold()


def _model_key(value: Any) -> str:
    model = str(value or "").strip()
    parts = model.split(":", 2)
    if len(parts) == 3 and parts[0] == "llm":
        model = parts[2]
    return model


def _settings_route() -> tuple[str, str]:
    try:
        from app.settings.effective_defaults import effective_llm_route, load_effective_profile

        provider_id, model_id = effective_llm_route(
            load_effective_profile(),
            "rpg",
            "rpg.world_forge.generate",
        )
        return _provider_key(provider_id), _model_key(model_id)
    except Exception:
        return "", ""


def _auto_detect_lmstudio_route() -> tuple[str, str]:
    """Return the currently loaded LM Studio LLM, without persisting a setting."""

    try:
        from app.settings.access import load_settings

        settings = load_settings()
        base_url = str(
            dict(settings.get("lmstudio") or {}).get("base_url")
            or "http://localhost:1234"
        ).rstrip("/")
        with urlopen(f"{base_url}/api/v1/models", timeout=2) as response:  # nosec B310
            payload = json.load(response)
        for entry in payload.get("models") or ():
            if not isinstance(entry, Mapping) or str(entry.get("type") or "") != "llm":
                continue
            instances = entry.get("loaded_instances") or ()
            if not isinstance(instances, list) or not instances:
                continue
            instance = instances[0]
            model = str(
                instance.get("id") if isinstance(instance, Mapping) else ""
            ).strip() or str(entry.get("key") or "").strip()
            if model:
                return "lmstudio", model
    except Exception:
        logger.debug("suppressed error in %s", "_auto_detect_lmstudio_route", exc_info=True)
    return "", ""


def resolve_world_forge_route(
    provider_route: str = "configured",
    model: str = "configured",
    *,
    environ: Mapping[str, str] | None = None,
    allow_deterministic: bool = False,
) -> ResolvedWorldForgeRoute:
    """Resolve one concrete provider or fail before a durable run is created."""

    env = environ if environ is not None else _environment()
    test_mode = deterministic_world_forge_test_mode(env)
    requested_provider = _provider_key(provider_route)
    requested_model = _model_key(model)
    explicit_provider = requested_provider not in _CONFIGURED_VALUES
    explicit_model = requested_model.casefold() not in _CONFIGURED_VALUES

    if requested_provider in _DETERMINISTIC_VALUES:
        if not (allow_deterministic or test_mode):
            raise WorldForgeRouteUnavailableError(
                "deterministic_world_forge_route_is_test_only"
            )
        return ResolvedWorldForgeRoute(
            "deterministic",
            "reference-safe",
            "explicit_test",
            requested_provider,
            requested_model,
        )

    env_provider = _provider_key(env.get("OMNIX_RPG_WORLD_FORGE_PROVIDER"))
    env_model = _model_key(env.get("OMNIX_RPG_WORLD_FORGE_MODEL"))
    settings_provider, settings_model = _settings_route()

    if explicit_provider:
        resolved_model = requested_model if explicit_model else ""
        if not resolved_model and env_provider == requested_provider:
            resolved_model = env_model
        if not resolved_model and settings_provider == requested_provider:
            resolved_model = settings_model
        if not resolved_model:
            raise WorldForgeRouteUnavailableError(
                f"world_forge_model_required:{requested_provider}"
            )
        return ResolvedWorldForgeRoute(
            requested_provider,
            resolved_model,
            "explicit",
            requested_provider,
            requested_model,
        )

    if env_provider and env_model:
        return ResolvedWorldForgeRoute(
            env_provider,
            requested_model if explicit_model else env_model,
            "world_forge_environment",
            requested_provider,
            requested_model,
        )

    if settings_provider and settings_model:
        # A configured LM Studio route is intentionally model-agnostic: the
        # local server's loaded instance is its effective default.  Retain the
        # saved model as a fallback for an unavailable/older LM Studio server,
        # but do not cause it to unload the model the user selected locally.
        if settings_provider == "lmstudio" and not explicit_model:
            detected_provider, detected_model = _auto_detect_lmstudio_route()
            if detected_provider and detected_model:
                return ResolvedWorldForgeRoute(
                    detected_provider,
                    detected_model,
                    "lmstudio_loaded_default",
                    requested_provider,
                    requested_model,
                )
        return ResolvedWorldForgeRoute(
            settings_provider,
            requested_model if explicit_model else settings_model,
            "settings_control_center",
            requested_provider,
            requested_model,
        )

    detected_provider, detected_model = _auto_detect_lmstudio_route()
    if detected_provider and detected_model:
        return ResolvedWorldForgeRoute(
            detected_provider,
            detected_model,
            "lmstudio_auto_detected",
            requested_provider,
            requested_model,
        )

    if test_mode:
        return ResolvedWorldForgeRoute(
            "deterministic",
            "reference-safe",
            "rpg_test_mode",
            requested_provider,
            requested_model,
        )

    raise WorldForgeRouteUnavailableError(
        "world_forge_provider_and_model_required:configure_rpg.world_forge.generate"
    )


def build_world_forge_generator_from_settings(
    settings: Mapping[str, Any],
) -> WorldForgeTopicGenerator:
    """Build the recorded provider with bounded same-model structural recovery."""

    provider_id = _provider_key(settings.get("provider_route"))
    model_id = _model_key(settings.get("model"))
    if provider_id in _CONFIGURED_VALUES or not provider_id or not model_id:
        return UnavailableWorldForgeTopicGenerator(
            "durable World Forge job has no concrete provider and model"
        )
    if provider_id in _DETERMINISTIC_VALUES or provider_id == "deterministic":
        if deterministic_world_forge_test_mode():
            return ReferenceSafeWorldForgeGenerator(DeterministicWorldForgeGenerator())
        return UnavailableWorldForgeTopicGenerator(
            "deterministic World Forge lore is disabled in production"
        )

    config = replace(
        WorldForgeProviderConfig.from_environment(),
        mode="live",
        provider=provider_id,
        model=model_id,
        prompt_version=str(settings.get("prompt_version") or "world-prompt-v1"),
        max_retries=0,
        lmstudio_schema_fallback=False,
    )
    provider = None
    try:
        from app.providers.service import get_provider as get_runtime_provider

        provider = get_runtime_provider(provider_id)
    except Exception:
        provider = None
    if provider is None:
        try:
            provider = get_provider(
                provider_id,
                {
                    "api_key": config.api_key,
                    "base_url": config.base_url,
                    "model": config.model or None,
                    "timeout": config.timeout_seconds,
                    "max_retries": 0,
                },
            )
        except Exception as exc:
            return UnavailableWorldForgeTopicGenerator(
                f"durable World Forge provider {provider_id} could not initialize: {exc}"
            )
    if provider is None:
        return UnavailableWorldForgeTopicGenerator(
            f"durable World Forge provider {provider_id} is unavailable"
        )
    return ReferenceSafeWorldForgeGenerator(
        EvidenceBackedRecoveringWorldForgeTopicGenerator(provider, config)
    )


def build_world_forge_generator_for_run(
    run: Mapping[str, Any] | None,
) -> WorldForgeTopicGenerator:
    settings = dict((run or {}).get("settings") or {})
    provider_id = _provider_key(settings.get("provider_route"))
    if provider_id and provider_id not in _CONFIGURED_VALUES:
        return build_world_forge_generator_from_settings(settings)
    try:
        route = resolve_world_forge_route()
    except WorldForgeRouteUnavailableError as exc:
        return UnavailableWorldForgeTopicGenerator(str(exc))
    return build_world_forge_generator_from_settings(
        {"provider_route": route.provider, "model": route.model}
    )


__all__ = [
    "ResolvedWorldForgeRoute",
    "WorldForgeRouteUnavailableError",
    "build_world_forge_generator_for_run",
    "build_world_forge_generator_from_settings",
    "resolve_world_forge_route",
]
