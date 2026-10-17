"""Deterministic token estimates and bounded prompt-section helpers."""
from __future__ import annotations

from app.config.env import environment

from pydantic import BaseModel, ConfigDict, Field
from app.conversation.contracts import estimate_tokens

from .memory_port import memory_runtime_settings as load_memory_runtime_settings

DEFAULT_INPUT_TOKEN_BUDGET = 65_536
DEFAULT_OUTPUT_TOKEN_RESERVE = 4_096


class PromptBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_input_tokens: int = Field(default=DEFAULT_INPUT_TOKEN_BUDGET, ge=1)
    reserved_output_tokens: int = Field(default=DEFAULT_OUTPUT_TOKEN_RESERVE, ge=0)
    memory_tokens: int = Field(default=4_000, ge=0)
    summary_tokens: int = Field(default=4_000, ge=0)
    history_tokens: int = Field(default=8_000, ge=0)
    external_context_tokens: int = Field(default=12_000, ge=0)

    @property
    def usable_input_tokens(self) -> int:
        return max(1, self.max_input_tokens - self.reserved_output_tokens)


class PromptBudgetDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_tokens: int = Field(default=0, ge=0)
    usable_input_tokens: int = Field(default=0, ge=0)
    truncated_sections: list[str] = Field(default_factory=list)
    section_tokens: dict[str, int] = Field(default_factory=dict)


def trim_to_token_budget(text: str, max_tokens: int, *, marker: str = "\n[truncated]") -> str:
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    byte_budget = max_tokens * 4
    marker_bytes = marker.encode("utf-8")
    available = max(0, byte_budget - len(marker_bytes))
    raw = text.encode("utf-8")[:available]
    while raw:
        try:
            return raw.decode("utf-8").rstrip() + marker
        except UnicodeDecodeError:
            raw = raw[:-1]
    return marker.strip() if estimate_tokens(marker.strip()) <= max_tokens else ""


def prompt_budget_from_env() -> PromptBudget:
    def integer(name: str, fallback: int) -> int:
        value = (environment().get(name) or "").strip()
        if not value:
            return fallback
        try:
            return max(0, int(value))
        except ValueError:
            return fallback

    memory_settings = load_memory_runtime_settings()
    return PromptBudget(
        max_input_tokens=max(1, integer("OMNIX_CHAT_INPUT_TOKEN_BUDGET", DEFAULT_INPUT_TOKEN_BUDGET)),
        reserved_output_tokens=integer("OMNIX_CHAT_OUTPUT_TOKEN_RESERVE", DEFAULT_OUTPUT_TOKEN_RESERVE),
        memory_tokens=memory_settings.memory_token_budget,
        summary_tokens=integer("OMNIX_CHAT_SUMMARY_TOKEN_BUDGET", 4_000),
        history_tokens=memory_settings.history_token_budget,
        external_context_tokens=integer("OMNIX_CHAT_EXTERNAL_CONTEXT_TOKEN_BUDGET", 12_000),
    )


def prompt_budget_for_model(provider_id: str | None, model_id: str | None) -> PromptBudget:
    """The budget for one model: its advertised context window when known (WP-5.7).

    ``OMNIX_CHAT_INPUT_TOKEN_BUDGET``, when set, caps it; without an advertised
    window the configured or default budget applies.
    """
    from app.providers.model_catalog import advertised_context_window

    base = prompt_budget_from_env()
    window = advertised_context_window(provider_id, model_id)
    if window is None:
        return base
    configured = (environment().get("OMNIX_CHAT_INPUT_TOKEN_BUDGET") or "").strip()
    limit = window
    if configured.isdigit() and int(configured) > 0:
        limit = min(window, int(configured))
    # Keep room for the answer even on a small model.
    reserve = min(base.reserved_output_tokens, max(0, limit // 4))
    return base.model_copy(update={"max_input_tokens": max(1, limit), "reserved_output_tokens": reserve})
