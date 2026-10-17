from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import bindings_for_instrument, instrument_by_id
from app.apps.trading.metric_data import MarketMetricPoint, MarketMetricResponse, MarketMetricSeries
from app.apps.trading.providers.errors import ProviderContractError, ProviderDataUnavailableError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class YahooAnalystMetricAdapter:
    """Yahoo analyst snapshots with the cookie/crumb flow required by quoteSummary.

    Yahoo's chart endpoint is keyless, while quoteSummary requires a short-lived
    crumb coupled to a session cookie. Keep that authentication detail inside the
    provider adapter so chart indicators never need to know about it.
    """

    provider_id = "yahoo"

    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        timeout_seconds: float = 20.0,
        crumb_ttl_seconds: float = 1_800.0,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=64)
        self.runtime = runtime or ProviderHttpRuntime("yahoo-analyst-metrics", max_concurrency=2)
        self.timeout_seconds = timeout_seconds
        self.crumb_ttl_seconds = max(60.0, crumb_ttl_seconds)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
            )
        }
        self._crumb: str | None = None
        self._crumb_expires_at = 0.0
        self._crumb_lock = threading.Lock()

    @staticmethod
    def _symbol(instrument_id: str) -> str:
        instrument = instrument_by_id(instrument_id)
        if instrument is None or str(instrument.asset_class) != "equity":
            raise ValueError("Yahoo analyst metrics require an equity instrument")
        binding = next(
            (item for item in bindings_for_instrument(instrument_id) if item.provider == "yahoo"),
            None,
        )
        if binding is None:
            raise ValueError(f"Yahoo binding unavailable for {instrument_id}")
        return binding.provider_symbol

    @staticmethod
    def _decimal(value: Any, *, field: str) -> Decimal:
        if isinstance(value, dict) and "raw" in value:
            value = value["raw"]
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ProviderContractError(f"invalid Yahoo numeric {field}") from exc
        if not result.is_finite():
            raise ProviderContractError(f"non-finite Yahoo numeric {field}")
        return result

    @staticmethod
    def _raw(value: Any) -> Any:
        return value.get("raw") if isinstance(value, dict) and "raw" in value else value

    def _bootstrap_cookie(self) -> None:
        session = self.runtime.session
        getter = getattr(session, "get", None)
        if not callable(getter):
            return
        try:
            # fc.yahoo.com intentionally commonly answers 404; the useful side
            # effect is the A3 cookie, so do not call raise_for_status here.
            getter("https://fc.yahoo.com", headers=self.headers, timeout=self.timeout_seconds)
        except httpx.HTTPError:
            return

    def _request_crumb(self) -> str:
        response = self.runtime.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        crumb = str(getattr(response, "text", "") or "").strip()
        if not crumb or crumb.lower().startswith("too many requests"):
            raise ProviderDataUnavailableError("Yahoo did not return a usable quoteSummary crumb")
        return crumb

    def _ensure_crumb(self, *, force: bool = False) -> str:
        now = time.monotonic()
        if not force and self._crumb and self._crumb_expires_at > now:
            return self._crumb
        with self._crumb_lock:
            now = time.monotonic()
            if not force and self._crumb and self._crumb_expires_at > now:
                return self._crumb
            try:
                crumb = self._request_crumb()
            except (httpx.HTTPError, ProviderDataUnavailableError):
                self._bootstrap_cookie()
                crumb = self._request_crumb()
            self._crumb = crumb
            self._crumb_expires_at = time.monotonic() + self.crumb_ttl_seconds
            return crumb

    def _quote_summary(self, symbol: str) -> dict[str, Any]:
        def request(force_crumb: bool = False) -> dict[str, Any]:
            crumb = self._ensure_crumb(force=force_crumb)
            response = self.runtime.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                params={
                    "modules": "financialData",
                    "formatted": "false",
                    "lang": "en-US",
                    "region": "US",
                    "crumb": crumb,
                },
                headers=self.headers,
                timeout=self.timeout_seconds,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Yahoo quoteSummary returned invalid JSON") from exc
            result = (((payload or {}).get("quoteSummary") or {}).get("result") or [None])[0]
            financial_data = result.get("financialData") if isinstance(result, dict) else None
            if not isinstance(financial_data, dict):
                error = ((payload or {}).get("quoteSummary") or {}).get("error")
                raise ProviderDataUnavailableError(f"Yahoo returned no financialData: {error or 'unknown error'}")
            return financial_data

        try:
            return request()
        except httpx.HTTPStatusError as exc:
            status = getattr(exc.response, "status_code", None)
            if status not in {401, 403}:
                raise
            self._crumb = None
            self._crumb_expires_at = 0.0
            return request(force_crumb=True)

    def analyst_targets(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
    ) -> MarketMetricResponse:
        del limit
        now = datetime.now(timezone.utc)
        # Current analyst consensus has no honest point-in-time history in this
        # adapter. A small grace window permits weekends/holidays on daily charts
        # without leaking a current snapshot into an old replay session.
        if end_time is not None and end_time.astimezone(timezone.utc) < now - timedelta(days=3):
            raise ProviderDataUnavailableError(
                "historical analyst-target snapshots are unavailable; current consensus only"
            )
        symbol = self._symbol(instrument_id)
        key = self.cache.key("metric", "yahoo-auth", "analyst-targets", symbol)

        def load() -> dict[str, Any]:
            return {"financial_data": self._quote_summary(symbol)}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=900,
            source="yahoo_quote_summary_financial_data",
        )
        financial_data = payload.get("financial_data")
        if not isinstance(financial_data, dict):
            raise ProviderDataUnavailableError("Yahoo analyst target data is unavailable")

        fields = (
            ("target-low", "Analyst Target Low", "targetLowPrice"),
            ("target-mean", "Analyst Target Mean", "targetMeanPrice"),
            ("target-high", "Analyst Target High", "targetHighPrice"),
        )
        series: list[MarketMetricSeries] = []
        for key_name, title, field in fields:
            raw = self._raw(financial_data.get(field))
            if raw is None:
                continue
            series.append(
                MarketMetricSeries(
                    key=key_name,
                    title=title,
                    unit="price",
                    points=[MarketMetricPoint(time=now, value=self._decimal(raw, field=field))],
                )
            )
        if not series:
            raise ProviderDataUnavailableError("Yahoo analyst target fields are unavailable")

        opinions = self._raw(financial_data.get("numberOfAnalystOpinions"))
        recommendation = self._raw(financial_data.get("recommendationKey"))
        return MarketMetricResponse(
            instrument_id=instrument_id,
            metric="yahoo.analyst_price_forecast",
            provider=self.provider_id,
            interval=interval,
            series=series,
            received_at=now,
            freshness_mode="cached" if cached else "polled",
            history_complete=False,
            metadata={
                "symbol": symbol,
                "snapshot_only": True,
                "analyst_opinions": opinions,
                "recommendation": recommendation,
                "authentication": "yahoo_cookie_crumb",
            },
        )


_default_adapter: YahooAnalystMetricAdapter | None = None
_default_lock = threading.Lock()


def default_yahoo_analyst_metric_adapter() -> YahooAnalystMetricAdapter:
    global _default_adapter
    if _default_adapter is None:
        with _default_lock:
            if _default_adapter is None:
                _default_adapter = YahooAnalystMetricAdapter(
                    cache=TradingMarketDataCache(max_entries=64),
                )
    return _default_adapter
