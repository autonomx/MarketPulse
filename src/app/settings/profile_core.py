"""Core typed Settings Control Center profile models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SETTINGS_PROFILE_KEY = "settings_control_center"
SETTINGS_SCHEMA_VERSION = 1


class ProviderDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    llm: str = "lmstudio"
    tts: str = "faster-qwen3-tts"
    stt: str = "parakeet"
    image: str = ""
    voice_cloning: str = Field("", alias="voiceCloning")


class ModelDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    chat: str = ""
    fast: str = ""
    quality: str = ""
    background: str = ""
    embedding: str = ""
    image_prompt: str = Field("", alias="imagePrompt")


class RoutingDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    fallback_behavior: str = Field("next-available", alias="fallbackBehavior")
    task_overrides: dict[str, dict[str, str]] = Field(default_factory=dict, alias="taskOverrides")


class LmStudioProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field("http://localhost:1234", alias="baseUrl")
    model: str = ""
    direct: bool = False


class OpenRouterProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str = Field("", alias="apiKey")
    model: str = "openai/gpt-4o-mini"
    context_size: int = Field(128000, alias="contextSize")
    thinking_budget: int = Field(0, alias="thinkingBudget")


class CerebrasProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str = Field("", alias="apiKey")
    model: str = "llama-3.3-70b-versatile"


class ChatGPTCodexProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str = "gpt-5.6-sol"
    reasoning_effort: str = Field("medium", alias="reasoningEffort")
    fast_mode: bool = Field(False, alias="fastMode")
    codex_path: str = Field("codex", alias="codexPath")
    transport: str = "app_server"


class ClaudeCliProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str = "sonnet"
    effort: str = ""
    claude_path: str = Field("claude", alias="claudePath")


class LlamaCppProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field("http://localhost:8180", alias="baseUrl")
    model: str = ""
    download_location: str = Field("server", alias="downloadLocation")
    auto_start: bool = Field(False, alias="autoStart")


class FasterQwen3TtsProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_name: str = Field("Qwen/Qwen3-TTS-12Hz-0.6B-Base", alias="modelName")
    model_dir: str = Field("", alias="modelDir")
    device: str = "cuda"
    dtype: str = "bfloat16"
    chunk_size: int = Field(12, alias="chunkSize")
    non_streaming_mode: bool = Field(True, alias="nonStreamingMode")


class ParakeetProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field("http://127.0.0.1:5201", alias="baseUrl")


class FluxKleinProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    repo_id: str = Field("black-forest-labs/FLUX.2-klein-4B", alias="repoId")
    local_dir: str = Field("", alias="localDir")
    device: str = "cuda"
    torch_dtype: str = Field("bfloat16", alias="torchDtype")
    prefer_local_files: bool = Field(True, alias="preferLocalFiles")
    allow_repo_fallback: bool = Field(False, alias="allowRepoFallback")


class ProviderConfigs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lmstudio: LmStudioProviderConfig = Field(default_factory=LmStudioProviderConfig)
    openrouter: OpenRouterProviderConfig = Field(default_factory=OpenRouterProviderConfig)
    cerebras: CerebrasProviderConfig = Field(default_factory=CerebrasProviderConfig)
    chatgpt_codex: ChatGPTCodexProviderConfig = Field(default_factory=ChatGPTCodexProviderConfig, alias="chatgptCodex")
    claude_cli: ClaudeCliProviderConfig = Field(default_factory=ClaudeCliProviderConfig, alias="claudeCli")
    llamacpp: LlamaCppProviderConfig = Field(default_factory=LlamaCppProviderConfig)
    faster_qwen3_tts: FasterQwen3TtsProviderConfig = Field(default_factory=FasterQwen3TtsProviderConfig, alias="fasterQwen3Tts")
    parakeet: ParakeetProviderConfig = Field(default_factory=ParakeetProviderConfig)
    flux_klein: FluxKleinProviderConfig = Field(default_factory=FluxKleinProviderConfig, alias="fluxKlein")
