from datetime import datetime, timedelta, timezone
import threading
from decimal import Decimal

import pytest

from app.apps.trading.providers.ibkr_runtime import (
    FakeIbkrTransport,
    IbkrContractAmbiguousError,
    IbkrContractIdentity,
    IbkrHistoricalBar,
    IbkrQuoteSnapshot,
    IbkrRuntime,
    OfficialIbapiTransport,
)


INSTRUMENT = "equity:NASDAQ:AAPL"


def _contract(
    con_id: int = 265598,
    *,
    exchange: str = "SMART",
    primary_exchange: str = "NASDAQ",
) -> IbkrContractIdentity:
    return IbkrContractIdentity(
        con_id=con_id,
        symbol="AAPL",
        local_symbol="AAPL",
        sec_type="STK",
        currency="USD",
        exchange=exchange,
        primary_exchange=primary_exchange,
        trading_class="NMS",
    )


def _runtime(transport: FakeIbkrTransport) -> IbkrRuntime:
    return IbkrRuntime(
        transport=transport,
        enabled=True,
        host="127.0.0.1",
        port=4002,
        client_id=71,
    )


def test_contract_qualification_keeps_unique_conid_and_exchange_identity():
    transport = FakeIbkrTransport(
        contracts={
            "AAPL": [
                _contract(1, primary_exchange="NYSE"),
                _contract(2, primary_exchange="NASDAQ"),
            ]
        }
    )
    runtime = _runtime(transport)

    resolved = runtime.qualify_contract(
        INSTRUMENT,
        symbol="AAPL",
        venue="NASDAQ",
    )

    assert resolved.con_id == 2
    assert resolved.primary_exchange == "NASDAQ"
    assert resolved.local_symbol == "AAPL"


def test_contract_qualification_fails_closed_when_multiple_plausible_rows_remain():
    transport = FakeIbkrTransport(
        contracts={
            "AAPL": [
                _contract(1, primary_exchange="NASDAQ"),
                _contract(2, primary_exchange="NASDAQ"),
            ]
        }
    )
    runtime = _runtime(transport)

    with pytest.raises(IbkrContractAmbiguousError, match="IBKR_CONTRACT_AMBIGUOUS"):
        runtime.qualify_contract(
            INSTRUMENT,
            symbol="AAPL",
            venue="NASDAQ",
        )


def test_quote_subscription_is_deduplicated_across_consumers():
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = _runtime(transport)
    runtime.connect()
    seen_a = []
    seen_b = []

    token_a = runtime.subscribe_quote(INSTRUMENT, contract=contract, listener=seen_a.append)
    token_b = runtime.subscribe_quote(INSTRUMENT, contract=contract, listener=seen_b.append)

    assert token_a == token_b
    assert len(transport.listeners) == 1
    snapshot = IbkrQuoteSnapshot(
        contract=contract,
        bid=Decimal("100"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        market_data_type="LIVE",
    )
    transport.emit(token_a, snapshot)

    assert seen_a == [snapshot]
    assert seen_b == [snapshot]
    assert runtime.diagnostics()["active_quote_subscriptions"] == 1


def test_gateway_reconnect_invalidates_old_request_ids_quotes_and_resubscribes():
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = _runtime(transport)
    runtime.connect()
    first = runtime.subscribe_quote(INSTRUMENT, contract=contract)
    assert first in transport.listeners
    stale = IbkrQuoteSnapshot(
        contract=contract,
        bid=Decimal("100"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        source_time=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
        market_data_type="LIVE",
    )
    transport.emit(first, stale)
    assert runtime.latest_quote(INSTRUMENT) == stale

    transport.disconnect()
    assert runtime.latest_quote(INSTRUMENT) is None

    runtime.connect()
    assert runtime.reconnect_count == 1
    assert runtime.latest_quote(INSTRUMENT) is None
    assert runtime.diagnostics()["active_quote_subscriptions"] == 0

    second = runtime.subscribe_quote(INSTRUMENT, contract=contract)
    assert second != first
    assert second in transport.listeners
    assert runtime.latest_quote(INSTRUMENT) is None


def test_subscription_health_preserves_entitlement_denial():
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = _runtime(transport)
    token = runtime.subscribe_quote(INSTRUMENT, contract=contract)
    transport.request_health_by_token[token] = {
        "request_id": token,
        "market_data_type": "UNKNOWN",
        "error": {"code": 354, "message": "Not subscribed"},
        "entitlement_denied": True,
    }

    health = runtime.subscription_health(INSTRUMENT)

    assert health["entitlement_denied"] is True
    assert health["error"]["code"] == 354


def test_historical_range_is_clipped_to_requested_window(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    contract = _contract()
    start = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
    transport = FakeIbkrTransport(
        contracts={"AAPL": [contract]},
        history={
            contract.con_id: [
                IbkrHistoricalBar(
                    start_time=start - timedelta(minutes=1),
                    open=Decimal("10"),
                    high=Decimal("10"),
                    low=Decimal("10"),
                    close=Decimal("10"),
                    volume=Decimal("10"),
                ),
                IbkrHistoricalBar(
                    start_time=start,
                    open=Decimal("11"),
                    high=Decimal("11"),
                    low=Decimal("11"),
                    close=Decimal("11"),
                    volume=Decimal("11"),
                ),
                IbkrHistoricalBar(
                    start_time=start + timedelta(minutes=1),
                    open=Decimal("12"),
                    high=Decimal("12"),
                    low=Decimal("12"),
                    close=Decimal("12"),
                    volume=Decimal("12"),
                ),
            ]
        },
    )
    runtime = _runtime(transport)

    rows = runtime.historical_bars(
        contract,
        start=start,
        end=start + timedelta(minutes=1),
    )

    assert [row.start_time for row in rows] == [start]
    assert runtime.historical_request_count == 1


def test_official_transport_socket_reset_wakes_waiters_and_clears_request_state():
    transport = OfficialIbapiTransport.__new__(OfficialIbapiTransport)
    transport._lock = threading.RLock()
    transport._connected = threading.Event()
    transport._connected.set()
    contract_event = threading.Event()
    historical_event = threading.Event()
    transport._contract_rows = {1: [_contract()]}
    transport._contract_events = {1: contract_event}
    transport._historical_rows = {
        2: [
            IbkrHistoricalBar(
                start_time=datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        ]
    }
    transport._historical_events = {2: historical_event}
    transport._quote_contracts = {3: _contract()}
    transport._quote_listeners = {3: lambda snapshot: None}
    transport._quote_values = {3: {"last": Decimal("100")}}
    transport._market_data_types = {3: "LIVE"}
    transport._request_errors = {2: {"code": 162}}
    transport._farm_status = {"market_data": "READY"}

    transport._reset_socket_state()

    assert not transport._connected.is_set()
    assert contract_event.is_set()
    assert historical_event.is_set()
    assert transport._contract_rows == {}
    assert transport._contract_events == {}
    assert transport._historical_rows == {}
    assert transport._historical_events == {}
    assert transport._quote_contracts == {}
    assert transport._quote_listeners == {}
    assert transport._quote_values == {}
    assert transport._market_data_types == {}
    assert transport._request_errors == {}
    assert transport._farm_status == {}


def test_wait_for_quote_wakes_when_the_quote_arrives_instead_of_polling():
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = _runtime(transport)
    runtime.connect()
    token = runtime.subscribe_quote(INSTRUMENT, contract=contract)
    snapshot = IbkrQuoteSnapshot(contract=contract, last=Decimal("100.01"), market_data_type="LIVE")

    timer = threading.Timer(0.05, transport.emit, args=(token, snapshot))
    timer.start()
    started = datetime.now(timezone.utc)
    assert runtime.wait_for_quote(INSTRUMENT, timeout_seconds=5.0) == snapshot
    assert datetime.now(timezone.utc) - started < timedelta(seconds=2)
    timer.join()

    # No quote with a last price: the wait ends at the timeout with what it has.
    assert runtime.wait_for_quote("equity:NASDAQ:MSFT", timeout_seconds=0.05) is None


def _bar(start: datetime, price: str = "11") -> IbkrHistoricalBar:
    value = Decimal(price)
    return IbkrHistoricalBar(start_time=start, open=value, high=value, low=value, close=value, volume=value)


def test_finished_historical_ranges_are_asked_once_including_empty_answers(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    contract = _contract()
    start = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]}, history={contract.con_id: [_bar(start)]})
    now = [start + timedelta(hours=1)]
    runtime = IbkrRuntime(
        transport=transport, enabled=True, host="127.0.0.1", port=4002, client_id=71, clock=lambda: now[0],
    )
    minute = timedelta(minutes=1)

    # Gap repair asks for the same finished minutes every cycle; the Gateway answers once.
    for _ in range(3):
        assert [row.start_time for row in runtime.historical_bars(contract, start=start, end=start + minute)] == [start]
    # "No trades in this range" is a final answer too.
    for _ in range(3):
        assert runtime.historical_bars(contract, start=start + 10 * minute, end=start + 11 * minute) == []
    assert runtime.historical_request_count == 2
    assert runtime.historical_cache_hit_count == 4

    # A range that ended moments ago may still change: strategies asking for it
    # within 30 seconds share one answer, and it is asked again after that.
    now[0] = start + 12 * minute
    recent = dict(start=start + 10 * minute, end=start + 12 * minute)
    for _ in range(2):
        runtime.historical_bars(contract, **recent)
    assert runtime.historical_request_count == 3
    now[0] += timedelta(seconds=31)
    runtime.historical_bars(contract, **recent)
    assert runtime.historical_request_count == 4
    assert runtime.diagnostics()["historical_cache_entries"] == 3


class _ScriptedClient:
    def __init__(self, transport: OfficialIbapiTransport, script) -> None:
        self.transport = transport
        self.script = script

    def isConnected(self) -> bool:  # noqa: N802
        return True

    def reqHistoricalData(self, req_id, *_args) -> None:  # noqa: N802
        self.script(self.transport, req_id)

    def cancelHistoricalData(self, req_id) -> None:  # noqa: N802
        return None


def _official_transport(script) -> OfficialIbapiTransport:
    transport = OfficialIbapiTransport.__new__(OfficialIbapiTransport)
    transport._lock = threading.RLock()
    transport._connected = threading.Event()
    transport._connected.set()
    transport._next_request_id = 10_000
    transport._contract_rows, transport._contract_events = {}, {}
    transport._historical_rows, transport._historical_events = {}, {}
    transport._quote_contracts, transport._quote_listeners, transport._quote_values = {}, {}, {}
    transport._market_data_types, transport._request_errors, transport._farm_status = {}, {}, {}
    transport._error_count, transport._entitlement_error_count, transport._last_error = 0, 0, None
    transport._qualified_contract = lambda identity: identity
    transport._client = _ScriptedClient(transport, script)
    return transport


def _request(transport: OfficialIbapiTransport):
    return transport.historical_bars(
        _contract(), end=datetime(2026, 10, 14, 14, 1, tzinfo=timezone.utc), duration_seconds=60,
        bar_size="1 min", what_to_show="TRADES", use_rth=True, timeout_seconds=5.0,
    )


def test_official_transport_historical_errors_end_the_request_instead_of_waiting_for_the_timeout():
    started = datetime.now(timezone.utc)

    # IBKR's "no data" answer for minutes without trades is a factual empty result.
    def no_data(transport, req_id):
        transport._on_request_error(req_id, 162, "Historical Market Data Service error message:HMDS query returned no data: AEHL@SMART Trades")
    assert _request(_official_transport(no_data)) == []

    # Any other request error (a pacing violation here) fails at once and is never mistaken for "no trades".
    def pacing(transport, req_id):
        transport._on_request_error(req_id, 162, "Historical Market Data Service error message:API historical data query cancelled: pacing violation")
    with pytest.raises(Exception, match="ibkr_historical_error:162"):
        _request(_official_transport(pacing))

    # A socket reset is "no answer", not an empty answer.
    def reset(transport, req_id):
        transport._historical_rows[req_id].append(_bar(datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)))
        transport._reset_socket_state()
    with pytest.raises(Exception, match="ibkr_gateway_disconnected"):
        _request(_official_transport(reset))

    # Notices (2000+) do not end the request; the data that follows is returned.
    def notice_then_data(transport, req_id):
        transport._on_request_error(req_id, 2176, "Warning: fractional share size rules")
        assert not transport._historical_events[req_id].is_set()
        transport._historical_rows[req_id].append(_bar(datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)))
        transport._historical_events[req_id].set()
    assert len(_request(_official_transport(notice_then_data))) == 1
    assert datetime.now(timezone.utc) - started < timedelta(seconds=3)


def test_concurrent_requests_for_one_finished_range_reach_the_gateway_once(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    contract = _contract()
    start = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]}, history={contract.con_id: [_bar(start)]})
    runtime = IbkrRuntime(
        transport=transport, enabled=True, host="127.0.0.1", port=4002, client_id=71,
        clock=lambda: start + timedelta(hours=1),
    )
    answer = transport.historical_bars
    first_in_flight = threading.Event()
    release = threading.Event()

    def slow_gateway(*args, **kwargs):
        first_in_flight.set()
        release.wait(5)
        return answer(*args, **kwargs)

    monkeypatch.setattr(transport, "historical_bars", slow_gateway)
    window = dict(start=start, end=start + timedelta(minutes=15))
    results = []
    # Tape warmers ask for the same block from several threads at once.
    workers = [threading.Thread(target=lambda: results.append(runtime.historical_bars(contract, **window))) for _ in range(2)]
    workers[0].start()
    assert first_in_flight.wait(5)
    workers[1].start()
    release.set()
    for worker in workers:
        worker.join(5)

    assert len(results) == 2 and results[0] == results[1]
    assert runtime.historical_request_count == 1
