"""Unified provider/model facade over existing Omnix registries."""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from app.runtime.ports import Port, implementations


class ProviderCatalog(Protocol):
    """A feature's provider list for the facade (ADR-0016)."""

    family: Literal["image", "rpg_visual"]

    def list_providers(self) -> list[dict[str, Any]]: ...


PROVIDER_CATALOGS: Port[ProviderCatalog] = Port("providers.catalogs", ProviderCatalog, "many")


def _catalog_providers(family: str) -> list[dict[str, Any]]:
    return [info for catalog in implementations(PROVIDER_CATALOGS) if catalog.family == family
            for info in catalog.list_providers()]


ProviderFamily = Literal["llm", "tts", "stt", "image", "rpg_visual"]
ProviderStatus = Literal["available", "configured", "unknown", "degraded"]
ModelLocation = Literal["local", "remote", "unknown"]


class ProviderCapability(str, Enum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    TTS = "tts"
    STT = "stt"
    IMAGE = "image"
    VOICE_CLONING = "voice_cloning"
    DIAGNOSTICS = "diagnostics"
    MODEL_DISCOVERY = "model_discovery"


class ProviderSummary(BaseModel):
    id: str
    label: str
    family: ProviderFamily
    capabilities: list[ProviderCapability]
    status: ProviderStatus = "unknown"
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelSummary(BaseModel):
    id: str
    label: str
    provider_id: str
    capabilities: list[ProviderCapability]
    location: ModelLocation = "unknown"
    vram_hint_mb: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderFacadePayload(BaseModel):
    providers: list[ProviderSummary]
    models: list[ModelSummary]


ProviderLister = Callable[[], list[dict[str, Any]]]
SettingsLoader = Callable[[], dict[str, Any]]


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _dedupe_capabilities(values: list[ProviderCapability]) -> list[ProviderCapability]:
    seen: set[ProviderCapability] = set()
    result: list[ProviderCapability] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _map_llm_capabilities(values: list[Any]) -> list[ProviderCapability]:
    mapped = [ProviderCapability.CHAT, ProviderCapability.COMPLETION, ProviderCapability.DIAGNOSTICS]
    for value in values:
        normalized = _safe_str(value).strip().lower()
        if normalized in {"embeddings", "embedding"}:
            mapped.append(ProviderCapability.EMBEDDING)
        if normalized in {"models", "model_discovery"}:
            mapped.append(ProviderCapability.MODEL_DISCOVERY)
        if normalized in {"image_generation", "image"}:
            mapped.append(ProviderCapability.IMAGE)
    return _dedupe_capabilities(mapped)


def _map_audio_capabilities(kind: Literal["tts", "stt"], values: list[Any]) -> list[ProviderCapability]:
    mapped = [ProviderCapability.TTS if kind == "tts" else ProviderCapability.STT, ProviderCapability.DIAGNOSTICS]
    for value in values:
        normalized = _safe_str(value).strip().lower()
        if normalized == "voice_cloning":
            mapped.append(ProviderCapability.VOICE_CLONING)
    return _dedupe_capabilities(mapped)


def _provider_from_info(
    info: dict[str, Any],
    *,
    family: ProviderFamily,
    source: str,
    capabilities: list[ProviderCapability],
) -> ProviderSummary:
    provider_id = _safe_str(info.get("name") or info.get("key")).strip()
    label = _safe_str(info.get("display_name") or info.get("label") or provider_id).strip()
    status = _safe_str(info.get("status")).strip().lower() or "available"
    if status not in {"available", "configured", "unknown", "degraded"}:
        status = "unknown"
    return ProviderSummary(
        id=f"{family}:{provider_id}",
        label=label or provider_id,
        family=family,
        capabilities=capabilities,
        status=status,  # type: ignore[arg-type]
        source=source,
        metadata={key: value for key, value in info.items() if key not in {"name", "key", "display_name", "label"}},
    )


def _chatgpt_codex_model(settings: dict[str, Any]) -> str:
    profile = settings.get("settings_control_center")
    if not isinstance(profile, dict):
        return "gpt-5.6-sol"
    configs = profile.get("providerConfigs")
    if not isinstance(configs, dict):
        return "gpt-5.6-sol"
    codex = configs.get("chatgptCodex")
    if not isinstance(codex, dict):
        return "gpt-5.6-sol"
    return _safe_str(codex.get("model")).strip() or "gpt-5.6-sol"


def _claude_cli_model(settings: dict[str, Any]) -> str:
    profile = settings.get("settings_control_center")
    configs = profile.get("providerConfigs") if isinstance(profile, dict) else None
    claude = configs.get("claudeCli") if isinstance(configs, dict) else None
    model = _safe_str(claude.get("model")).strip() if isinstance(claude, dict) else ""
    return model or "sonnet"


class ProviderFacade:
    """Read-only facade that normalizes existing provider registries."""

    def __init__(
        self,
        *,
        llm_lister: ProviderLister | None = None,
        tts_lister: ProviderLister | None = None,
        stt_lister: ProviderLister | None = None,
        image_lister: ProviderLister | None = None,
        visual_lister: ProviderLister | None = None,
        settings_loader: SettingsLoader | None = None,
    ) -> None:
        self._llm_lister = llm_lister
        self._tts_lister = tts_lister
        self._stt_lister = stt_lister
        self._image_lister = image_lister
        self._visual_lister = visual_lister
        self._settings_loader = settings_loader

    def payload(self) -> ProviderFacadePayload:
        providers = self.list_providers()
        models = self.list_configured_models(providers)
        return ProviderFacadePayload(providers=providers, models=models)

    def list_providers(self) -> list[ProviderSummary]:
        providers: list[ProviderSummary] = []

        for info in self._list_llm():
            providers.append(
                _provider_from_info(
                    info,
                    family="llm",
                    source="app.providers.registry",
                    capabilities=_map_llm_capabilities(list(info.get("capabilities") or [])),
                )
            )

        for info in self._list_tts():
            providers.append(
                _provider_from_info(
                    info,
                    family="tts",
                    source="app.providers.audio_registry",
                    capabilities=_map_audio_capabilities("tts", list(info.get("capabilities") or [])),
                )
            )

        for info in self._list_stt():
            providers.append(
                _provider_from_info(
                    info,
                    family="stt",
                    source="app.providers.audio_registry",
                    capabilities=_map_audio_capabilities("stt", list(info.get("capabilities") or [])),
                )
            )

        for info in self._list_image():
            providers.append(
                _provider_from_info(
                    info,
                    family="image",
                    source="app.platform.image.providers.registry",
                    capabilities=[ProviderCapability.IMAGE, ProviderCapability.DIAGNOSTICS],
                )
            )

        for info in self._list_visual():
            providers.append(
                _provider_from_info(
                    info,
                    family="rpg_visual",
                    source="app.apps.rpg.narration.visual.providers.registry",
                    capabilities=[ProviderCapability.IMAGE, ProviderCapability.DIAGNOSTICS],
                )
            )

        return sorted(providers, key=lambda item: (item.family, item.id))

    def list_configured_models(self, providers: list[ProviderSummary] | None = None) -> list[ModelSummary]:
        settings = self._load_settings()
        available_provider_ids = {provider.id for provider in providers or self.list_providers()}
        models: list[ModelSummary] = []
        live_codex_models = (
            self._live_chatgpt_codex_models()
            if "llm:chatgpt_codex" in available_provider_ids
            else []
        )
        configured = {
            "llm:openrouter": ("openrouter", settings.get("openrouter", {}).get("model"), "remote"),
            "llm:cerebras": ("cerebras", settings.get("cerebras", {}).get("model"), "remote"),
            "llm:chatgpt_codex": ("ChatGPT Codex", _chatgpt_codex_model(settings), "remote"),
            "llm:claude_cli": ("Claude CLI", _claude_cli_model(settings), "remote"),
            "llm:llamacpp": ("llamacpp", settings.get("llamacpp", {}).get("model"), "local"),
            "llm:lmstudio": ("lmstudio", settings.get("lmstudio", {}).get("model"), "local"),
            "tts:faster-qwen3-tts": (
                "faster-qwen3-tts",
                settings.get("faster-qwen3-tts", {}).get("model_name"),
                "local",
            ),
        }
        for provider_id, (label_prefix, model_id, location) in configured.items():
            if provider_id == "llm:chatgpt_codex" and live_codex_models:
                continue
            model_id = _safe_str(model_id).strip()
            if not model_id or provider_id not in available_provider_ids:
                continue
            if provider_id == "llm:claude_cli":
                models.extend(self._claude_cli_models(model_id))
                continue
            capabilities = [ProviderCapability.TTS] if provider_id.startswith("tts:") else [ProviderCapability.CHAT]
            models.append(
                ModelSummary(
                    id=f"{provider_id}:{model_id}",
                    label=f"{label_prefix}: {model_id}",
                    provider_id=provider_id,
                    capabilities=capabilities,
                    location=location,  # type: ignore[arg-type]
                    metadata={"source": "settings", "model_id": model_id},
                )
            )
        models.extend(live_codex_models)
        return models

    @staticmethod
    def _claude_cli_models(configured: str) -> list[ModelSummary]:
        """The Claude CLI's model catalog (the configured model included), as the CLI's picker lists it."""
        from app.providers.claude_cli_provider import claude_cli_models

        return [
            ModelSummary(
                id=f"llm:claude_cli:{model.id}",
                label=model.name,
                provider_id="llm:claude_cli",
                capabilities=[ProviderCapability.CHAT],
                location="remote",
                metadata={"source": "catalog", "model_id": model.id, "description": model.description,
                          "configured": model.id == configured},
            )
            for model in claude_cli_models(configured)
        ]

    @staticmethod
    def _live_chatgpt_codex_models() -> list[ModelSummary]:
        """Expose the authenticated Codex catalog, falling back inside the provider."""
        try:
            from app.providers.service import get_provider

            provider = get_provider("chatgpt_codex")
            if provider is None:
                return []
            live_models = provider.get_models()
        except Exception:
            return []

        result: list[ModelSummary] = []
        seen: set[str] = set()
        for model in live_models:
            model_id = _safe_str(getattr(model, "id", "")).strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            metadata = dict(getattr(model, "metadata", {}) or {})
            metadata["model_id"] = model_id
            result.append(
                ModelSummary(
                    id=f"llm:chatgpt_codex:{model_id}",
                    label=_safe_str(getattr(model, "name", "")).strip() or model_id,
                    provider_id="llm:chatgpt_codex",
                    capabilities=[ProviderCapability.CHAT],
                    location="remote",
                    metadata=metadata,
                )
            )
        return result

    def _list_llm(self) -> list[dict[str, Any]]:
        if self._llm_lister:
            return self._llm_lister()
        from app.providers.registry import list_available_providers

        return list_available_providers()

    def _list_tts(self) -> list[dict[str, Any]]:
        if self._tts_lister:
            return self._tts_lister()
        from app.providers.audio_registry import list_available_tts_providers

        return list_available_tts_providers()

    def _list_stt(self) -> list[dict[str, Any]]:
        if self._stt_lister:
            return self._stt_lister()
        from app.providers.audio_registry import list_available_stt_providers

        return list_available_stt_providers()

    def _list_image(self) -> list[dict[str, Any]]:
        if self._image_lister:
            return self._image_lister()
        return _catalog_providers("image")

    def _list_visual(self) -> list[dict[str, Any]]:
        if self._visual_lister:
            return self._visual_lister()
        return _catalog_providers("rpg_visual")

    def _load_settings(self) -> dict[str, Any]:
        if self._settings_loader:
            return self._settings_loader()
        from app.settings.access import load_settings

        return load_settings()


def default_provider_facade() -> ProviderFacade:
    return ProviderFacade()
