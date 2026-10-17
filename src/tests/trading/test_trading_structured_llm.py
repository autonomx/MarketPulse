"""Trading research model calls go through the structured-output gateway (WP-8.4)."""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from app.providers import ChatMessage
from app.providers.base import ChatResponse
from app.apps.trading.structured_llm import TradingModelOutputError, trading_model_call


class Decision(BaseModel):
    instrument_id: str
    execution_authority: Literal[False] = False


class FakeProvider:
    def __init__(self, provider_name: str, content: str = "", *, finish_reason: str = "stop", error: Exception | None = None) -> None:
        self.provider_name = provider_name
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.calls: list[dict] = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return ChatResponse(content=self.content, model="served-model", finish_reason=self.finish_reason)


MESSAGES = [ChatMessage(role="user", content="assess")]


def _call(provider, **kwargs):
    return trading_model_call(
        provider,
        MESSAGES,
        output_model=Decision,
        contract_id="trading.test",
        schema_name="test_decision",
        model="m",
        max_tokens=500,
        **kwargs,
    )


def test_the_analyzer_request_reaches_the_provider_unchanged() -> None:
    local = FakeProvider("lmstudio", '{"instrument_id": "AAPL"}')
    codex = FakeProvider("chatgpt_codex", '{"instrument_id": "AAPL"}')

    reply = _call(local)
    _call(codex)

    assert reply.value == Decision(instrument_id="AAPL")
    assert reply.response.model == "served-model"
    assert local.calls == [{
        "stream": False, "model": "m", "response_format": {"type": "json_object"},
        "request_timeout_seconds": 45.0, "temperature": 0, "max_tokens": 500,
    }]
    strict = codex.calls[0]["response_format"]
    assert strict["type"] == "json_schema" and strict["json_schema"]["strict"] is True
    assert strict["json_schema"]["schema"]["required"] == ["instrument_id", "execution_authority"]


def test_a_fenced_object_is_accepted() -> None:
    reply = _call(FakeProvider("lmstudio", '```json\n{"instrument_id": "MSFT"}\n```'))
    assert reply.value.instrument_id == "MSFT"


@pytest.mark.parametrize(
    ("content", "finish_reason", "reason"),
    [
        ("", "stop", "empty"),
        ('{"instrument_id": "AA', "length", "truncated"),
        ("not json", "stop", "syntax"),
        ('{"instrument_id": "AAPL", "execution_authority": true}', "stop", "schema"),
    ],
)
def test_unusable_output_names_its_failure(content, finish_reason, reason) -> None:
    with pytest.raises(TradingModelOutputError) as caught:
        _call(FakeProvider("lmstudio", content, finish_reason=finish_reason))
    assert caught.value.reason == reason


def test_a_provider_failure_is_raised_unchanged() -> None:
    failure = ConnectionError("upstream reset")
    with pytest.raises(ConnectionError) as caught:
        _call(FakeProvider("lmstudio", error=failure))
    assert caught.value is failure


def test_a_provider_without_the_extended_arguments_is_called_plainly() -> None:
    class Plain:
        provider_name = "plain"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat_completion(self, messages, model=None, stream=False):
            self.calls.append({"model": model, "stream": stream})
            return ChatResponse(content='{"instrument_id": "AAPL"}', model="plain")

    provider = Plain()
    assert _call(provider).value.instrument_id == "AAPL"
    assert provider.calls == [{"model": "m", "stream": False}]
