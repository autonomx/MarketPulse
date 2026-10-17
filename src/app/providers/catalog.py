"""The explicit list of provider implementations (WP-7.2 Part A).

Registries load providers from this catalog instead of importing every module
in the package and registering whatever subclasses they find. Adding a
provider means adding its spec here; an id may appear once per kind.

Callers that need a provider behaviour ask for a capability
(``provider_supports(provider_id, CONVERSATION_SESSIONS)``) instead of comparing
provider names, so a new provider gains the behaviour by declaring it here.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Literal

ProviderKind = Literal["llm", "tts", "stt"]

# LLM capabilities.
CHAT = "chat"
STREAMING = "streaming"
MODELS = "models"  # lists models with their context windows
TOOLS = "tools"
CANCELLATION = "cancellation"  # chat_completion(cancel=...) ends the stream
# The provider keeps a server-side conversation per ``conversation_id`` call
# argument, so callers pass a stable id for a session or run.
CONVERSATION_SESSIONS = "conversation_sessions"
# Inference runs on this machine's GPU: calls take a ``llm-local`` device permit.
LOCAL_DEVICE = "local_device"
# Responses carry the server's own timing stats (tokens per second, TTFT).
RUNTIME_STATS = "runtime_stats"
# Accepts ``chat_template_kwargs={"enable_thinking": False}``.
THINKING_TOGGLE = "thinking_toggle"
# Answers with the model the local server has loaded; a route need not name one.
LOADED_MODEL = "loaded_model"
# Enforces a JSON Schema ``response_format`` natively (strict, inline refs).
NATIVE_JSON_SCHEMA = "native_json_schema"
# Validates schemas in OpenAI strict mode: every object closed, every property
# required, no regex lookarounds.
CLOSED_OBJECT_SCHEMA = "closed_object_schema"
API_KEY = "api_key"  # needs an API key from the secret store
DESKTOP_VISION = "desktop_vision"  # has its own desktop-observation vision client
# Audio capabilities.
SYNTHESIS = "synthesis"
VOICE_CLONE = "voice_clone"
LOCAL_ARTIFACTS = "local_artifacts"
TRANSCRIPTION = "transcription"

CAPABILITIES = frozenset({
    CHAT, STREAMING, MODELS, TOOLS, CANCELLATION, CONVERSATION_SESSIONS, LOCAL_DEVICE,
    RUNTIME_STATS, THINKING_TOGGLE, LOADED_MODEL, NATIVE_JSON_SCHEMA, CLOSED_OBJECT_SCHEMA,
    API_KEY, DESKTOP_VISION, SYNTHESIS, VOICE_CLONE, LOCAL_ARTIFACTS, TRANSCRIPTION,
})


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    kind: ProviderKind
    module: str
    attribute: str
    capabilities: frozenset[str]

    def load(self) -> type:
        """Import the implementation; its declared id must match the spec."""
        provider_class = getattr(importlib.import_module(self.module), self.attribute)
        declared = getattr(provider_class, "provider_name", None)
        if isinstance(declared, property):
            declared = declared.fget(None)
        if declared != self.id:
            raise ValueError(f"{self.module}.{self.attribute} declares provider id {declared!r}, catalog says {self.id!r}")
        return provider_class


def _llm(id: str, module: str, attribute: str, *capabilities: str) -> ProviderSpec:
    return ProviderSpec(id, "llm", f"app.providers.{module}", attribute, frozenset((CHAT, *capabilities)))


CATALOG: tuple[ProviderSpec, ...] = (
    _llm("cerebras", "cerebras_provider", "CerebrasProvider", STREAMING, MODELS, API_KEY),
    _llm(
        "chatgpt_codex", "chatgpt_codex_provider", "ChatGPTCodexProvider",
        STREAMING, TOOLS, CONVERSATION_SESSIONS, CLOSED_OBJECT_SCHEMA, DESKTOP_VISION,
    ),
    _llm("claude_cli", "claude_cli_provider", "ClaudeCliProvider", STREAMING, MODELS, NATIVE_JSON_SCHEMA),
    _llm("llamacpp", "llamacpp_provider", "LlamaCppProvider", STREAMING, MODELS, LOCAL_DEVICE),
    _llm(
        "lmstudio", "lmstudio_provider", "LMStudioProvider",
        STREAMING, MODELS, CANCELLATION, LOCAL_DEVICE, RUNTIME_STATS, THINKING_TOGGLE, LOADED_MODEL,
        NATIVE_JSON_SCHEMA,
    ),
    _llm("openai_compatible", "openai_compatible_provider", "OpenAICompatibleProvider", STREAMING, MODELS),
    _llm("openrouter", "openrouter_provider", "OpenRouterProvider", STREAMING, MODELS, API_KEY),
    ProviderSpec(
        "faster-qwen3-tts", "tts", "app.providers.faster_qwen3_tts_provider", "FasterQwen3TTSProvider",
        frozenset({SYNTHESIS, STREAMING, VOICE_CLONE, LOCAL_ARTIFACTS, LOCAL_DEVICE}),
    ),
    ProviderSpec(
        "fish-s2-pro", "tts", "app.providers.fish_s2_pro_provider", "FishS2ProTTSProvider",
        frozenset({SYNTHESIS, STREAMING, VOICE_CLONE, LOCAL_ARTIFACTS, LOCAL_DEVICE}),
    ),
    ProviderSpec("parakeet", "stt", "app.providers.audio_plugins", "ParakeetSTT", frozenset({TRANSCRIPTION})),
)


def specs(kind: ProviderKind) -> tuple[ProviderSpec, ...]:
    return tuple(spec for spec in CATALOG if spec.kind == kind)


def provider_id_of(provider: Any) -> str:
    """A provider id from an id string (``llm:`` prefix allowed) or an instance."""
    if provider is None:
        return ""
    if isinstance(provider, str):
        raw = provider
    else:
        raw = getattr(provider, "provider_name", None) or getattr(
            getattr(provider, "config", None), "provider_type", None
        )
    return str(raw or "").strip().casefold().removeprefix("llm:")


def provider_capabilities(provider: Any, kind: ProviderKind = "llm") -> frozenset[str]:
    """The declared capabilities of a provider id or instance; empty when unknown."""
    provider_id = provider_id_of(provider)
    spec = next((spec for spec in CATALOG if spec.kind == kind and spec.id == provider_id), None)
    return spec.capabilities if spec is not None else frozenset()


def provider_supports(provider: Any, capability: str, kind: ProviderKind = "llm") -> bool:
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown provider capability {capability!r}")
    return capability in provider_capabilities(provider, kind)


def providers_with(capability: str, kind: ProviderKind = "llm") -> tuple[str, ...]:
    """Ids of the catalogued providers that declare ``capability``."""
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown provider capability {capability!r}")
    return tuple(spec.id for spec in CATALOG if spec.kind == kind and capability in spec.capabilities)


def _check_unique(catalog: tuple[ProviderSpec, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    for spec in catalog:
        unknown = spec.capabilities - CAPABILITIES
        if unknown:
            raise ValueError(f"{spec.id} declares unknown capabilities {sorted(unknown)}")
        key = (spec.kind, spec.id)
        if key in seen:
            raise ValueError(f"duplicate {spec.kind} provider id {spec.id!r} in the catalog")
        seen.add(key)


_check_unique(CATALOG)

__all__ = [
    "API_KEY",
    "CANCELLATION",
    "CAPABILITIES",
    "CATALOG",
    "CHAT",
    "CLOSED_OBJECT_SCHEMA",
    "CONVERSATION_SESSIONS",
    "DESKTOP_VISION",
    "LOADED_MODEL",
    "LOCAL_ARTIFACTS",
    "LOCAL_DEVICE",
    "MODELS",
    "NATIVE_JSON_SCHEMA",
    "ProviderKind",
    "ProviderSpec",
    "RUNTIME_STATS",
    "STREAMING",
    "SYNTHESIS",
    "THINKING_TOGGLE",
    "TOOLS",
    "TRANSCRIPTION",
    "VOICE_CLONE",
    "provider_capabilities",
    "provider_id_of",
    "provider_supports",
    "providers_with",
    "specs",
]
