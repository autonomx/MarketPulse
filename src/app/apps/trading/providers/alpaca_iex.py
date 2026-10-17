from __future__ import annotations

from app.config.env import environment

import re
from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.apps.trading.catalog import POLICIES, bindings_for_instrument
from app.apps.trading.execution import (
    ExecutionEligibilityPolicy,
    ExecutionObservation,
    assess_execution_observation,
    execution_observation_from_quote,
)
from app.apps.trading.models import AdjustmentMode, MarketBar, ProviderBinding
from app.apps.trading.us_equity_calendar import us_equity_session

from .base import ProviderAdapter
from .alpaca_iex_status import default_alpaca_iex_status_cache
from .errors import ProviderContractError, ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime
from app.apps.trading.us_equity_calendar import EASTERN as _ET


ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_IEX_PARTIAL_MARKET = True
_EXTENDED_SESSION_OPEN = time(4, 0)
_INDICATOR_BAR_LIMIT = 1000


def _stored_credentials() -> dict[str, str]:
    try:
        from app.security.provider_secret_store import load_trading_provider_secrets

        return dict(load_trading_provider_secrets().get("alpaca_iex") or {})
    except Exception:
        # Provider startup must remain fail-closed if the optional local secret
        # store cannot be read. Environment credentials still work below.
        return {}


def _api_key() -> str:
    environment_value = (
        environment().get("OMNIX_ALPACA_API_KEY_ID")
        or environment().get("APCA_API_KEY_ID")
        or ""
    ).strip()
    return environment_value or _stored_credentials().get("api_key_id", "").strip()


def _api_secret() -> str:
    environment_value = (
        environment().get("OMNIX_ALPACA_API_SECRET_KEY")
        or environment().get("APCA_API_SECRET_KEY")
        or ""
    ).strip()
    return environment_value or _stored_credentials().get("secret_key", "").strip()


def alpaca_iex_configured() -> bool:
    return bool(_api_key() and _api_secret())


def alpaca_iex_auth_headers() -> dict[str, str]:
    """Return Alpaca auth headers for server-side Trading provider adapters only.

    The values are intentionally never exposed through an API response or UI
    model. This helper lets historical research adapters reuse the same encrypted
    local credential source as the execution provider without duplicating secret
    resolution logic.
    """

    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise ProviderDataUnavailableError(
            "Alpaca IEX credentials are not configured; set them in the Trading UI "
            "or configure OMNIX_ALPACA_API_KEY_ID and OMNIX_ALPACA_API_SECRET_KEY"
        )
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if value is None or value == "":
        raise ProviderContractError(
            f"Alpaca IEX snapshot is missing {field} timestamp"
        )
    parsed: datetime | None = None
    raw = repr(value)[:160]
    try:
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            epoch = Decimal(str(value))
        elif isinstance(value, str) and re.fullmatch(
            r"[+-]?\d+(?:\.\d+)?", value.strip()
        ):
            epoch = Decimal(value.strip())
        else:
            epoch = None
        if epoch is not None:
            magnitude = abs(epoch)
            if magnitude >= Decimal("1e17"):
                epoch /= Decimal("1e9")
            elif magnitude >= Decimal("1e14"):
                epoch /= Decimal("1e6")
            elif magnitude >= Decimal("1e11"):
                epoch /= Decimal("1e3")
            parsed = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            match = re.match(
                r"^(?P<prefix>\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2})"
                r"(?:\.(?P<fraction>\d+))?(?P<offset>[+-]\d{2}:?\d{2})$",
                text,
            )
            if match:
                fraction = (match.group("fraction") or "")[:6]
                offset = match.group("offset")
                if len(offset) == 5 and ":" not in offset:
                    offset = offset[:3] + ":" + offset[3:]
                text = (
                    match.group("prefix").replace("t", "T")
                    + (f".{fraction}" if fraction else "")
                    + offset
                )
            parsed = datetime.fromisoformat(text)
        else:
            raise TypeError("unsupported timestamp type")
    except (ValueError, TypeError, InvalidOperation, OverflowError, OSError) as exc:
        raise ProviderContractError(
            f"Alpaca IEX returned invalid {field} timestamp (value={raw})"
        ) from exc
    if parsed is None:
        raise ProviderContractError(
            f"Alpaca IEX returned invalid {field} timestamp (value={raw})"
        )
    if parsed.tzinfo is None:
        raise ProviderContractError(
            f"Alpaca IEX {field} timestamp must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _round_lot_shares(value: Any) -> Decimal | None:
    """Alpaca stock quote sizes are expressed in 100-share round lots."""
    if value in {None, ""}:
        return None
    try:
        lots = Decimal(str(value))
    except Exception as exc:
        raise ProviderContractError("Alpaca IEX returned invalid quote size") from exc
    if lots < 0:
        raise ProviderContractError("Alpaca IEX quote size cannot be negative")
    return lots * Decimal("100")


def _decimal_bar_value(value: Any, *, field: str) -> Decimal:
    if value in {None, ""}:
        raise ProviderContractError(f"Alpaca IEX historical bar is missing {field}")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ProviderContractError(
            f"Alpaca IEX historical bar returned invalid {field}"
        ) from exc


class AlpacaIexExecutionProvider(ProviderAdapter):
    """Official Alpaca IEX adapter for paper execution and causal evidence.

    Alpaca Basic/Paper Only accounts expose real-time IEX data rather than the
    consolidated SIP. Omnix therefore records the provider as ``alpaca_iex`` and
    never represents this feed as full-market NBBO coverage. Historical 1-minute
    bars exposed here are for causal SHADOW/research telemetry only and do not
    grant execution authority.
    """

    provider_id = "alpaca_iex"
    policy = POLICIES[provider_id]
    display_name = "Alpaca IEX"

    def configured(self) -> bool:
        return alpaca_iex_configured()

    def __init__(
        self,
        *,
        session: Any | None = None,
        runtime: ProviderHttpRuntime | None = None,
        data_url: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime or ProviderHttpRuntime(
            self.provider_id,
            session=session,
            max_concurrency=4,
        )
        self.session = self.runtime.session
        self.data_url = (
            data_url
            or environment().get("OMNIX_ALPACA_DATA_URL")
            or ALPACA_DATA_URL
        ).rstrip("/")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

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
            raise ValueError(f"Alpaca IEX does not support instrument: {instrument_id}")
        return binding

    def indicator_bars_as_of(
        self,
        instrument_id: str,
        *,
        as_of: datetime,
        cancellation=None,
    ) -> list[MarketBar]:
        try:
            return self._indicator_bars_as_of(
                instrument_id,
                as_of=as_of,
                cancellation=cancellation,
            )
        except ProviderContractError as exc:
            if str(exc) == "Alpaca IEX historical-bars response has no bars list":
                return []
            raise

    def _indicator_bars_as_of(
        self,
        instrument_id: str,
        *,
        as_of: datetime,
        cancellation=None,
    ) -> list[MarketBar]:
        """Return completed same-day 1m IEX bars from 04:00 ET through ``as_of``.

        Alpaca timestamps one-minute bars at bar *start*. The provider can return
        a bar whose start is before ``end`` but whose close is not yet knowable at
        ``as_of``. Omnix therefore applies the stronger causal rule
        ``bar_start + 1m <= as_of`` before exposing a bar to indicator telemetry.
        """

        if as_of.tzinfo is None:
            raise ValueError("indicator history as_of must be timezone-aware")
        cutoff = as_of.astimezone(timezone.utc)
        local_cutoff = cutoff.astimezone(_ET)
        session_start = datetime.combine(
            local_cutoff.date(),
            _EXTENDED_SESSION_OPEN,
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        if cutoff <= session_start:
            return []

        binding = self.get_binding(instrument_id)
        headers = alpaca_iex_auth_headers()
        params: dict[str, Any] = {
            "timeframe": "1Min",
            "start": session_start.isoformat().replace("+00:00", "Z"),
            "end": cutoff.isoformat().replace("+00:00", "Z"),
            "adjustment": "raw",
            "feed": "iex",
            "sort": "asc",
            "limit": _INDICATOR_BAR_LIMIT,
        }
        response = self.runtime.get(
            f"{self.data_url}/v2/stocks/{binding.provider_symbol}/bars",
            params=params,
            headers=headers,
            timeout=10,
            cancellation=cancellation,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("Alpaca IEX returned invalid historical-bars JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderContractError("Alpaca IEX historical-bars payload is malformed")
        raw_bars = payload.get("bars")
        if not isinstance(raw_bars, list):
            raise ProviderContractError("Alpaca IEX historical-bars response has no bars list")

        # An authenticated historical-bars request can legitimately return an
        # empty list when the symbol has not printed in the requested window
        # yet.  That is a typed unavailable-data result, not a provider
        # contract failure.  Returning an empty causal series lets SHADOW
        # evaluators record a data gap without converting every poll into an
        # exception.  Non-empty malformed payloads still fail closed below.
        if not raw_bars:
            return []

        received_at = self.clock()
        if received_at.tzinfo is None:
            raise ProviderContractError("Alpaca IEX provider clock must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)
        bars: list[MarketBar] = []
        for raw in raw_bars:
            if not isinstance(raw, dict):
                continue
            start_time = _parse_timestamp(raw.get("t"), field="historical bar")
            end_time = start_time + timedelta(minutes=1)
            if start_time < session_start or end_time > cutoff:
                continue
            if start_time.astimezone(_ET).date() != local_cutoff.date():
                continue
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval="1m",
                    start_time=start_time,
                    end_time=end_time,
                    open=_decimal_bar_value(raw.get("o"), field="open"),
                    high=_decimal_bar_value(raw.get("h"), field="high"),
                    low=_decimal_bar_value(raw.get("l"), field="low"),
                    close=_decimal_bar_value(raw.get("c"), field="close"),
                    volume=_decimal_bar_value(raw.get("v") or 0, field="volume"),
                    is_final=True,
                    adjustment_mode=AdjustmentMode.RAW,
                    session=us_equity_session(start_time),
                    provider=self.provider_id,
                    provider_event_id=str(raw.get("t") or start_time.isoformat()),
                    received_at=received_at,
                )
            )
        bars.sort(key=lambda bar: bar.start_time)
        if not bars:
            raise ProviderDataUnavailableError(
                f"Alpaca IEX returned no completed same-day 1m bars for {binding.provider_symbol}"
            )
        return bars

    def execution_observation(
        self,
        instrument_id: str,
        *,
        policy: ExecutionEligibilityPolicy | None = None,
        cancellation=None,
    ) -> ExecutionObservation:
        headers = alpaca_iex_auth_headers()

        binding = self.get_binding(instrument_id)
        response = self.runtime.get(
            f"{self.data_url}/v2/stocks/{binding.provider_symbol}/snapshot",
            params={"feed": "iex"},
            headers=headers,
            timeout=10,
            cancellation=cancellation,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("Alpaca IEX returned invalid snapshot JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderContractError("Alpaca IEX snapshot payload is malformed")

        latest_quote = payload.get("latestQuote")
        latest_trade = payload.get("latestTrade")
        if not isinstance(latest_trade, dict):
            raise ProviderDataUnavailableError("Alpaca IEX snapshot has no latest trade")

        quote_available = isinstance(latest_quote, dict)
        quote_payload: dict[str, Any] = latest_quote if isinstance(latest_quote, dict) else {}
        quote_timestamp_degraded = False
        quote_time = None
        if quote_available:
            try:
                quote_time = _parse_timestamp(quote_payload.get("t"), field="quote")
            except ProviderContractError:
                # A malformed quote timestamp makes the book non-causal. Keep a
                # valid latest trade as research/SHADOW price evidence, but drop
                # bid/ask entirely so execution eligibility remains fail-closed.
                quote_available = False
                quote_timestamp_degraded = True
        trade_time = _parse_timestamp(latest_trade.get("t"), field="trade")
        # Execution freshness belongs to the executable bid/ask book. An older
        # last trade must not make a current quote stale.
        source_time = quote_time if quote_available and quote_time is not None else trade_time

        minute_bar = payload.get("minuteBar")
        bar_start_time: datetime | None = None
        bar_high = None
        bar_low = None
        bar_volume = None
        if isinstance(minute_bar, dict):
            timestamp = minute_bar.get("t")
            if timestamp not in {None, ""}:
                bar_start_time = _parse_timestamp(timestamp, field="minute bar")
            bar_high = minute_bar.get("h")
            bar_low = minute_bar.get("l")
            bar_volume = minute_bar.get("v")

        daily_bar = payload.get("dailyBar")
        cumulative_volume = (
            daily_bar.get("v")
            if isinstance(daily_bar, dict) and daily_bar.get("v") is not None
            else None
        )
        now = self.clock()
        if now.tzinfo is None:
            raise ProviderContractError("Alpaca IEX provider clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        quote: dict[str, Any] = {
            "instrument_id": instrument_id,
            "binding_id": binding.binding_id,
            "provider": self.provider_id,
            "bid": quote_payload.get("bp") if quote_available else None,
            "ask": quote_payload.get("ap") if quote_available else None,
            "bid_size": _round_lot_shares(quote_payload.get("bs")) if quote_available else None,
            "ask_size": _round_lot_shares(quote_payload.get("as")) if quote_available else None,
            "last": latest_trade.get("p"),
            "high": bar_high,
            "low": bar_low,
            "bar_volume": bar_volume,
            "bar_start_time": bar_start_time.isoformat() if bar_start_time else None,
            "cumulative_volume": cumulative_volume,
            "source_time": source_time,
            "received_at": now.isoformat(),
            "session": us_equity_session(source_time),
            "freshness_mode": "fallback" if quote_timestamp_degraded else "live",
            "halted": default_alpaca_iex_status_cache().halted(binding.provider_symbol),
        }
        try:
            observation = execution_observation_from_quote(
                quote,
                binding_id=binding.binding_id,
                provider=self.provider_id,
                received_at=now,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderContractError("Alpaca IEX snapshot is missing executable prices") from exc
        return assess_execution_observation(observation, policy)
