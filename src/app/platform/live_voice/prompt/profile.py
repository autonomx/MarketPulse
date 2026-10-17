"""Live voice prompt budgets and history profile."""
from __future__ import annotations

from app.config.env import env_str as _env_str
from app.platform.chat.contracts import PromptBudget, prompt_budget_from_env


_DEFAULT_RECENT_MESSAGE_LIMIT = 12
_DEFAULT_INPUT_TOKEN_BUDGET = 12_288
_DEFAULT_OUTPUT_TOKEN_RESERVE = 1_024
_DEFAULT_MEMORY_TOKEN_BUDGET = 1_000
_DEFAULT_SUMMARY_TOKEN_BUDGET = 2_000
_DEFAULT_EXTERNAL_CONTEXT_TOKEN_BUDGET = 2_048


def _integer_setting(name: str, fallback: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int((_env_str(name) or fallback)))
    except (TypeError, ValueError):
        return max(minimum, fallback)


def _live_voice_recent_message_limit() -> int:
    return _integer_setting(
        "OMNIX_LIVE_VOICE_RECENT_MESSAGE_LIMIT",
        _DEFAULT_RECENT_MESSAGE_LIMIT,
        minimum=2,
    )


def _live_voice_prompt_budget() -> PromptBudget:
    base = prompt_budget_from_env()
    input_budget = min(
        base.max_input_tokens,
        _integer_setting(
            "OMNIX_LIVE_VOICE_INPUT_TOKEN_BUDGET",
            _DEFAULT_INPUT_TOKEN_BUDGET,
            minimum=1,
        ),
    )
    output_reserve = min(
        max(0, input_budget - 1),
        base.reserved_output_tokens,
        _integer_setting(
            "OMNIX_LIVE_VOICE_OUTPUT_TOKEN_RESERVE",
            _DEFAULT_OUTPUT_TOKEN_RESERVE,
        ),
    )
    return base.model_copy(
        update={
            "max_input_tokens": input_budget,
            "reserved_output_tokens": output_reserve,
            "memory_tokens": min(
                base.memory_tokens,
                _integer_setting(
                    "OMNIX_LIVE_VOICE_MEMORY_TOKEN_BUDGET",
                    _DEFAULT_MEMORY_TOKEN_BUDGET,
                ),
            ),
            "summary_tokens": min(
                base.summary_tokens,
                _integer_setting(
                    "OMNIX_LIVE_VOICE_SUMMARY_TOKEN_BUDGET",
                    _DEFAULT_SUMMARY_TOKEN_BUDGET,
                ),
            ),
            "history_tokens": 0,
            "external_context_tokens": min(
                base.external_context_tokens,
                _integer_setting(
                    "OMNIX_LIVE_VOICE_EXTERNAL_CONTEXT_TOKEN_BUDGET",
                    _DEFAULT_EXTERNAL_CONTEXT_TOKEN_BUDGET,
                ),
            ),
        }
    )


__all__ = ["_live_voice_prompt_budget", "_live_voice_recent_message_limit"]
