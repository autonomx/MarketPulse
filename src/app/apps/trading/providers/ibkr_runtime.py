from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

logger = logging.getLogger(__name__)

"""Process-wide IBKR transport and runtime.

IBKR is reached through IB Gateway with the official Python client, which is
intentionally optional and keeps authentication inside Gateway, or through
IBKR's Web API with OAuth keys (``ibkr_web_api.py``), held in the OS-protected
provider secret store. Omnix never stores an IBKR username or password.
Unit tests use FakeIbkrTransport so CI does not require Gateway or IBKR's local
TWS API installation.
"""

import threading
import time as time_module
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol

from app.apps.trading.ibkr_settings import IbkrSettings, load_ibkr_settings

# A range that ended this long ago is final: its answer, including "no trades",
# is remembered instead of asking the Gateway again on every repair cycle. A
# more recent range is remembered briefly, so strategies evaluating the same
# stock moments apart share one request.
HISTORICAL_CACHE_SETTLE = timedelta(minutes=3)
HISTORICAL_CACHE_RECENT_TTL = timedelta(seconds=30)
HISTORICAL_CACHE_MAX_ENTRIES = 50_000


IBKR_MARKET_DATA_TYPES = {
    1: "LIVE",
    2: "FROZEN",
    3: "DELAYED",
    4: "DELAYED_FROZEN",
}


class IbkrRuntimeError(RuntimeError):
    pass


class IbkrClientUnavailableError(IbkrRuntimeError):
    pass


class IbkrContractUnavailableError(IbkrRuntimeError):
    pass


class IbkrContractAmbiguousError(IbkrRuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IbkrContractIdentity:
    con_id: int
    symbol: str
    local_symbol: str
    sec_type: str
    currency: str
    exchange: str
    primary_exchange: str
    trading_class: str = ""
    qualified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class IbkrQuoteSnapshot:
    contract: IbkrContractIdentity
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    last: Decimal | None = None
    last_size: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    cumulative_volume: Decimal | None = None
    # Top-of-book ticks do not carry an exchange timestamp in reqMktData.
    # Track when each field was observed locally and keep the last-trade
    # timestamp separate from BBO freshness.
    bid_observed_at: datetime | None = None
    ask_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    last_trade_at: datetime | None = None
    source_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    market_data_type: str = "UNKNOWN"
    provider_sequence: int | None = None

    @property
    def live_entitled(self) -> bool:
        return self.market_data_type == "LIVE"


@dataclass(frozen=True, slots=True)
class IbkrHistoricalBar:
    start_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


# The reason a bar stream closes when IBKR has no trades in its opening window.
BAR_STREAM_NO_DATA = "ibkr_historical_no_data"


@dataclass(frozen=True, slots=True)
class IbkrBarStreamEvent:
    """One message on a live 1m bar stream.

    ``history`` carries the bars the Gateway sends first, ``update`` the forming
    bar each time it changes (a new start time means the previous minute closed),
    and ``closed`` ends the stream: the socket dropped or IBKR rejected it.
    """

    kind: Literal["history", "update", "closed"]
    bars: tuple[IbkrHistoricalBar, ...] = ()
    reason: str | None = None


def _historical_bar(bar: Any) -> IbkrHistoricalBar | None:
    raw_date = getattr(bar, "date", "")
    try:
        start = datetime.fromtimestamp(float(raw_date), tz=timezone.utc)
    except Exception:
        text = str(raw_date)
        parsed = None
        for fmt in ("%Y%m%d  %H:%M:%S", "%Y%m%d-%H:%M:%S", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
        start = parsed
    return IbkrHistoricalBar(
        start_time=start,
        open=Decimal(str(bar.open)),
        high=Decimal(str(bar.high)),
        low=Decimal(str(bar.low)),
        close=Decimal(str(bar.close)),
        volume=Decimal(str(bar.volume)),
    )


def _duration_text(duration_seconds: int) -> str:
    seconds = max(1, int(duration_seconds))
    return f"{seconds} S" if seconds <= 86_400 else f"{max(1, (seconds + 86_399) // 86_400)} D"


class IbkrTransport(Protocol):
    def connect(self, host: str, port: int, client_id: int, timeout_seconds: float = 8.0) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def qualify_stock(self, symbol: str, *, currency: str = "USD") -> list[IbkrContractIdentity]: ...
    def subscribe_quote(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrQuoteSnapshot], None],
    ) -> int: ...
    def unsubscribe_quote(self, token: int) -> None: ...
    def historical_bars(
        self,
        contract: IbkrContractIdentity,
        *,
        end: datetime,
        duration_seconds: int,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        timeout_seconds: float = 20.0,
    ) -> list[IbkrHistoricalBar]: ...
    def subscribe_bars(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrBarStreamEvent], None],
        *,
        duration_seconds: int,
        use_rth: bool,
    ) -> int: ...
    def unsubscribe_bars(self, token: int) -> None: ...
    def request_health(self, token: int) -> dict[str, Any]: ...
    def diagnostics(self) -> dict[str, Any]: ...


def official_ibapi_available() -> bool:
    try:
        import ibapi  # noqa: F401
    except Exception:
        return False
    return True


class OfficialIbapiTransport:
    """Thin adapter around IBKR's locally installed official ibapi package."""

    def __init__(self) -> None:
        try:
            from ibapi.client import EClient
            from ibapi.contract import Contract
            from ibapi.wrapper import EWrapper
        except Exception as exc:
            raise IbkrClientUnavailableError(
                "official IBKR ibapi package is not installed"
            ) from exc

        self._Contract = Contract
        self._lock = threading.RLock()
        self._connected = threading.Event()
        self._next_request_id = 10_000
        self._thread: threading.Thread | None = None
        self._contract_rows: dict[int, list[IbkrContractIdentity]] = {}
        self._contract_events: dict[int, threading.Event] = {}
        self._historical_rows: dict[int, list[IbkrHistoricalBar]] = {}
        self._historical_events: dict[int, threading.Event] = {}
        self._bar_stream_listeners: dict[int, Callable[[IbkrBarStreamEvent], None]] = {}
        self._bar_stream_rows: dict[int, list[IbkrHistoricalBar]] = {}
        self._quote_contracts: dict[int, IbkrContractIdentity] = {}
        self._quote_listeners: dict[int, Callable[[IbkrQuoteSnapshot], None]] = {}
        self._quote_values: dict[int, dict[str, Any]] = {}
        self._market_data_types: dict[int, str] = {}
        self._request_errors: dict[int, dict[str, Any]] = {}
        self._farm_status: dict[str, str] = {}
        self._sequence = 0
        self._last_error: str | None = None
        self._error_count = 0
        self._entitlement_error_count = 0
        owner = self

        class Wrapper(EWrapper):
            def nextValidId(self, orderId):  # noqa: N802
                owner._connected.set()

            def connectionClosed(self):  # noqa: N802
                # An unexpected Gateway/socket loss invalidates every request id
                # and every field assembled from that socket. Treat it exactly
                # like an explicit disconnect so reconnect cannot inherit stale
                # quote state or orphaned request bookkeeping.
                owner._reset_socket_state()

            def error(self, reqId, *args):  # noqa: N802
                # IBKR API 10.33+ inserts errorTime after reqId while older
                # clients use (reqId, errorCode, errorString, rejectJson).
                if len(args) >= 4:
                    _error_time, error_code, error_string, *_rest = args
                elif len(args) >= 2:
                    error_code, error_string, *_rest = args
                else:
                    return
                owner._on_request_error(int(reqId), int(error_code), str(error_string))

            def contractDetails(self, reqId, details):  # noqa: N802
                contract = details.contract
                row = IbkrContractIdentity(
                    con_id=int(getattr(contract, "conId", 0) or 0),
                    symbol=str(getattr(contract, "symbol", "") or "").upper(),
                    local_symbol=str(getattr(contract, "localSymbol", "") or "").upper(),
                    sec_type=str(getattr(contract, "secType", "") or "").upper(),
                    currency=str(getattr(contract, "currency", "") or "").upper(),
                    exchange=str(getattr(contract, "exchange", "") or "").upper(),
                    primary_exchange=str(getattr(contract, "primaryExchange", "") or "").upper(),
                    trading_class=str(getattr(contract, "tradingClass", "") or "").upper(),
                )
                owner._contract_rows.setdefault(int(reqId), []).append(row)

            def contractDetailsEnd(self, reqId):  # noqa: N802
                event = owner._contract_events.get(int(reqId))
                if event is not None:
                    event.set()

            def marketDataType(self, reqId, marketDataType):  # noqa: N802
                owner._market_data_types[int(reqId)] = IBKR_MARKET_DATA_TYPES.get(
                    int(marketDataType), "UNKNOWN"
                )
                owner._emit_quote(int(reqId))

            def tickPrice(self, reqId, tickType, price, attrib):  # noqa: N802
                mapping = {1: "bid", 2: "ask", 4: "last", 6: "high", 7: "low"}
                field_name = mapping.get(int(tickType))
                if field_name is None:
                    return
                now = datetime.now(timezone.utc)
                values = owner._quote_values.setdefault(int(reqId), {})
                valid = price is not None and float(price) > 0
                values[field_name] = Decimal(str(price)) if valid else None
                if field_name in {"bid", "ask", "last"}:
                    values[f"{field_name}_observed_at"] = now
                if not valid and field_name == "bid":
                    values["bid_size"] = None
                if not valid and field_name == "ask":
                    values["ask_size"] = None
                if not valid and field_name == "last":
                    values["last_size"] = None
                owner._emit_quote(int(reqId))

            def tickSize(self, reqId, tickType, size):  # noqa: N802
                mapping = {0: "bid_size", 3: "ask_size", 5: "last_size", 8: "cumulative_volume"}
                field_name = mapping.get(int(tickType))
                if field_name and size is not None:
                    owner._quote_values.setdefault(int(reqId), {})[field_name] = Decimal(str(size))
                    owner._emit_quote(int(reqId))

            def tickString(self, reqId, tickType, value):  # noqa: N802
                if int(tickType) == 45:
                    try:
                        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
                    except Exception:
                        logger.debug("suppressed error in %s", "OfficialIbapiTransport.__init__.Wrapper.tickString", exc_info=True)
                        return
                    owner._quote_values.setdefault(int(reqId), {})["last_trade_at"] = parsed
                    owner._emit_quote(int(reqId))

            def historicalData(self, reqId, bar):  # noqa: N802
                row = _historical_bar(bar)
                if row is None:
                    return
                stream_rows = owner._bar_stream_rows.get(int(reqId))
                if stream_rows is not None:
                    stream_rows.append(row)
                    return
                owner._historical_rows.setdefault(int(reqId), []).append(row)

            def historicalDataEnd(self, reqId, start, end):  # noqa: N802
                stream_rows = owner._bar_stream_rows.pop(int(reqId), None)
                if stream_rows is not None:
                    owner._emit_bar_stream(int(reqId), IbkrBarStreamEvent("history", tuple(stream_rows)))
                    return
                event = owner._historical_events.get(int(reqId))
                if event is not None:
                    event.set()

            def historicalDataUpdate(self, reqId, bar):  # noqa: N802
                row = _historical_bar(bar)
                if row is not None:
                    owner._emit_bar_stream(int(reqId), IbkrBarStreamEvent("update", (row,)))

        self._wrapper = Wrapper()
        self._client = EClient(self._wrapper)

    def _reset_socket_state(self) -> None:
        """Invalidate all state whose authority is scoped to one IBKR socket."""

        with self._lock:
            self._connected.clear()
            # Wake callers blocked on requests that can no longer complete.
            for event in tuple(self._contract_events.values()):
                event.set()
            for event in tuple(self._historical_events.values()):
                event.set()
            # A live bar stream cannot outlive its socket: tell each consumer so
            # it can resubscribe after the reconnect.
            bar_streams = tuple(self._bar_stream_listeners.values())
            self._bar_stream_listeners.clear()
            self._bar_stream_rows.clear()
            for listener in bar_streams:
                self._deliver_bar_stream(
                    listener, IbkrBarStreamEvent("closed", reason="ibkr_gateway_disconnected")
                )

            self._contract_rows.clear()
            self._contract_events.clear()
            self._historical_rows.clear()
            self._historical_events.clear()
            self._quote_contracts.clear()
            self._quote_listeners.clear()
            self._quote_values.clear()
            self._market_data_types.clear()
            self._request_errors.clear()
            self._farm_status.clear()

    def _request_id(self) -> int:
        with self._lock:
            self._next_request_id += 1
            return self._next_request_id

    def _on_request_error(self, request_id: int, code: int, message: str) -> None:
        self._error_count += 1
        self._last_error = f"{request_id}:{code}:{message}"

        entitlement_denied = code in {354, 10089, 10167, 10168}
        if entitlement_denied:
            self._entitlement_error_count += 1
        if request_id >= 0:
            self._request_errors[request_id] = {
                "code": code,
                "message": message,
                "entitlement_denied": entitlement_denied,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            # A request error (codes below 2000; 2000+ are notices) ends a
            # historical request: wake its waiter now instead of letting it hold
            # the historical lock until the timeout.
            historical = self._historical_events.get(request_id)
            if historical is not None and code < 2000:
                historical.set()
            if code < 2000 or code >= 10_000:
                # A live bar stream ends on the same errors, and on the 10000+
                # ones (no subscription, competing session, live updates lost).
                listener = self._bar_stream_listeners.pop(request_id, None)
                self._bar_stream_rows.pop(request_id, None)
                if listener is not None:
                    # "No data": no trades in the opening window (a weekend or holiday), not a failure.
                    no_data = code == 162 and "no data" in message.lower()
                    reason = f"{BAR_STREAM_NO_DATA if no_data else 'ibkr_historical_error'}:{code}:{message}"
                    self._deliver_bar_stream(listener, IbkrBarStreamEvent("closed", reason=reason))

        farm_names = {
            2103: ("market_data", "DISCONNECTED"),
            2104: ("market_data", "READY"),
            2105: ("historical_data", "DISCONNECTED"),
            2106: ("historical_data", "READY"),
            2157: ("sec_def", "DISCONNECTED"),
            2158: ("sec_def", "READY"),
        }
        farm = farm_names.get(code)
        if farm is not None:
            self._farm_status[farm[0]] = farm[1]

    def _emit_quote(self, req_id: int) -> None:
        contract = self._quote_contracts.get(req_id)
        listener = self._quote_listeners.get(req_id)
        values = self._quote_values.get(req_id, {})
        if contract is None or listener is None:
            return
        self._sequence += 1
        now = datetime.now(timezone.utc)
        bid_observed = values.get("bid_observed_at")
        ask_observed = values.get("ask_observed_at")
        last_observed = values.get("last_observed_at")
        # source_time represents the oldest component needed for a valid BBO,
        # making generic freshness checks conservative rather than using the
        # unrelated last-trade timestamp.
        if isinstance(bid_observed, datetime) and isinstance(ask_observed, datetime):
            source_time = min(bid_observed, ask_observed)
        else:
            observed_times = [
                value
                for value in (bid_observed, ask_observed, last_observed)
                if isinstance(value, datetime)
            ]
            source_time = max(observed_times, default=now)
        snapshot = IbkrQuoteSnapshot(
            contract=contract,
            bid=values.get("bid"),
            ask=values.get("ask"),
            bid_size=values.get("bid_size"),
            ask_size=values.get("ask_size"),
            last=values.get("last"),
            last_size=values.get("last_size"),
            high=values.get("high"),
            low=values.get("low"),
            cumulative_volume=values.get("cumulative_volume"),
            bid_observed_at=bid_observed if isinstance(bid_observed, datetime) else None,
            ask_observed_at=ask_observed if isinstance(ask_observed, datetime) else None,
            last_observed_at=last_observed if isinstance(last_observed, datetime) else None,
            last_trade_at=(
                values.get("last_trade_at")
                if isinstance(values.get("last_trade_at"), datetime)
                else None
            ),
            source_time=source_time,
            received_at=now,
            market_data_type=self._market_data_types.get(req_id, "UNKNOWN"),
            provider_sequence=self._sequence,
        )
        try:
            listener(snapshot)
        except Exception:
            # A consumer failure must never kill the IBKR network thread.
            logger.debug("suppressed error in %s", "OfficialIbapiTransport._emit_quote", exc_info=True)
            return

    def _emit_bar_stream(self, req_id: int, event: IbkrBarStreamEvent) -> None:
        listener = self._bar_stream_listeners.get(req_id)
        if listener is not None:
            self._deliver_bar_stream(listener, event)

    @staticmethod
    def _deliver_bar_stream(
        listener: Callable[[IbkrBarStreamEvent], None],
        event: IbkrBarStreamEvent,
    ) -> None:
        try:
            listener(event)
        except Exception:
            # A consumer failure must never kill the IBKR network thread.
            logger.debug("suppressed error in %s", "OfficialIbapiTransport._deliver_bar_stream", exc_info=True)

    def connect(self, host: str, port: int, client_id: int, timeout_seconds: float = 8.0) -> None:
        if self.is_connected():
            return
        self._client.connect(host, int(port), clientId=int(client_id))
        self._thread = threading.Thread(
            target=self._client.run,
            name="omnix-ibkr-api",
            daemon=True,
        )
        self._thread.start()
        if not self._connected.wait(timeout_seconds):
            self.disconnect()
            raise IbkrRuntimeError("ibkr_gateway_handshake_timeout")

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._reset_socket_state()

    def is_connected(self) -> bool:
        return bool(self._client.isConnected()) and self._connected.is_set()

    def _stock_contract(self, symbol: str):
        contract = self._Contract()
        contract.symbol = symbol.upper()
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def _qualified_contract(self, identity: IbkrContractIdentity):
        contract = self._Contract()
        contract.conId = identity.con_id
        contract.symbol = identity.symbol
        contract.localSymbol = identity.local_symbol
        contract.secType = identity.sec_type
        contract.exchange = "SMART"
        contract.primaryExchange = identity.primary_exchange
        contract.currency = identity.currency
        if identity.trading_class:
            contract.tradingClass = identity.trading_class
        return contract

    def qualify_stock(self, symbol: str, *, currency: str = "USD") -> list[IbkrContractIdentity]:
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        req_id = self._request_id()
        event = threading.Event()
        self._contract_events[req_id] = event
        self._contract_rows[req_id] = []
        contract = self._stock_contract(symbol)
        contract.currency = currency.upper()
        self._client.reqContractDetails(req_id, contract)
        try:
            if not event.wait(10.0):
                raise IbkrRuntimeError("ibkr_contract_details_timeout")
            return list(self._contract_rows.get(req_id, []))
        finally:
            self._contract_rows.pop(req_id, None)
            self._contract_events.pop(req_id, None)
            self._request_errors.pop(req_id, None)

    def subscribe_quote(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrQuoteSnapshot], None],
    ) -> int:
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        req_id = self._request_id()
        self._quote_contracts[req_id] = contract
        self._quote_listeners[req_id] = listener
        self._quote_values[req_id] = {}
        self._client.reqMarketDataType(1)
        self._client.reqMktData(
            req_id,
            self._qualified_contract(contract),
            "",
            False,
            False,
            [],
        )
        return req_id

    def unsubscribe_quote(self, token: int) -> None:
        req_id = int(token)
        try:
            self._client.cancelMktData(req_id)
        finally:
            self._quote_contracts.pop(req_id, None)
            self._quote_listeners.pop(req_id, None)
            self._quote_values.pop(req_id, None)
            self._market_data_types.pop(req_id, None)
            self._request_errors.pop(req_id, None)

    def historical_bars(
        self,
        contract: IbkrContractIdentity,
        *,
        end: datetime,
        duration_seconds: int,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        timeout_seconds: float = 20.0,
    ) -> list[IbkrHistoricalBar]:
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        if end.tzinfo is None:
            raise ValueError("ibkr historical end must be timezone-aware")
        req_id = self._request_id()
        event = threading.Event()
        self._historical_events[req_id] = event
        self._historical_rows[req_id] = []
        end_text = end.astimezone(timezone.utc).strftime("%Y%m%d %H:%M:%S UTC")
        self._client.reqHistoricalData(
            req_id,
            self._qualified_contract(contract),
            end_text,
            _duration_text(duration_seconds),
            bar_size,
            what_to_show,
            1 if use_rth else 0,
            2,
            False,
            [],
        )
        try:
            if not event.wait(timeout_seconds):
                try:
                    self._client.cancelHistoricalData(req_id)
                except Exception:
                    logger.debug("suppressed error in %s", "OfficialIbapiTransport.historical_bars", exc_info=True)
                raise IbkrRuntimeError("ibkr_historical_data_timeout")
            if req_id not in self._historical_events:
                # The socket reset cleared this request: no answer, not "no data".
                raise IbkrRuntimeError("ibkr_gateway_disconnected")
            error = self._request_errors.get(req_id)
            if error is not None and error["code"] < 2000:
                if error["code"] == 162 and "returned no data" in error["message"].lower():
                    return []  # IBKR has no trades in the range: a factual empty answer
                raise IbkrRuntimeError(f"ibkr_historical_error:{error['code']}")
            return list(self._historical_rows.get(req_id, []))
        finally:
            self._historical_rows.pop(req_id, None)
            self._historical_events.pop(req_id, None)
            self._request_errors.pop(req_id, None)

    def subscribe_bars(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrBarStreamEvent], None],
        *,
        duration_seconds: int,
        use_rth: bool,
    ) -> int:
        """Request 1m TRADES bars for the last ``duration_seconds`` and keep the newest one up to date."""
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        req_id = self._request_id()
        self._bar_stream_rows[req_id] = []
        self._bar_stream_listeners[req_id] = listener
        # keepUpToDate requires an empty end time: the window ends now and stays open.
        self._client.reqHistoricalData(
            req_id,
            self._qualified_contract(contract),
            "",
            _duration_text(duration_seconds),
            "1 min",
            "TRADES",
            1 if use_rth else 0,
            2,
            True,
            [],
        )
        return req_id

    def unsubscribe_bars(self, token: int) -> None:
        req_id = int(token)
        active = self._bar_stream_listeners.pop(req_id, None) is not None
        self._bar_stream_rows.pop(req_id, None)
        self._request_errors.pop(req_id, None)
        if not active:
            return
        try:
            self._client.cancelHistoricalData(req_id)
        except Exception:
            logger.debug("suppressed error in %s", "OfficialIbapiTransport.unsubscribe_bars", exc_info=True)

    def request_health(self, token: int) -> dict[str, Any]:
        request_id = int(token)
        error = self._request_errors.get(request_id)
        return {
            "request_id": request_id,
            "market_data_type": self._market_data_types.get(request_id, "UNKNOWN"),
            "error": dict(error) if error is not None else None,
            "entitlement_denied": bool(error and error.get("entitlement_denied")),
        }

    def diagnostics(self) -> dict[str, Any]:
        recent_errors = {
            str(key): dict(value)
            for key, value in sorted(self._request_errors.items())[-20:]
        }
        return {
            "transport": "official_ibapi",
            "connected": self.is_connected(),
            "active_quote_subscriptions": len(self._quote_listeners),
            "active_bar_streams": len(self._bar_stream_listeners),
            "last_error": self._last_error,
            "error_count": self._error_count,
            "entitlement_error_count": self._entitlement_error_count,
            "farm_status": dict(self._farm_status),
            "recent_request_errors": recent_errors,
        }


class FakeIbkrTransport:
    """Deterministic transport for provider/runtime unit tests."""

    def __init__(
        self,
        *,
        contracts: dict[str, list[IbkrContractIdentity]] | None = None,
        history: dict[int, list[IbkrHistoricalBar]] | None = None,
    ) -> None:
        self.contracts = contracts or {}
        self.history = history or {}
        self.connected = False
        self.listeners: dict[int, tuple[IbkrContractIdentity, Callable[[IbkrQuoteSnapshot], None]]] = {}
        self.bar_streams: dict[int, tuple[IbkrContractIdentity, Callable[[IbkrBarStreamEvent], None]]] = {}
        self.bar_stream_requests: list[dict[str, Any]] = []
        self.request_health_by_token: dict[int, dict[str, Any]] = {}
        self._next_token = 1

    def connect(self, host: str, port: int, client_id: int, timeout_seconds: float = 8.0) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False
        self.listeners.clear()
        bar_streams = tuple(listener for _, listener in self.bar_streams.values())
        self.bar_streams.clear()
        for listener in bar_streams:
            listener(IbkrBarStreamEvent("closed", reason="ibkr_gateway_disconnected"))

    def is_connected(self) -> bool:
        return self.connected

    def qualify_stock(self, symbol: str, *, currency: str = "USD") -> list[IbkrContractIdentity]:
        return list(self.contracts.get(symbol.upper(), ()))

    def subscribe_quote(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrQuoteSnapshot], None],
    ) -> int:
        token = self._next_token
        self._next_token += 1
        self.listeners[token] = (contract, listener)
        return token

    def unsubscribe_quote(self, token: int) -> None:
        self.listeners.pop(int(token), None)

    def emit(self, token: int, snapshot: IbkrQuoteSnapshot) -> None:
        _, listener = self.listeners[int(token)]
        listener(snapshot)

    def historical_bars(
        self,
        contract: IbkrContractIdentity,
        *,
        end: datetime,
        duration_seconds: int,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        timeout_seconds: float = 20.0,
    ) -> list[IbkrHistoricalBar]:
        start = end.astimezone(timezone.utc).timestamp() - max(1, int(duration_seconds))
        return [
            row
            for row in self.history.get(contract.con_id, ())
            if start <= row.start_time.astimezone(timezone.utc).timestamp() < end.astimezone(timezone.utc).timestamp()
        ]

    def subscribe_bars(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrBarStreamEvent], None],
        *,
        duration_seconds: int,
        use_rth: bool,
    ) -> int:
        token = self._next_token
        self._next_token += 1
        self.bar_streams[token] = (contract, listener)
        self.bar_stream_requests.append(
            {"token": token, "con_id": contract.con_id, "duration_seconds": duration_seconds, "use_rth": use_rth}
        )
        return token

    def unsubscribe_bars(self, token: int) -> None:
        self.bar_streams.pop(int(token), None)

    def emit_bars(self, token: int, event: IbkrBarStreamEvent) -> None:
        _, listener = self.bar_streams[int(token)]
        if event.kind == "closed":
            self.bar_streams.pop(int(token), None)
        listener(event)

    def request_health(self, token: int) -> dict[str, Any]:
        return dict(
            self.request_health_by_token.get(
                int(token),
                {
                    "request_id": int(token),
                    "market_data_type": "UNKNOWN",
                    "error": None,
                    "entitlement_denied": False,
                },
            )
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "transport": "fake",
            "connected": self.connected,
            "active_quote_subscriptions": len(self.listeners),
            "active_bar_streams": len(self.bar_streams),
            "request_health": {
                str(key): dict(value)
                for key, value in self.request_health_by_token.items()
            },
        }


def _bool_env(name: str, default: str = "0") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


class IbkrRuntime:
    """Connection, contract identity, subscription and diagnostics authority."""

    def __init__(
        self,
        *,
        transport: IbkrTransport | None = None,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
        enabled: bool | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._settings_managed = (
            transport is None
            and host is None
            and port is None
            and client_id is None
            and enabled is None
        )
        resolved_settings, settings_source = load_ibkr_settings() if self._settings_managed else (
            IbkrSettings(
                enabled=_bool_env("OMNIX_IBKR_ENABLED", "0") if enabled is None else bool(enabled),
                monitor_enabled=_bool_env("OMNIX_IBKR_MONITOR", "1"),
                host=host or _env_str("OMNIX_IBKR_HOST", "127.0.0.1"),
                port=int(port or _env_str("OMNIX_IBKR_PORT", "4002")),
                client_id=int(client_id or _env_str("OMNIX_IBKR_CLIENT_ID", "71")),
                live_authority_enabled=_bool_env("OMNIX_IBKR_LIVE_AUTHORITY", "0"),
                recovery_authority_enabled=_bool_env("OMNIX_IBKR_RECOVERY_AUTHORITY", "0"),
            ),
            "runtime_arguments",
        )
        self._settings_source = settings_source
        self.connection = resolved_settings.connection
        self.host = resolved_settings.host
        self.port = resolved_settings.port
        self.client_id = resolved_settings.client_id
        self.enabled = resolved_settings.enabled
        self._live_authority_enabled = resolved_settings.live_authority_enabled
        self._recovery_authority_enabled = resolved_settings.recovery_authority_enabled
        self.transport = transport
        # A transport the caller passed in (tests, embedding) is usable by definition.
        self._transport_injected = transport is not None
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._historical_lock = threading.Lock()
        self._contract_cache: dict[str, IbkrContractIdentity] = {}
        self._quote_tokens: dict[str, int] = {}
        self._latest_quotes: dict[str, IbkrQuoteSnapshot] = {}
        # Notified on every quote, so waiting for one does not poll.
        self._quote_arrived = threading.Condition()
        self._quote_listeners: dict[str, list[Callable[[IbkrQuoteSnapshot], None]]] = {}
        self._bar_stream_tokens: set[int] = set()
        self.connect_count = 0
        self.connect_failure_count = 0
        self.reconnect_count = 0
        self.contract_failure_count = 0
        self.historical_request_count = 0
        self.historical_cache_hit_count = 0
        # key -> (rows, expires_at); expires_at None for a final range.
        self._historical_cache: OrderedDict[
            tuple[Any, ...], tuple[tuple[IbkrHistoricalBar, ...], datetime | None]
        ] = OrderedDict()
        self._historical_cache_lock = threading.Lock()
        self._next_connect_attempt_monotonic = 0.0
        self._last_historical_request_monotonic = 0.0
        self.subscription_count = 0
        self.last_error: str | None = None
        self.last_connected_at: datetime | None = None

    def refresh_settings(self) -> None:
        """Apply saved Omnix IBKR settings without requiring a process restart."""

        if not self._settings_managed:
            return
        settings, source = load_ibkr_settings()
        changed = (
            self.host != settings.host
            or self.port != settings.port
            or self.client_id != settings.client_id
            or self.enabled != settings.enabled
            or self.connection != settings.connection
        )
        if changed and self.is_connected():
            self.disconnect()
        if self.connection != settings.connection and self.transport is not None:
            # The other connection needs its own transport.
            self.disconnect()
            self.transport = None
        self.connection = settings.connection
        self.host = settings.host
        self.port = settings.port
        self.client_id = settings.client_id
        self.enabled = settings.enabled
        self._live_authority_enabled = settings.live_authority_enabled
        self._recovery_authority_enabled = settings.recovery_authority_enabled
        self._settings_source = source

    @property
    def live_authority_enabled(self) -> bool:
        if self._settings_managed:
            return self._live_authority_enabled
        return _bool_env("OMNIX_IBKR_LIVE_AUTHORITY", "0")

    @property
    def recovery_authority_enabled(self) -> bool:
        if self._settings_managed:
            return self._recovery_authority_enabled
        return _bool_env("OMNIX_IBKR_RECOVERY_AUTHORITY", "0")

    def _ensure_transport(self) -> IbkrTransport:
        if self.transport is not None:
            return self.transport
        if self.connection == "web_api":
            from .ibkr_web_api import IbkrWebApiTransport

            self.transport = IbkrWebApiTransport()
        else:
            self.transport = OfficialIbapiTransport()
        return self.transport

    def client_available(self) -> bool:
        """Whether the chosen connection can be tried: the ibapi client, or saved Web API credentials."""
        if self._transport_injected:
            return True
        # A transport the runtime built exists after any attempt, so it proves nothing.
        if self.connection == "web_api":
            from .ibkr_web_api import web_api_credentials_configured

            return web_api_credentials_configured()
        return official_ibapi_available()

    def connect(self, *, force: bool = False) -> None:
        """Connect if needed; ``force`` (a user's explicit attempt) skips the retry backoff."""
        self.refresh_settings()
        if not self.enabled:
            raise IbkrRuntimeError("ibkr_provider_disabled")
        with self._connect_lock:
            transport = self._ensure_transport()
            if transport.is_connected():
                return
            now_mono = time_module.monotonic()
            if not force and now_mono < self._next_connect_attempt_monotonic:
                raise IbkrRuntimeError("ibkr_reconnect_backoff")
            prior = self.connect_count > 0
            if prior:
                # The prior socket is already known dead here. Invalidate any
                # consumer-facing quote cache before attempting the reconnect,
                # so even a failed reconnect cannot expose a pre-disconnect quote.
                with self._lock:
                    self._quote_tokens.clear()
                    self._latest_quotes.clear()
                    self._bar_stream_tokens.clear()
                    self.subscription_count = 0
            try:
                transport.connect(self.host, self.port, self.client_id)
            except Exception as exc:
                self.connect_failure_count += 1
                backoff = max(
                    # A Web API attempt is a signed request to IBKR's servers, not a local socket.
                    60.0 if self.connection == "web_api" else 0.25,
                    float(_env_str("OMNIX_IBKR_RECONNECT_BACKOFF_SECONDS", "1")),
                )
                self._next_connect_attempt_monotonic = time_module.monotonic() + backoff
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise
            self._next_connect_attempt_monotonic = 0.0
            self.connect_count += 1
            if prior:
                self.reconnect_count += 1
                # Runtime listeners are consumer-owned and intentionally survive;
                # upstream request IDs and cached quotes do not.
            self.last_connected_at = datetime.now(timezone.utc)
            self.last_error = None

    def disconnect(self) -> None:
        if self.transport is not None:
            self.transport.disconnect()
        with self._lock:
            self._quote_tokens.clear()
            self._latest_quotes.clear()
            self._bar_stream_tokens.clear()
            self.subscription_count = 0

    def is_connected(self) -> bool:
        return bool(self.transport is not None and self.transport.is_connected())

    @staticmethod
    def _venue_aliases(venue: str) -> set[str]:
        normalized = venue.upper()
        aliases = {
            "NASDAQ": {"NASDAQ", "ISLAND", "NASDAQOM"},
            "NYSE": {"NYSE"},
            "ARCA": {"ARCA"},
            "AMEX": {"AMEX", "NYSEMKT"},
        }
        return aliases.get(normalized, {normalized})

    def qualify_contract(
        self,
        instrument_id: str,
        *,
        symbol: str,
        venue: str,
        currency: str = "USD",
    ) -> IbkrContractIdentity:
        with self._lock:
            cached = self._contract_cache.get(instrument_id)
            if cached is not None:
                return cached
        self.connect()
        assert self.transport is not None
        rows = [
            row
            for row in self.transport.qualify_stock(symbol, currency=currency)
            if row.sec_type == "STK"
            and row.currency == currency.upper()
            and row.symbol == symbol.upper()
            and row.con_id > 0
        ]
        if not rows:
            self.contract_failure_count += 1
            raise IbkrContractUnavailableError(f"IBKR_CONTRACT_UNAVAILABLE:{symbol}")
        if len(rows) > 1:
            aliases = self._venue_aliases(venue)
            narrowed = [
                row
                for row in rows
                if row.primary_exchange in aliases or row.exchange in aliases
            ]
            if len(narrowed) == 1:
                rows = narrowed
            else:
                self.contract_failure_count += 1
                raise IbkrContractAmbiguousError(
                    f"IBKR_CONTRACT_AMBIGUOUS:{symbol}:{len(rows)}"
                )
        selected = rows[0]
        with self._lock:
            self._contract_cache[instrument_id] = selected
        return selected

    def subscribe_quote(
        self,
        instrument_id: str,
        *,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrQuoteSnapshot], None] | None = None,
    ) -> int:
        self.connect()
        assert self.transport is not None
        with self._lock:
            if listener is not None:
                listeners = self._quote_listeners.setdefault(instrument_id, [])
                if not any(item is listener for item in listeners):
                    listeners.append(listener)
            existing = self._quote_tokens.get(instrument_id)
            if existing is not None:
                return existing

        def on_quote(snapshot: IbkrQuoteSnapshot) -> None:
            with self._lock:
                self._latest_quotes[instrument_id] = snapshot
                listeners = tuple(self._quote_listeners.get(instrument_id, ()))
            with self._quote_arrived:
                self._quote_arrived.notify_all()
            for callback in listeners:
                try:
                    callback(snapshot)
                except Exception:
                    logger.debug("suppressed error in %s", "IbkrRuntime.subscribe_quote.on_quote", exc_info=True)
                    continue

        token = self.transport.subscribe_quote(contract, on_quote)
        with self._lock:
            self._quote_tokens[instrument_id] = token
            self.subscription_count = len(self._quote_tokens)
        return token

    def unsubscribe_quote(
        self,
        instrument_id: str,
        *,
        listener: Callable[[IbkrQuoteSnapshot], None] | None = None,
    ) -> None:
        with self._lock:
            if listener is not None:
                rows = self._quote_listeners.get(instrument_id, [])
                self._quote_listeners[instrument_id] = [item for item in rows if item is not listener]
                if self._quote_listeners[instrument_id]:
                    return
            token = self._quote_tokens.pop(instrument_id, None)
            self._quote_listeners.pop(instrument_id, None)
            self.subscription_count = len(self._quote_tokens)
        if token is not None and self.transport is not None:
            self.transport.unsubscribe_quote(token)

    def latest_quote(self, instrument_id: str) -> IbkrQuoteSnapshot | None:
        # Never surface a cached socket observation while the transport is down.
        if not self.is_connected():
            return None
        with self._lock:
            return self._latest_quotes.get(instrument_id)

    def subscription_health(self, instrument_id: str) -> dict[str, Any]:
        with self._lock:
            token = self._quote_tokens.get(instrument_id)
        if token is None or self.transport is None:
            return {
                "request_id": None,
                "market_data_type": "UNKNOWN",
                "error": None,
                "entitlement_denied": False,
            }
        try:
            return self.transport.request_health(token)
        except Exception as exc:
            return {
                "request_id": token,
                "market_data_type": "UNKNOWN",
                "error": {"message": f"{type(exc).__name__}: {exc}"},
                "entitlement_denied": False,
            }

    def wait_for_quote(self, instrument_id: str, timeout_seconds: float = 3.0) -> IbkrQuoteSnapshot | None:
        """The latest quote with a last price, waiting up to the timeout for one to arrive."""
        deadline = time_module.monotonic() + max(0.0, timeout_seconds)
        with self._quote_arrived:
            while True:
                snapshot = self.latest_quote(instrument_id)
                if snapshot is not None and snapshot.last is not None:
                    return snapshot
                remaining = deadline - time_module.monotonic()
                if remaining <= 0:
                    return snapshot
                self._quote_arrived.wait(remaining)

    def historical_bars(
        self,
        contract: IbkrContractIdentity,
        *,
        start: datetime,
        end: datetime,
        bar_size: str = "1 min",
        what_to_show: str = "TRADES",
        use_rth: bool = False,
    ) -> list[IbkrHistoricalBar]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("ibkr historical range must be timezone-aware")
        if end <= start:
            raise ValueError("ibkr historical end must follow start")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        key = (contract.con_id, start_utc, end_utc, bar_size, what_to_show, bool(use_rth))
        with self._historical_cache_lock:
            cached = self._historical_cache.get(key)
            if cached is not None and (cached[1] is None or self._clock() < cached[1]):
                self._historical_cache.move_to_end(key)
                self.historical_cache_hit_count += 1
                return list(cached[0])
        self.connect()
        assert self.transport is not None
        duration = int((end_utc - start_utc).total_seconds())
        min_interval = self._historical_min_interval_seconds()
        with self._historical_lock:
            # Concurrent tape warmers miss the cache together; whichever waited
            # on this lock reuses the answer the first one stored.
            with self._historical_cache_lock:
                cached = self._historical_cache.get(key)
                if cached is not None and (cached[1] is None or self._clock() < cached[1]):
                    self._historical_cache.move_to_end(key)
                    self.historical_cache_hit_count += 1
                    return list(cached[0])
            elapsed = time_module.monotonic() - self._last_historical_request_monotonic
            if elapsed < min_interval:
                time_module.sleep(min_interval - elapsed)
            rows = self.transport.historical_bars(
                contract,
                end=end.astimezone(timezone.utc),
                duration_seconds=max(60, duration),
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
            )
            self._last_historical_request_monotonic = time_module.monotonic()
            self.historical_request_count += 1
        result = [row for row in rows if start_utc <= row.start_time < end_utc]
        now = self._clock()
        expires_at = None if end_utc <= now - HISTORICAL_CACHE_SETTLE else now + HISTORICAL_CACHE_RECENT_TTL
        with self._historical_cache_lock:
            self._historical_cache[key] = (tuple(result), expires_at)
            self._historical_cache.move_to_end(key)
            while len(self._historical_cache) > HISTORICAL_CACHE_MAX_ENTRIES:
                self._historical_cache.popitem(last=False)
        return result

    @staticmethod
    def _historical_min_interval_seconds() -> float:
        if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
            return 0.0
        return max(0.0, float(_env_str("OMNIX_IBKR_HISTORICAL_MIN_INTERVAL_SECONDS", "0.25")))

    def subscribe_bars(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrBarStreamEvent], None],
        *,
        duration_seconds: int,
        use_rth: bool = False,
    ) -> int:
        """Open a live 1m TRADES bar stream; ``listener`` runs on the IBKR network thread."""
        self.connect()
        assert self.transport is not None
        min_interval = self._historical_min_interval_seconds()
        # The stream opens with a historical request, so it shares their pacing.
        with self._historical_lock:
            elapsed = time_module.monotonic() - self._last_historical_request_monotonic
            if elapsed < min_interval:
                time_module.sleep(min_interval - elapsed)
            token = self.transport.subscribe_bars(
                contract,
                listener,
                duration_seconds=duration_seconds,
                use_rth=use_rth,
            )
            self._last_historical_request_monotonic = time_module.monotonic()
        with self._lock:
            self._bar_stream_tokens.add(token)
        return token

    def unsubscribe_bars(self, token: int) -> None:
        with self._lock:
            self._bar_stream_tokens.discard(token)
        if self.transport is not None:
            self.transport.unsubscribe_bars(token)

    def diagnostics(self) -> dict[str, Any]:
        self.refresh_settings()
        transport_diagnostics = self.transport.diagnostics() if self.transport is not None else {}
        settings, settings_source = load_ibkr_settings() if self._settings_managed else (
            IbkrSettings(
                enabled=self.enabled,
                monitor_enabled=_bool_env("OMNIX_IBKR_MONITOR", "1"),
                host=self.host,
                port=self.port,
                client_id=self.client_id,
                live_authority_enabled=self.live_authority_enabled,
                recovery_authority_enabled=self.recovery_authority_enabled,
            ),
            self._settings_source,
        )
        return {
            "enabled": self.enabled,
            "monitor_enabled": settings.monitor_enabled,
            "settings_source": settings_source,
            "settings_managed": self._settings_managed,
            "connection": self.connection,
            "client_available": self.client_available(),
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "official_ibapi_available": official_ibapi_available(),
            "connected": self.is_connected(),
            "live_authority_enabled": self.live_authority_enabled,
            "recovery_authority_enabled": self.recovery_authority_enabled,
            "connect_count": self.connect_count,
            "connect_failure_count": self.connect_failure_count,
            "reconnect_count": self.reconnect_count,
            "qualified_contract_count": len(self._contract_cache),
            "active_quote_subscriptions": len(self._quote_tokens),
            "active_bar_streams": len(self._bar_stream_tokens),
            "contract_failure_count": self.contract_failure_count,
            "historical_request_count": self.historical_request_count,
            "historical_cache_hit_count": self.historical_cache_hit_count,
            "historical_cache_entries": len(self._historical_cache),
            "last_error": self.last_error,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "transport": transport_diagnostics,
        }


_DEFAULT_RUNTIME: IbkrRuntime | None = None
_DEFAULT_LOCK = threading.Lock()


def default_ibkr_runtime() -> IbkrRuntime:
    global _DEFAULT_RUNTIME
    if _DEFAULT_RUNTIME is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_RUNTIME is None:
                _DEFAULT_RUNTIME = IbkrRuntime()
    return _DEFAULT_RUNTIME


__all__ = [
    "BAR_STREAM_NO_DATA",
    "FakeIbkrTransport",
    "IbkrBarStreamEvent",
    "IbkrClientUnavailableError",
    "IbkrContractAmbiguousError",
    "IbkrContractIdentity",
    "IbkrContractUnavailableError",
    "IbkrHistoricalBar",
    "IbkrQuoteSnapshot",
    "IbkrRuntime",
    "IbkrRuntimeError",
    "IbkrTransport",
    "OfficialIbapiTransport",
    "default_ibkr_runtime",
    "official_ibapi_available",
]
