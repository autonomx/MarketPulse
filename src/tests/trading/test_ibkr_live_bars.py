"""Live IBKR chart bars: the keepUpToDate stream, shaped like the chart's IBKR history."""
from __future__ import annotations

import asyncio
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading.catalog import bindings_for_instrument, instrument_by_id
from app.apps.trading.providers.ibkr import IbkrEquityProvider
from app.apps.trading.providers.ibkr_runtime import (
    BAR_STREAM_NO_DATA,
    FakeIbkrTransport,
    IbkrBarStreamEvent,
    IbkrContractIdentity,
    IbkrHistoricalBar,
    IbkrRuntime,
    OfficialIbapiTransport,
)
from app.apps.trading.providers.registry import ProviderRegistry
from app.apps.trading.service import TradingMarketDataService
from app.apps.trading.streaming.ibkr_stream import (
    NO_DATA_RETRY_SECONDS,
    IbkrBarStream,
    IbkrLiveBars,
    ibkr_streams_interval,
)

INSTRUMENT = "equity:NASDAQ:AAPL"
# Thursday 2026-09-17, 09:30 ET.
OPEN = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def _contract() -> IbkrContractIdentity:
    return IbkrContractIdentity(
        con_id=265598,
        symbol="AAPL",
        local_symbol="AAPL",
        sec_type="STK",
        currency="USD",
        exchange="SMART",
        primary_exchange="NASDAQ",
        trading_class="NMS",
    )


def _ibkr_binding_id() -> str:
    return next(item.binding_id for item in bindings_for_instrument(INSTRUMENT) if item.provider == "ibkr")


def _row(start: datetime, close: str, *, volume: str = "100") -> IbkrHistoricalBar:
    value = Decimal(close)
    return IbkrHistoricalBar(
        start_time=start,
        open=value - Decimal("0.10"),
        high=value + Decimal("0.20"),
        low=value - Decimal("0.30"),
        close=value,
        volume=Decimal(volume),
    )


def _live(interval: str, now: list[datetime], *, extended_hours: bool = True) -> IbkrLiveBars:
    instrument = instrument_by_id(INSTRUMENT)
    assert instrument is not None
    return IbkrLiveBars(
        instrument=instrument,
        binding_id=_ibkr_binding_id(),
        contract=_contract(),
        interval=interval,
        extended_hours=extended_hours,
        clock=lambda: now[0],
    )


def test_streamable_intervals_are_whole_minutes_shorter_than_a_day() -> None:
    assert all(ibkr_streams_interval(interval) for interval in ("1m", "3m", "5m", "15m", "30m", "1h", "4h"))
    assert not any(ibkr_streams_interval(interval) for interval in ("1d", "1w", "1mo", "30s", "bogus"))


def test_1m_stream_sends_the_forming_bar_and_closes_it_when_the_minute_turns() -> None:
    now = [OPEN + 2 * MINUTE + timedelta(seconds=30)]
    live = _live("1m", now)

    history = live.apply(
        IbkrBarStreamEvent("history", (_row(OPEN, "100"), _row(OPEN + MINUTE, "101"), _row(OPEN + 2 * MINUTE, "102")))
    )
    # The chart already holds older history; only the forming minute is sent.
    assert [(item.start_time, item.close, item.is_final) for item in history] == [
        (OPEN + 2 * MINUTE, Decimal("102"), False)
    ]
    first = history[0]
    assert first.interval == "1m"
    assert first.session == "regular"
    assert first.extended_hours is True
    # The same event id as the chart's IBKR history for that minute.
    assert first.provider_event_id == f"ibkr:265598:{(OPEN + 2 * MINUTE).isoformat()}"

    revised = live.apply(IbkrBarStreamEvent("update", (_row(OPEN + 2 * MINUTE, "102.5", volume="150"),)))
    assert [(item.close, item.volume, item.is_final) for item in revised] == [(Decimal("102.5"), Decimal("150"), False)]
    assert revised[0].ingestion_revision > first.ingestion_revision

    # A late revision of a closed minute and a malformed bar change nothing.
    assert live.apply(IbkrBarStreamEvent("update", (_row(OPEN + MINUTE, "90"),))) == []
    broken = IbkrHistoricalBar(
        start_time=OPEN + 3 * MINUTE,
        open=Decimal("-1"),
        high=Decimal("-1"),
        low=Decimal("-1"),
        close=Decimal("-1"),
        volume=Decimal("-1"),
    )
    assert live.apply(IbkrBarStreamEvent("update", (broken,))) == []

    now[0] = OPEN + 3 * MINUTE + timedelta(seconds=5)
    rolled = live.apply(IbkrBarStreamEvent("update", (_row(OPEN + 3 * MINUTE, "103"),)))
    assert [(item.start_time, item.close, item.is_final) for item in rolled] == [
        (OPEN + 2 * MINUTE, Decimal("102.5"), True),
        (OPEN + 3 * MINUTE, Decimal("103"), False),
    ]


def _chart_history_registry(rows: list[IbkrHistoricalBar], now: datetime) -> ProviderRegistry:
    transport = FakeIbkrTransport(contracts={"AAPL": [_contract()]}, history={265598: rows})
    provider = IbkrEquityProvider(runtime=IbkrRuntime(transport=transport, enabled=True), clock=lambda: now)
    return ProviderRegistry(factories={"ibkr": lambda: provider})


@pytest.mark.parametrize("extended_hours", [True, False])
def test_derived_live_bars_match_the_charts_clock_aligned_ibkr_history(monkeypatch, extended_hours) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    # 09:20 to 09:46 ET: pre-market into the regular session, with a quiet minute at 09:33.
    starts = [OPEN - 10 * MINUTE + offset * MINUTE for offset in range(27) if offset != 13]
    rows = [_row(start, str(100 + index), volume=str(10 + index)) for index, start in enumerate(starts)]
    now = [rows[0].start_time + timedelta(seconds=30)]
    live = _live("5m", now, extended_hours=extended_hours)

    emitted = {}
    for item in live.apply(IbkrBarStreamEvent("history", (rows[0],))):
        emitted[item.start_time] = item
    for row in rows[1:]:
        now[0] = row.start_time + timedelta(seconds=30)
        for item in live.apply(IbkrBarStreamEvent("update", (row,))):
            emitted[item.start_time] = item

    history = _chart_history_registry(rows, now[0]).bars(
        INSTRUMENT,
        "5m",
        20,
        _ibkr_binding_id(),
        alignment="clock",
        include_extended_hours=extended_hours,
    )
    expected = [bar for bar in history.bars if bar.is_final]
    assert expected, "the chart history should hold closed 5m buckets"
    if not extended_hours:
        assert all(bar.session == "regular" for bar in expected)
    for bar in expected:
        live_bar = emitted[bar.start_time]
        assert live_bar.is_final
        assert (live_bar.end_time, live_bar.open, live_bar.high, live_bar.low, live_bar.close, live_bar.volume) == (
            bar.end_time,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
        )
        assert live_bar.session == bar.session
        assert live_bar.provider_event_id == bar.provider_event_id
    # No bucket the chart history leaves out (pre-market without extended hours) is streamed.
    assert {start for start, item in emitted.items() if item.is_final} == {bar.start_time for bar in expected}
    forming = max(emitted.values(), key=lambda item: item.start_time)
    assert forming.start_time == OPEN + 15 * MINUTE
    assert not forming.is_final


def test_ibkr_bar_stream_bridges_gateway_events_and_releases_the_request(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    transport = FakeIbkrTransport(contracts={"AAPL": [_contract()]})
    provider = IbkrEquityProvider(runtime=IbkrRuntime(transport=transport, enabled=True))
    now = [OPEN + timedelta(seconds=20)]
    stream = IbkrBarStream(lambda: provider, clock=lambda: now[0])

    async def scenario():
        updates = stream.messages(
            provider_symbol="AAPL",
            binding_id=_ibkr_binding_id(),
            instrument_id=INSTRUMENT,
            interval="1m",
        )
        first = asyncio.ensure_future(anext(updates))
        while not transport.bar_streams:
            await asyncio.sleep(0.01)
        (token,) = transport.bar_streams
        # Gateway callbacks arrive on the IBKR network thread.
        sender = threading.Thread(
            target=transport.emit_bars,
            args=(token, IbkrBarStreamEvent("history", (_row(OPEN, "100"),))),
        )
        sender.start()
        update = await first
        sender.join()
        transport.emit_bars(token, IbkrBarStreamEvent("closed", reason="ibkr_gateway_disconnected"))
        with pytest.raises(ConnectionError, match="ibkr_gateway_disconnected"):
            await anext(updates)
        return update

    update = asyncio.run(scenario())

    assert (update.start_time, update.close, update.is_final) == (OPEN, Decimal("100"), False)
    # One extended-hours request whose history covers the bucket of any intraday interval.
    assert [(item["con_id"], item["use_rth"], item["duration_seconds"]) for item in transport.bar_stream_requests] == [
        (265598, False, 86_400)
    ]
    assert transport.bar_streams == {}
    assert provider.runtime.diagnostics()["active_bar_streams"] == 0


def test_runtime_bar_stream_tokens_follow_subscriptions_and_disconnects(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    transport = FakeIbkrTransport(contracts={"AAPL": [_contract()]})
    runtime = IbkrRuntime(transport=transport, enabled=True)
    events: list[IbkrBarStreamEvent] = []

    token = runtime.subscribe_bars(_contract(), events.append, duration_seconds=3_600)
    assert runtime.diagnostics()["active_bar_streams"] == 1
    runtime.unsubscribe_bars(token)
    assert runtime.diagnostics()["active_bar_streams"] == 0
    assert transport.bar_streams == {}

    runtime.subscribe_bars(_contract(), events.append, duration_seconds=3_600)
    runtime.disconnect()
    assert [(event.kind, event.reason) for event in events] == [("closed", "ibkr_gateway_disconnected")]
    assert runtime.diagnostics()["active_bar_streams"] == 0


def _stub_ibapi(monkeypatch) -> None:
    class EClient:
        def __init__(self, wrapper) -> None:
            self.wrapper = wrapper
            self.requests: list[tuple] = []
            self.cancelled: list[int] = []

        def isConnected(self) -> bool:  # noqa: N802
            return True

        def reqHistoricalData(self, *args) -> None:  # noqa: N802
            self.requests.append(args)

        def cancelHistoricalData(self, req_id) -> None:  # noqa: N802
            self.cancelled.append(req_id)

    class Contract:
        pass

    class EWrapper:
        pass

    package = types.ModuleType("ibapi")
    client = types.ModuleType("ibapi.client")
    client.EClient = EClient
    contract = types.ModuleType("ibapi.contract")
    contract.Contract = Contract
    wrapper = types.ModuleType("ibapi.wrapper")
    wrapper.EWrapper = EWrapper
    for name, module in (("ibapi", package), ("ibapi.client", client), ("ibapi.contract", contract), ("ibapi.wrapper", wrapper)):
        monkeypatch.setitem(sys.modules, name, module)


def _gateway_bar(start: datetime, close: str) -> SimpleNamespace:
    # keepUpToDate requests use formatDate=2: epoch seconds.
    return SimpleNamespace(date=str(int(start.timestamp())), open=close, high=close, low=close, close=close, volume="5")


def test_official_transport_streams_keep_up_to_date_bars_until_ibkr_ends_them(monkeypatch) -> None:
    _stub_ibapi(monkeypatch)
    transport = OfficialIbapiTransport()
    transport._connected.set()
    gateway = transport._wrapper
    events: list[IbkrBarStreamEvent] = []

    token = transport.subscribe_bars(_contract(), events.append, duration_seconds=86_400, use_rth=False)
    request = transport._client.requests[0]
    # (reqId, contract, endDateTime, duration, barSize, whatToShow, useRTH, formatDate, keepUpToDate, chartOptions)
    assert request[0] == token
    assert request[2:] == ("", "86400 S", "1 min", "TRADES", 0, 2, True, [])

    gateway.historicalData(token, _gateway_bar(OPEN, "100"))
    gateway.historicalData(token, _gateway_bar(OPEN + MINUTE, "101"))
    assert events == []  # the opening history arrives as one event
    gateway.historicalDataEnd(token, "", "")
    gateway.historicalDataUpdate(token, _gateway_bar(OPEN + MINUTE, "101.5"))
    assert [(event.kind, [bar.close for bar in event.bars]) for event in events] == [
        ("history", [Decimal("100"), Decimal("101")]),
        ("update", [Decimal("101.5")]),
    ]
    assert events[1].bars[0].start_time == OPEN + MINUTE

    # A notice does not end the stream; a request error does.
    gateway.error(token, 2176, "Warning: fractional share size rules", "")
    assert len(events) == 2
    gateway.error(token, 10197, "No market data during competing live session", "")
    assert (events[2].kind, events[2].reason) == ("closed", "ibkr_historical_error:10197:No market data during competing live session")
    assert transport.diagnostics()["active_bar_streams"] == 0

    # An ordinary historical request on the same socket is not mistaken for a stream.
    gateway.historicalData(99, _gateway_bar(OPEN, "1"))
    assert [bar.close for bar in transport._historical_rows[99]] == [Decimal("1")]

    second = transport.subscribe_bars(_contract(), events.append, duration_seconds=3_600, use_rth=True)
    assert transport._client.requests[1][3:7] == ("3600 S", "1 min", "TRADES", 1)
    transport.unsubscribe_bars(second)
    transport.unsubscribe_bars(second)
    assert transport._client.cancelled == [second]
    gateway.historicalDataUpdate(second, _gateway_bar(OPEN, "1"))
    assert len(events) == 3


def _service(provider: IbkrEquityProvider, ibkr_stream) -> TradingMarketDataService:
    registry = ProviderRegistry(factories={"binance": lambda: object(), "ibkr": lambda: provider})
    return TradingMarketDataService(registry=registry, ibkr_stream=ibkr_stream)


def test_service_streams_ibkr_intraday_charts_and_rejects_the_rest() -> None:
    transport = FakeIbkrTransport(contracts={"AAPL": [_contract()]})
    provider = IbkrEquityProvider(runtime=IbkrRuntime(transport=transport, enabled=True))
    calls = []

    class FakeIbkrStream:
        async def messages(self, **kwargs):
            calls.append(kwargs)
            yield _live("5m", [OPEN + timedelta(seconds=30)], extended_hours=kwargs["extended_hours"]).apply(
                IbkrBarStreamEvent("history", (_row(OPEN, "100"),))
            )[0]
            await asyncio.Event().wait()

    service = _service(provider, FakeIbkrStream())
    binding_id = _ibkr_binding_id()
    yahoo_id = next(item.binding_id for item in bindings_for_instrument(INSTRUMENT) if item.provider == "yahoo")

    async def first(**kwargs):
        updates = service.stream_updates(INSTRUMENT, **kwargs)
        try:
            return await anext(updates)
        finally:
            await updates.aclose()

    update = asyncio.run(first(interval="5m", binding_id=binding_id, extended_hours=False))
    assert (update.interval, update.extended_hours) == ("5m", False)
    assert calls == [
        {
            "provider_symbol": "AAPL",
            "binding_id": binding_id,
            "instrument_id": INSTRUMENT,
            "interval": "5m",
            "extended_hours": False,
        }
    ]
    # The last client leaving releases the shared upstream.
    assert service.bar_streams.upstream_count() == 0

    with pytest.raises(ValueError, match="does not support Omnix live streaming"):
        asyncio.run(first(interval="1d", binding_id=binding_id))
    with pytest.raises(ValueError, match="does not support Omnix live streaming"):
        asyncio.run(first(interval="5m", binding_id=yahoo_id))


def test_a_stream_cancelled_while_its_request_opens_still_releases_it() -> None:
    opened = threading.Event()
    proceed = threading.Event()
    released: list[int] = []

    class SlowProvider:
        def subscribe_bars(self, instrument_id, listener, *, duration_seconds):
            opened.set()
            proceed.wait(5)  # contract qualification or the history lock, say
            return 7, _contract()

        def unsubscribe_bars(self, token):
            released.append(token)

    stream = IbkrBarStream(lambda: SlowProvider())

    async def scenario():
        updates = stream.messages(provider_symbol="AAPL", binding_id=_ibkr_binding_id(), instrument_id=INSTRUMENT, interval="1m")
        pending = asyncio.ensure_future(anext(updates))
        while not opened.is_set():
            await asyncio.sleep(0.01)
        pending.cancel()  # the chart switched symbol meanwhile
        with pytest.raises(asyncio.CancelledError):
            await pending
        proceed.set()
        for _ in range(200):
            if released:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert released == [7]


def test_no_trades_in_the_opening_window_reopens_after_a_pause_not_at_once(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    transport = FakeIbkrTransport(contracts={"AAPL": [_contract()]})
    provider = IbkrEquityProvider(runtime=IbkrRuntime(transport=transport, enabled=True))
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    stream = IbkrBarStream(lambda: provider, idle_sleep=pause)

    async def scenario():
        updates = stream.messages(provider_symbol="AAPL", binding_id=_ibkr_binding_id(), instrument_id=INSTRUMENT, interval="1m")
        pending = asyncio.ensure_future(anext(updates))
        while not transport.bar_streams:
            await asyncio.sleep(0.01)
        (token,) = transport.bar_streams
        transport.emit_bars(token, IbkrBarStreamEvent("closed", reason=f"{BAR_STREAM_NO_DATA}:162:HMDS query returned no data"))
        with pytest.raises(ConnectionError):
            await pending

    asyncio.run(scenario())
    # A weekend chart asks IBKR again in ten minutes, not every backoff cycle.
    assert pauses == [NO_DATA_RETRY_SECONDS]


def test_the_gateway_reports_no_data_apart_from_failures(monkeypatch) -> None:
    _stub_ibapi(monkeypatch)
    transport = OfficialIbapiTransport()
    transport._connected.set()
    events: list[IbkrBarStreamEvent] = []

    no_data = transport.subscribe_bars(_contract(), events.append, duration_seconds=86_400, use_rth=False)
    transport._wrapper.error(no_data, 162, "Historical Market Data Service error message:HMDS query returned no data: AAPL@SMART Trades", "")
    failing = transport.subscribe_bars(_contract(), events.append, duration_seconds=86_400, use_rth=False)
    transport._wrapper.error(failing, 162, "Historical Market Data Service error message:API historical data query cancelled: 1", "")

    assert events[0].reason.startswith(BAR_STREAM_NO_DATA)
    assert events[1].reason.startswith("ibkr_historical_error:162")


def test_a_derived_bucket_closes_when_the_next_one_starts_even_with_a_slow_local_clock() -> None:
    # The local clock trails IBKR: the first trade of 13:35 arrives at 13:34:59.7 local time.
    now = [OPEN + timedelta(minutes=4, seconds=30)]
    live = _live("5m", now)
    live.apply(IbkrBarStreamEvent("history", tuple(_row(OPEN + offset * MINUTE, "100") for offset in range(5))))

    now[0] = OPEN + timedelta(minutes=4, seconds=59.7)
    updates = live.apply(IbkrBarStreamEvent("update", (_row(OPEN + 5 * MINUTE, "101"),)))

    assert [(item.start_time, item.is_final) for item in updates] == [(OPEN, True), (OPEN + 5 * MINUTE, False)]
