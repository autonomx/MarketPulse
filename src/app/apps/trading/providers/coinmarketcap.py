from __future__ import annotations
from app.config.env import env_str as _env_str

import threading
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.security.provider_secret_store import load_trading_provider_secrets
from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import COINMARKETCAP_POLICY, bindings_for_instrument, instrument_by_id
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar, ProviderBinding

from .base import ProviderAdapter
from .bar_semantics import is_final_bar
from .errors import ProviderContractError, ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime


CMC_BASE_URL = "https://pro-api.coinmarketcap.com"
CMC_COIN_IDS = {
    "BTC": 1,
    "ETH": 1027,
    "USDT": 825,
    "USDC": 3408,
    "DAI": 4943,
}
MAX_CMC_HISTORY_LIMIT = 10_000
CMC_BASIC_DAILY_HISTORY_LIMIT = 365
CMC_EXTENDED_DAILY_HISTORY_LIMIT = 1_095


class _CoinMarketCapHttpError(ProviderDataUnavailableError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _environment_api_key() -> str:
    return (
        _env_str("COINMARKETCAP_API_KEY")
        or _env_str("CMC_PRO_API_KEY")
        or ""
    ).strip()


def coinmarketcap_api_key() -> str:
    environment_value = _environment_api_key()
    if environment_value:
        return environment_value
    credentials = load_trading_provider_secrets().get("coinmarketcap") or {}
    return str(credentials.get("api_key") or "").strip()


def coinmarketcap_configured() -> bool:
    return bool(coinmarketcap_api_key())


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderContractError(f"CoinMarketCap returned a non-numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ProviderContractError(f"CoinMarketCap returned a non-finite value: {value!r}")
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ProviderContractError("CoinMarketCap returned a malformed timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderContractError(f"CoinMarketCap returned an invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_key(value: object) -> date:
    return _timestamp(value).date()


class CoinMarketCapProvider(ProviderAdapter):
    provider_id = "coinmarketcap"
    policy = COINMARKETCAP_POLICY
    display_name = "CoinMarketCap"

    def configured(self) -> bool:
        return coinmarketcap_configured()

    def __init__(
        self,
        *,
        session: Any | None = None,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        base_url: str = CMC_BASE_URL,
    ) -> None:
        self.runtime = runtime or ProviderHttpRuntime(
            self.provider_id,
            session=session,
            max_concurrency=2,
        )
        self.session = self.runtime.session
        self.cache = cache or TradingMarketDataCache()
        self.base_url = base_url.rstrip("/")

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next(
            (
                item
                for item in bindings_for_instrument(instrument_id)
                if item.provider == self.provider_id
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"CoinMarketCap does not support instrument: {instrument_id}")
        return binding

    def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        cancellation: threading.Event | None,
    ) -> dict[str, Any]:
        api_key = coinmarketcap_api_key()
        if not api_key:
            raise ValueError(
                "CoinMarketCap API key is not configured. Set COINMARKETCAP_API_KEY "
                "or save it from Trading settings."
            )
        try:
            response = self.runtime.get(
                f"{self.base_url}{path}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-CMC_PRO_API_KEY": api_key,
                },
                timeout=20,
                cancellation=cancellation,
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response is None:
                raise ProviderDataUnavailableError(str(exc)) from exc
            try:
                payload = response.json()
            except ValueError:
                raise ProviderDataUnavailableError(str(exc)) from exc
            if not isinstance(payload, dict):
                raise ProviderContractError("CoinMarketCap returned a malformed error payload") from exc
            status = payload.get("status")
            if isinstance(status, dict):
                message = str(status.get("error_message") or str(exc))
            else:
                message = str(exc)
            raise _CoinMarketCapHttpError(
                message,
                status_code=int(getattr(response, "status_code", 0) or 0),
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("CoinMarketCap returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderContractError("CoinMarketCap returned a malformed payload")
        status = payload.get("status")
        if isinstance(status, dict) and int(status.get("error_code") or 0) != 0:
            message = str(status.get("error_message") or "CoinMarketCap request failed")
            raise ProviderDataUnavailableError(message)
        return payload

    def _global_series(
        self,
        limit: int,
        cancellation: threading.Event | None,
    ) -> dict[date, tuple[Decimal, Decimal]]:
        payload = self._historical_json(
            "/v1/global-metrics/quotes/historical",
            {"count": limit, "interval": "daily", "convert": "USD"},
            cancellation,
        )
        rows = ((payload.get("data") or {}).get("quotes") if isinstance(payload.get("data"), dict) else None)
        if not isinstance(rows, list):
            raise ProviderContractError("CoinMarketCap global metrics quotes are missing")
        series: dict[date, tuple[Decimal, Decimal]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            quote = row.get("quote")
            usd = quote.get("USD") if isinstance(quote, dict) else None
            if not isinstance(usd, dict) or "total_market_cap" not in usd:
                continue
            series[_date_key(row.get("timestamp"))] = (
                _decimal(usd["total_market_cap"]),
                _decimal(usd.get("total_volume_24h", "0")),
            )
        if not series:
            raise ProviderDataUnavailableError("CoinMarketCap returned no global market-cap history")
        return series

    def _asset_series(
        self,
        symbol: str,
        limit: int,
        cancellation: threading.Event | None,
    ) -> dict[date, tuple[Decimal, Decimal]]:
        coin_id = CMC_COIN_IDS.get(symbol)
        if coin_id is None:
            raise ValueError(f"CoinMarketCap market-cap mapping is unavailable for {symbol}")
        payload = self._historical_json(
            "/v3/cryptocurrency/quotes/historical",
            {
                "id": coin_id,
                "count": limit,
                "interval": "daily",
                "convert": "USD",
            },
            cancellation,
        )
        data = payload.get("data")
        if isinstance(data, list):
            rows = data[0].get("quotes") if len(data) == 1 and isinstance(data[0], dict) else None
        elif isinstance(data, dict):
            asset_data = data.get(str(coin_id)) or data.get(symbol)
            rows = (
                asset_data.get("quotes")
                if isinstance(asset_data, dict)
                else data.get("quotes")
            )
        else:
            rows = None
        if not isinstance(rows, list):
            raise ProviderContractError(f"CoinMarketCap quotes are missing for {symbol}")
        series: dict[date, tuple[Decimal, Decimal]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            quote = row.get("quote")
            usd = quote.get("USD") if isinstance(quote, dict) else None
            if not isinstance(usd, dict) or "market_cap" not in usd:
                continue
            series[_date_key(row.get("timestamp"))] = (
                _decimal(usd["market_cap"]),
                _decimal(usd.get("volume_24h", "0")),
            )
        if not series:
            raise ProviderDataUnavailableError(f"CoinMarketCap returned no market-cap history for {symbol}")
        return series

    def _historical_json(
        self,
        path: str,
        params: dict[str, Any],
        cancellation: threading.Event | None,
    ) -> dict[str, Any]:
        try:
            return self._get_json(path, params, cancellation)
        except _CoinMarketCapHttpError as exc:
            requested_count = int(params.get("count") or 0)
            if (
                exc.status_code == 400
                and requested_count > CMC_BASIC_DAILY_HISTORY_LIMIT
                and params.get("interval") == "daily"
            ):
                # Keep the request compatible with Basic, but allow plans
                # with a larger daily-history window to work when the chart
                # asks for an extended range. CMC reports both cases as a
                # 400, so try the documented three-year window before the
                # one-year Basic fallback.
                fallback_counts = [
                    count
                    for count in (
                        CMC_EXTENDED_DAILY_HISTORY_LIMIT,
                        CMC_BASIC_DAILY_HISTORY_LIMIT,
                    )
                    if count < requested_count
                ]
                last_error: _CoinMarketCapHttpError = exc
                for fallback_count in fallback_counts:
                    fallback_params = dict(params)
                    fallback_params["count"] = fallback_count
                    try:
                        return self._get_json(path, fallback_params, cancellation)
                    except _CoinMarketCapHttpError as fallback_error:
                        if fallback_error.status_code != 400:
                            raise
                        last_error = fallback_error
                raise last_error
            raise

    @staticmethod
    def _subtract(
        left: dict[date, tuple[Decimal, Decimal]],
        *right: dict[date, tuple[Decimal, Decimal]],
    ) -> dict[date, tuple[Decimal, Decimal]]:
        dates = set(left)
        for series in right:
            dates.intersection_update(series)
        return {
            key: (
                left[key][0] - sum((series[key][0] for series in right), Decimal("0")),
                left[key][1],
            )
            for key in dates
            if left[key][0] - sum((series[key][0] for series in right), Decimal("0")) > 0
        }

    def _symbol_series(
        self,
        symbol: str,
        limit: int,
        cancellation: threading.Event | None,
    ) -> dict[date, tuple[Decimal, Decimal]]:
        global_series = self._global_series(limit, cancellation)
        assets: dict[str, dict[date, tuple[Decimal, Decimal]]] = {}

        def asset(name: str) -> dict[date, tuple[Decimal, Decimal]]:
            if name not in assets:
                assets[name] = self._asset_series(name, limit, cancellation)
            return assets[name]

        base = symbol.removesuffix(".D")
        if base == "TOTAL":
            numerator = global_series
        elif base == "TOTAL2":
            numerator = self._subtract(global_series, asset("BTC"))
        elif base == "TOTAL3":
            numerator = self._subtract(global_series, asset("BTC"), asset("ETH"))
        else:
            numerator = asset(base)

        if symbol.endswith(".D"):
            dates = set(numerator).intersection(global_series)
            return {
                key: (
                    numerator[key][0] / global_series[key][0] * Decimal("100"),
                    Decimal("0"),
                )
                for key in dates
                if global_series[key][0] > 0
            }
        return numerator

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        if interval != "1d":
            raise ValueError("CoinMarketCap CRYPTOCAP history currently supports the 1d interval only")
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        binding = self.get_binding(instrument_id)
        clean_limit = max(1, min(int(limit), MAX_CMC_HISTORY_LIMIT))
        cache_key = self.cache.key(binding.binding_id, instrument_id, interval, clean_limit)

        def load() -> dict[str, Any]:
            series = self._symbol_series(binding.provider_symbol, clean_limit, cancellation)
            rows = [
                {"date": key.isoformat(), "value": str(value), "volume": str(volume)}
                for key, (value, volume) in sorted(series.items())[-clean_limit:]
            ]
            if not rows:
                raise ProviderDataUnavailableError(
                    f"CoinMarketCap returned no CRYPTOCAP bars for {binding.provider_symbol}"
                )
            return {"rows": rows, "history_complete": len(rows) < clean_limit}

        payload, entry, cached = self.cache.get_or_load(
            cache_key,
            load,
            ttl_seconds=300,
            source="coinmarketcap_global_metrics",
        )
        received_at = datetime.now(timezone.utc)
        bars: list[MarketBar] = []
        previous_value: Decimal | None = None
        for row in payload["rows"]:
            start = datetime.combine(date.fromisoformat(row["date"]), time.min, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            value = _decimal(row["value"])
            # CoinMarketCap's CRYPTOCAP history is a daily snapshot rather
            # than an OHLC feed. Preserve the observed day-to-day movement as
            # a proper candle: the prior snapshot is the open, the current
            # snapshot is the close, and their ordered values form the
            # observed high/low range.
            opening = previous_value if previous_value is not None else value
            high = max(opening, value)
            low = min(opening, value)
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval=interval,
                    start_time=start,
                    end_time=end,
                    open=opening,
                    high=high,
                    low=low,
                    close=value,
                    volume=_decimal(row.get("volume", "0")),
                    is_final=is_final_bar(end, received_at),
                    session="24x7",
                    provider=self.provider_id,
                    provider_event_id=row["date"],
                    received_at=received_at,
                )
            )
            previous_value = value
        provenance = DatasetProvenance(
            instrument_id=instrument_id,
            requested_binding=binding.binding_id,
            resolved_binding=binding.binding_id,
            dataset_fingerprint=entry.fingerprint,
            freshness_mode="cached" if cached else "polled",
            as_of=bars[-1].end_time,
            received_at=received_at,
            delay_seconds=self.policy.delay_seconds,
            cached=cached,
            history_complete=bool(payload.get("history_complete")),
        )
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=provenance,
            interval=interval,
            bars=bars,
        )

    def get_quote(
        self,
        instrument_id: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        result = self.get_bars(instrument_id, "1d", 1, cancellation)
        bar = result.bars[-1]
        return {
            "instrument_id": instrument_id,
            "binding_id": result.binding.binding_id,
            "provider": self.provider_id,
            "price": str(bar.close),
            "received_at": result.provenance.received_at.isoformat(),
            "freshness_mode": result.provenance.freshness_mode,
        }
