from __future__ import annotations
from app.config.env import env_str as _env_str

"""Research-only consolidated Alpaca SIP evidence adapter.

This adapter exists solely for retrospective/formal scoring. It is intentionally
not registered as an execution provider and never produces execution authority.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.apps.trading.catalog import bindings_for_instrument
from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_evidence import SIPTradeEvent
from app.apps.trading.us_equity_calendar import us_equity_session

from .alpaca_iex import alpaca_iex_auth_headers
from .errors import ProviderContractError, ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


ALPACA_DATA_URL = "https://data.alpaca.markets"
_REGULAR_SALE_CODES = {"@"}
_ODD_LOT_CODES = {"I"}
_AUCTION_CODES = {"O", "6", "M", "Q"}
_LULD_CODES = {"5"}


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProviderContractError(f"Alpaca SIP is missing {field} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderContractError(f"Alpaca SIP returned invalid {field} timestamp") from exc
    if parsed.tzinfo is None:
        raise ProviderContractError(f"Alpaca SIP {field} timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, *, field: str) -> Decimal:
    if value in {None, ""}:
        raise ProviderContractError(f"Alpaca SIP evidence is missing {field}")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ProviderContractError(f"Alpaca SIP returned invalid {field}") from exc


def _symbol(instrument_id: str) -> str:
    binding = next(
        (
            item
            for item in bindings_for_instrument(instrument_id)
            if item.provider == "alpaca_iex"
        ),
        None,
    )
    if binding is None:
        raise ValueError(f"Alpaca SIP does not support instrument: {instrument_id}")
    return binding.provider_symbol


def _regular_window(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
    end = datetime.combine(session_date, regular_close_time(session_date), tzinfo=_ET)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


class AlpacaSipResearchProvider:
    provider_id = "alpaca_sip"

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
            max_concurrency=2,
        )
        self.data_url = (
            data_url
            or _env_str("OMNIX_ALPACA_DATA_URL")
            or ALPACA_DATA_URL
        ).rstrip("/")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def regular_session_5m_bars(
        self,
        instrument_id: str,
        session_date: date,
        *,
        cancellation=None,
    ) -> list[MarketBar]:
        symbol = _symbol(instrument_id)
        start, end = _regular_window(session_date)
        response = self.runtime.get(
            f"{self.data_url}/v2/stocks/{symbol}/bars",
            params={
                "timeframe": "5Min",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "adjustment": "raw",
                "feed": "sip",
                "sort": "asc",
                "limit": 1000,
            },
            headers=alpaca_iex_auth_headers(),
            timeout=15,
            cancellation=cancellation,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("Alpaca SIP returned invalid bars JSON") from exc
        raw_bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(raw_bars, list):
            raise ProviderContractError("Alpaca SIP bars response has no bars list")

        received_at = self.clock()
        if received_at.tzinfo is None:
            raise ProviderContractError("Alpaca SIP provider clock must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)
        bars: list[MarketBar] = []
        for raw in raw_bars:
            if not isinstance(raw, dict):
                continue
            bar_start = _parse_timestamp(raw.get("t"), field="bar")
            bar_end = bar_start + timedelta(minutes=5)
            if not start <= bar_start < end or bar_end > end:
                continue
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval="5m",
                    start_time=bar_start,
                    end_time=bar_end,
                    open=_decimal(raw.get("o"), field="open"),
                    high=_decimal(raw.get("h"), field="high"),
                    low=_decimal(raw.get("l"), field="low"),
                    close=_decimal(raw.get("c"), field="close"),
                    volume=_decimal(raw.get("v") or 0, field="volume"),
                    is_final=True,
                    adjustment_mode=AdjustmentMode.RAW,
                    session=us_equity_session(bar_start),
                    provider=self.provider_id,
                    provider_event_id=str(raw.get("t") or bar_start.isoformat()),
                    received_at=received_at,
                )
            )
        bars.sort(key=lambda bar: bar.start_time)
        if not bars:
            raise ProviderDataUnavailableError(
                f"Alpaca SIP returned no RAW 5m regular-session bars for {symbol}"
            )
        return bars

    def regular_session_trade_events(
        self,
        instrument_id: str,
        session_date: date,
        *,
        cancellation=None,
        max_pages: int = 500,
    ) -> list[SIPTradeEvent]:
        """Retrieve paginated consolidated trades for the formal price contract."""

        symbol = _symbol(instrument_id)
        start, end = _regular_window(session_date)
        headers = alpaca_iex_auth_headers()
        page_token: str | None = None
        received_at = self.clock()
        if received_at.tzinfo is None:
            raise ProviderContractError("Alpaca SIP provider clock must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)
        output: list[SIPTradeEvent] = []

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "feed": "sip",
                "sort": "asc",
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            response = self.runtime.get(
                f"{self.data_url}/v2/stocks/{symbol}/trades",
                params=params,
                headers=headers,
                timeout=20,
                cancellation=cancellation,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Alpaca SIP returned invalid trades JSON") from exc
            raw_trades = payload.get("trades") if isinstance(payload, dict) else None
            if not isinstance(raw_trades, list):
                raise ProviderContractError("Alpaca SIP trades response has no trades list")

            for raw in raw_trades:
                if not isinstance(raw, dict):
                    continue
                event_time = _parse_timestamp(raw.get("t"), field="trade")
                if not start <= event_time < end:
                    continue
                conditions = tuple(
                    str(value)
                    for value in (raw.get("c") or ())
                    if value not in {None, ""}
                )
                condition_set = set(conditions)
                size = _decimal(raw.get("s") or 0, field="size")
                unknown_conditions = condition_set - (
                    _REGULAR_SALE_CODES
                    | _ODD_LOT_CODES
                    | _AUCTION_CODES
                    | _LULD_CODES
                )
                raw_id = raw.get("i")
                sequence = None
                try:
                    sequence = int(raw_id) if raw_id is not None else None
                except (TypeError, ValueError):
                    sequence = None
                output.append(
                    SIPTradeEvent(
                        instrument_id=instrument_id,
                        price=_decimal(raw.get("p"), field="price"),
                        event_timestamp=event_time,
                        received_timestamp=received_at,
                        exchange=str(raw.get("x")) if raw.get("x") else None,
                        sip_feed="sip",
                        trade_condition_codes=conditions,
                        provider_event_id=str(raw_id) if raw_id is not None else None,
                        sequence=sequence,
                        odd_lot=(
                            size < Decimal("100")
                            or bool(condition_set & _ODD_LOT_CODES)
                        ),
                        auction=bool(condition_set & _AUCTION_CODES),
                        luld_related=bool(condition_set & _LULD_CODES),
                        special_condition=bool(unknown_conditions),
                    )
                )
            next_token = (
                payload.get("next_page_token")
                if isinstance(payload, dict)
                else None
            )
            if not next_token:
                break
            page_token = str(next_token)
        else:
            raise ProviderDataUnavailableError(
                f"Alpaca SIP trade pagination exceeded {max_pages} pages for {symbol}"
            )

        output.sort(
            key=lambda event: (
                event.event_timestamp,
                event.sequence if event.sequence is not None else -1,
                event.provider_event_id or "",
            )
        )
        if not output:
            raise ProviderDataUnavailableError(
                f"Alpaca SIP returned no regular-session trades for {symbol}"
            )
        return output


__all__ = ["AlpacaSipResearchProvider"]
