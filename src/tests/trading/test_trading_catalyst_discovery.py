from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.apps.trading.catalyst_discovery import discover_yahoo_catalyst_headlines


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Runtime:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


def test_yahoo_catalyst_capture_keeps_only_point_in_time_recent_headlines() -> None:
    now = datetime.now(timezone.utc)
    recent = int((now - timedelta(hours=2)).timestamp())
    too_old = int((now - timedelta(hours=90)).timestamp())
    future = int((now + timedelta(minutes=5)).timestamp())
    runtime = _Runtime(
        {
            "news": [
                {
                    "title": "V11 announces new distribution agreement",
                    "providerPublishTime": recent,
                    "publisher": "Example News",
                    "link": "https://example.test/v11-agreement",
                    "uuid": "one",
                },
                {
                    "title": "V11 announces new distribution agreement",
                    "providerPublishTime": recent,
                    "publisher": "Example News",
                    "link": "https://example.test/v11-agreement",
                    "uuid": "one",
                },
                {
                    "title": "Old V11 item",
                    "providerPublishTime": too_old,
                    "publisher": "Example News",
                    "link": "https://example.test/old",
                },
                {
                    "title": "Future V11 item",
                    "providerPublishTime": future,
                    "publisher": "Example News",
                    "link": "https://example.test/future",
                },
            ]
        }
    )

    evidence = discover_yahoo_catalyst_headlines(
        instrument_id="equity:NASDAQ:V11",
        symbol="V11",
        evaluation_time=now,
        runtime=runtime,
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.instrument_id == "equity:NASDAQ:V11"
    assert item.source_type == "news"
    assert item.headline == "V11 announces new distribution agreement"
    assert item.published_at <= now
    assert item.facts["evidence_scope"] == "headline"
    assert runtime.calls


def test_yahoo_catalyst_capture_extracts_deterministic_supply_flags() -> None:
    now = datetime.now(timezone.utc)
    runtime = _Runtime(
        {
            "news": [
                {
                    "title": "V11 enters at-the-market sales agreement with warrants",
                    "providerPublishTime": int((now - timedelta(minutes=15)).timestamp()),
                    "publisher": "Example News",
                    "link": "https://example.test/v11-atm",
                }
            ]
        }
    )

    evidence = discover_yahoo_catalyst_headlines(
        instrument_id="equity:NASDAQ:V11",
        symbol="V11",
        evaluation_time=now,
        runtime=runtime,
    )

    assert len(evidence) == 1
    assert "atm" in evidence[0].dilution_flags
    assert "warrants" in evidence[0].dilution_flags


def test_yahoo_catalyst_capture_cannot_reconstruct_historical_research() -> None:
    with pytest.raises(ValueError, match="current-only"):
        discover_yahoo_catalyst_headlines(
            instrument_id="equity:NASDAQ:V11",
            symbol="V11",
            evaluation_time=datetime.now(timezone.utc) - timedelta(days=1),
            runtime=_Runtime({"news": []}),
        )
