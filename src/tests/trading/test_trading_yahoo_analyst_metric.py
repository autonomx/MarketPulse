from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.fundamental_metric_data import YahooAnalystMetricAdapter
from app.apps.trading.providers.errors import ProviderDataUnavailableError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class FakeResponse:
    def __init__(self, *, payload=None, text="", status_code=200):
        self.payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://query2.finance.yahoo.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    def json(self):
        return self.payload


class YahooSession:
    def __init__(self):
        self.calls = []
        self.crumb_calls = 0

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == "https://query1.finance.yahoo.com/v1/test/getcrumb":
            self.crumb_calls += 1
            return FakeResponse(text="crumb-token")
        if "/v10/finance/quoteSummary/NVDA" in url:
            assert kwargs["params"]["crumb"] == "crumb-token"
            return FakeResponse(
                payload={
                    "quoteSummary": {
                        "result": [
                            {
                                "financialData": {
                                    "targetLowPrice": {"raw": 180.0},
                                    "targetMeanPrice": {"raw": 245.0},
                                    "targetHighPrice": {"raw": 310.0},
                                    "numberOfAnalystOpinions": {"raw": 52},
                                    "recommendationKey": "buy",
                                }
                            }
                        ],
                        "error": None,
                    }
                }
            )
        if url == "https://fc.yahoo.com":
            return FakeResponse(status_code=404)
        raise AssertionError(f"unexpected URL {url}")


def adapter(session: YahooSession) -> YahooAnalystMetricAdapter:
    return YahooAnalystMetricAdapter(
        cache=TradingMarketDataCache(),
        runtime=ProviderHttpRuntime(
            "yahoo-analyst-test",
            session=session,
            max_attempts=1,
            initial_backoff_seconds=0,
        ),
    )


def test_yahoo_analyst_adapter_uses_cookie_crumb_quote_summary_flow() -> None:
    session = YahooSession()
    provider = adapter(session)

    response = provider.analyst_targets("equity:NASDAQ:NVDA", "1d", 20)

    assert [series.points[0].value for series in response.series] == [
        Decimal("180.0"),
        Decimal("245.0"),
        Decimal("310.0"),
    ]
    assert response.metadata["analyst_opinions"] == 52
    assert response.metadata["recommendation"] == "buy"
    assert response.metadata["authentication"] == "yahoo_cookie_crumb"
    assert session.crumb_calls == 1

    # The provider response is cached, so reopening the chart does not churn
    # Yahoo's crumb or quoteSummary endpoints within the TTL.
    provider.analyst_targets("equity:NASDAQ:NVDA", "1d", 20)
    assert session.crumb_calls == 1


def test_yahoo_analyst_adapter_refuses_old_replay_snapshot() -> None:
    provider = adapter(YahooSession())

    with pytest.raises(ProviderDataUnavailableError, match="historical analyst-target"):
        provider.analyst_targets(
            "equity:NASDAQ:NVDA",
            "1d",
            20,
            end_time=datetime.now(timezone.utc) - timedelta(days=30),
        )
