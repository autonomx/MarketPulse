"""Trading research model calls: a daily budget with priority tiers, and schema-bound replies."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from app.apps.trading.evidence_storage import MemoryEvidenceBackend
from app.apps.trading.structured_llm import sends_json_schema, trading_model_call, trading_response_format
from app.apps.trading.trading_ai_budget import (
    AI_BUDGET_COUNTER_PROVIDER,
    TradingAIBudget,
    TradingAIBudgetExhausted,
    contract_tier,
)
from app.providers import ChatMessage
from app.providers.base import ChatResponse

INTRADAY = "trading.intraday_llm.batch"
SHADOW = "trading.ai_shadow_v3.batch"


def _budget(daily: int, clock=None) -> TradingAIBudget:
    now = clock or (lambda: datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc))
    return TradingAIBudget(backend=MemoryEvidenceBackend(), daily_calls=daily, clock=now)


def test_tiers_spend_only_their_share_of_the_day() -> None:
    assert (contract_tier(INTRADAY), contract_tier(SHADOW)) == ("high", "normal")
    assert contract_tier("trading.unlisted.batch") == "normal"
    budget = _budget(10)
    for _ in range(6):
        budget.acquire(SHADOW)  # normal: up to 60% of the day's calls
    with pytest.raises(TradingAIBudgetExhausted) as normal:
        budget.acquire(SHADOW)
    assert (normal.value.tier, normal.value.used, normal.value.limit) == ("normal", 6, 6)
    for _ in range(4):
        budget.acquire(INTRADAY)  # high: the rest of the day
    with pytest.raises(TradingAIBudgetExhausted):
        budget.acquire(INTRADAY)
    counters = budget.status()["counters"]
    assert counters["calls"] == 10
    assert counters["calls:high"] == 4 and counters["denied:normal"] == 1 and counters["denied:high"] == 1


def test_the_count_is_per_eastern_day_and_zero_turns_the_budget_off() -> None:
    now = [datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)]
    budget = _budget(2, clock=lambda: now[0])
    budget.acquire(INTRADAY)
    budget.acquire(INTRADAY)
    with pytest.raises(TradingAIBudgetExhausted):
        budget.acquire(INTRADAY)
    now[0] = datetime(2026, 10, 16, 4, 1, tzinfo=timezone.utc)  # 00:01 ET: a new day
    budget.acquire(INTRADAY)
    assert budget.backend.session_counters(AI_BUDGET_COUNTER_PROVIDER, now[0].date())[0]["calls"] == 1

    unlimited = _budget(0)
    for _ in range(50):
        unlimited.acquire(SHADOW)


class _Answer(BaseModel):
    ok: bool


class _Provider:
    provider_name = "claude_cli"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat_completion(self, messages, **request):
        self.calls.append(request)
        return ChatResponse(content='{"ok": true}', model="m")


def test_an_exhausted_budget_stops_the_call_before_the_provider() -> None:
    provider = _Provider()
    budget = _budget(1)
    messages = [ChatMessage(role="user", content="?")]
    call = dict(output_model=_Answer, contract_id=INTRADAY, schema_name="answer", model="m", max_tokens=50, budget=budget)

    assert trading_model_call(provider, messages, **call).value.ok is True
    with pytest.raises(TradingAIBudgetExhausted):
        trading_model_call(provider, messages, **call)
    assert len(provider.calls) == 1


def test_native_schema_providers_receive_the_output_schema() -> None:
    # Claude CLI enforces --json-schema; without it the model guessed the shape.
    assert sends_json_schema("claude_cli") and sends_json_schema("chatgpt_codex")
    assert not sends_json_schema("cerebras")
    assert not sends_json_schema("lmstudio")  # unchanged: a local model's schema support varies
    claude = trading_response_format(_Provider(), _Answer, "answer")
    assert claude["type"] == "json_schema" and claude["json_schema"]["schema"]["properties"]["ok"]
