"""A screen from words (TVP-9.4): the model proposes, the scanner validates, the user runs."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.scanner_api import create_trading_scanner_router
from app.apps.trading.screener_words import SCREEN_FROM_WORDS_TEMPLATE, propose_screen, validated_proposal


class Provider:
    name = "fake"

    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.messages: list = []

    def chat_completion(self, messages, **kwargs):
        self.messages = messages
        return {"choices": [{"message": {"content": json.dumps(self.answer)}}]}


ANSWER = {
    "interval": "1d",
    "rules": [
        {"metric": "percent_change", "operator": "gt", "threshold": 5, "period": 14, "lookback_bars": 1, "role": "filter", "source": None},
        {"metric": "relative_volume", "operator": "gte", "threshold": 2, "period": 20, "lookback_bars": 1, "role": "filter"},
        # Written for the default period; the model asked for 21: the output moves to the 21-period line.
        {"metric": "indicator", "operator": "lt", "threshold": 30, "period": 14, "lookback_bars": 1, "role": "column",
         "source": {"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 21}, "output": "rsi:14"}},
        {"metric": "short_interest", "operator": "gt", "threshold": 1e9},
    ],
    "unsupported": ["above the 200-day average (comparing two lines)"],
    "notes": "Daily gainers on heavy volume.",
}


def test_the_model_proposes_and_the_scanner_validates() -> None:
    provider = Provider(ANSWER)
    proposal = propose_screen("daily gainers over 5% on twice the usual volume, show RSI", provider_factory=lambda: provider, catalog=[{"id": "rsi", "outputs": ["rsi:14"]}])
    assert proposal.interval == "1d" and proposal.provider == "fake" and proposal.notes == "Daily gainers on heavy volume."
    assert [(rule.metric, rule.operator, float(rule.threshold), rule.role) for rule in proposal.rules] == [
        ("percent_change", "gt", 5.0, "filter"), ("relative_volume", "gte", 2.0, "filter"), ("indicator", "lt", 30.0, "column"),
    ]
    assert proposal.rules[2].source.output == "rsi:21"
    assert [rule.rule_id for rule in proposal.rules] == ["proposed-1", "proposed-2", "proposed-3"]
    assert proposal.unsupported[0].startswith("above the 200-day") and "short_interest" in proposal.unsupported[1]
    system, user = provider.messages
    assert system.content == SCREEN_FROM_WORDS_TEMPLATE.text
    assert json.loads(user.content) == {"description": "daily gainers over 5% on twice the usual volume, show RSI", "indicators": [{"id": "rsi", "outputs": ["rsi:14"]}]}


def test_no_filter_or_no_request_is_refused() -> None:
    with pytest.raises(ValueError, match="didn't give any screener filters"):
        validated_proposal({"rules": [{"metric": "close", "operator": "gt", "threshold": 1, "role": "column"}]})
    with pytest.raises(ValueError, match="in a sentence"):
        propose_screen("   ", provider_factory=lambda: Provider(ANSWER))
    assert validated_proposal({"interval": "2d", "rules": [{"metric": "close", "operator": "gt", "threshold": 1}]}).interval == "1d"


def test_the_api_returns_a_proposal_and_never_runs_it() -> None:
    calls: list[str] = []

    def propose(text: str):
        calls.append(text)
        return validated_proposal(ANSWER)

    def failing(text: str):
        raise RuntimeError("provider down")

    class Manager:
        def start(self, *args, **kwargs):  # a run would go through the manager; a proposal must not
            raise AssertionError("a proposal must not run a scan")

    app = FastAPI()
    app.include_router(create_trading_scanner_router(manager_factory=Manager, propose=propose))
    client = TestClient(app)
    body = client.post("/api/trading/scanners/propose", json={"text": "gainers"}).json()
    assert calls == ["gainers"] and len(body["rules"]) == 3 and body["rules"][0]["metric"] == "percent_change"
    bad = FastAPI()
    bad.include_router(create_trading_scanner_router(manager_factory=Manager, propose=failing))
    assert TestClient(bad).post("/api/trading/scanners/propose", json={"text": "gainers"}).status_code == 502
    refusing = FastAPI()
    refusing.include_router(create_trading_scanner_router(manager_factory=Manager, propose=lambda text: validated_proposal({"rules": []})))
    assert TestClient(refusing).post("/api/trading/scanners/propose", json={"text": "x"}).status_code == 422
