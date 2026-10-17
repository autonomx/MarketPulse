from datetime import date, datetime, timezone

from app.apps.trading.prospective_prediction_evidence import sip_trade_eligible
from app.apps.trading.providers import alpaca_sip


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Runtime:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.responses.pop(0))


def test_sip_bars_are_raw_research_only_consolidated(monkeypatch):
    runtime = _Runtime(
        [
            {
                "bars": [
                    {
                        "t": "2026-09-17T13:30:00Z",
                        "o": 10,
                        "h": 11,
                        "l": 9.5,
                        "c": 10.5,
                        "v": 1000,
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr(alpaca_sip, "_symbol", lambda instrument_id: "TEST")
    monkeypatch.setattr(alpaca_sip, "alpaca_iex_auth_headers", lambda: {})
    provider = alpaca_sip.AlpacaSipResearchProvider(
        runtime=runtime,
        clock=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    bars = provider.regular_session_5m_bars(
        "equity:NASDAQ:TEST",
        date(2026, 9, 17),
    )
    assert bars[0].provider == "alpaca_sip"
    assert bars[0].adjustment_mode == "raw"
    _, kwargs = runtime.calls[0]
    assert kwargs["params"]["feed"] == "sip"
    assert kwargs["params"]["adjustment"] == "raw"
    assert not hasattr(provider, "execution_observation")


def test_sip_trade_pagination_and_unknown_conditions_fail_closed(monkeypatch):
    runtime = _Runtime(
        [
            {
                "trades": [
                    {
                        "t": "2026-09-17T13:30:00Z",
                        "p": 10,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "i": 1,
                    }
                ],
                "next_page_token": "next",
            },
            {
                "trades": [
                    {
                        "t": "2026-09-17T19:59:59Z",
                        "p": 11,
                        "s": 100,
                        "x": "Q",
                        "c": ["Z"],
                        "i": 2,
                    }
                ],
                "next_page_token": None,
            },
        ]
    )
    monkeypatch.setattr(alpaca_sip, "_symbol", lambda instrument_id: "TEST")
    monkeypatch.setattr(alpaca_sip, "alpaca_iex_auth_headers", lambda: {})
    provider = alpaca_sip.AlpacaSipResearchProvider(
        runtime=runtime,
        clock=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    events = provider.regular_session_trade_events(
        "equity:NASDAQ:TEST",
        date(2026, 9, 17),
    )
    assert len(events) == 2
    assert sip_trade_eligible(events[0]) is True
    assert events[1].special_condition is True
    assert sip_trade_eligible(events[1]) is False
    assert runtime.calls[1][1]["params"]["page_token"] == "next"
