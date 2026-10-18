"""Browser clients share one upstream stream that reconnects and fills gaps (WP-8.3)."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.streaming.manager import (
    SharedBarStreamHub,
    SharedSubscriptionManager,
    StreamingBarUpdate,
    StreamingStatusUpdate,
)

T0 = datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc)


def _bar(minute: int, *, final: bool = True) -> StreamingBarUpdate:
    start = T0 + timedelta(minutes=minute)
    return StreamingBarUpdate(
        binding_id="binance:btc",
        instrument_id="crypto:BTCUSDT",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        is_final=final,
    )


def test_clients_share_one_upstream_that_reconnects_and_recovers_the_gap() -> None:
    async def scenario():
        upstream_calls = []
        hold = asyncio.Event()

        async def upstream(**kwargs):
            upstream_calls.append(kwargs)
            if len(upstream_calls) == 1:
                yield _bar(0)
                yield _bar(1)
                raise ConnectionError("socket dropped")
            yield _bar(5, final=False)
            await hold.wait()

        windows = []

        async def recover(binding_id, instrument_id, interval, start, end):
            windows.append((start, end))
            return [_bar(2), _bar(3), _bar(4)]

        async def no_wait(_seconds):
            await asyncio.sleep(0)

        hub = SharedBarStreamHub(SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, sleep=no_wait)
        listen = dict(provider_symbol="BTCUSDT", binding_id="binance:btc", instrument_id="crypto:BTCUSDT", interval="1m")

        def label(item):
            return item.connected if isinstance(item, StreamingStatusUpdate) else item.start_time

        async with hub.listen(**listen) as first, hub.listen(**listen) as second:
            seen_first = [label(await first.get()) for _ in range(8)]
            seen_second = [label(await second.get()) for _ in range(8)]
            assert hub.upstream_count() == 1
            assert hub.subscriptions.status()[0]["reconnects"] == 1
        return upstream_calls, windows, seen_first, seen_second, hub

    upstream_calls, windows, seen_first, seen_second, hub = asyncio.run(scenario())

    bars = [T0 + timedelta(minutes=minute) for minute in range(6)]
    # Clients hear the drop and the recovery, so a chart can poll in between.
    expected = [bars[0], bars[1], False, True, *bars[2:]]
    assert seen_first == seen_second == expected
    # One upstream for both clients, reopened once after the drop.
    assert len(upstream_calls) == 2
    assert windows == [(T0 + timedelta(minutes=2), T0 + timedelta(minutes=5))]
    # The last client leaving closes the upstream.
    assert hub.upstream_count() == 0
    assert hub.subscriptions.upstream_subscription_count == 0


def test_a_slow_client_drops_its_oldest_updates_instead_of_blocking_others() -> None:
    async def scenario():
        async def upstream(**_kwargs):
            for minute in range(5):
                yield _bar(minute)
            await asyncio.Event().wait()

        async def recover(*_args):
            return []

        hub = SharedBarStreamHub(
            SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, queue_size=2
        )
        async with hub.listen(provider_symbol="BTCUSDT", binding_id="binance:btc", instrument_id="crypto:BTCUSDT", interval="1m") as slow:
            for _ in range(10):
                await asyncio.sleep(0)
            return [(await slow.get()).start_time for _ in range(2)]

    assert asyncio.run(scenario()) == [T0 + timedelta(minutes=3), T0 + timedelta(minutes=4)]


def _equity_bar(minute: int, *, extended_hours: bool | None, final: bool = True) -> StreamingBarUpdate:
    start = T0 + timedelta(minutes=minute)
    return StreamingBarUpdate(
        binding_id="ibkr:aapl",
        instrument_id="equity:NASDAQ:AAPL",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        is_final=final,
        extended_hours=extended_hours,
    )


def test_extended_hours_streams_are_shared_separately_and_recover_with_their_scope() -> None:
    async def scenario():
        upstream_calls = []
        recover_calls = []
        hold = asyncio.Event()

        async def upstream(**kwargs):
            upstream_calls.append(kwargs)
            scope = kwargs["extended_hours"]
            if sum(1 for call in upstream_calls if call["extended_hours"] == scope) == 1:
                yield _equity_bar(0, extended_hours=scope)
                raise ConnectionError("gateway restarted")
            yield _equity_bar(15, extended_hours=scope, final=False)
            await hold.wait()

        async def recover(binding_id, instrument_id, interval, start, end, *, extended_hours):
            recover_calls.append((extended_hours, start, end))
            # REST bars carry no scope; the hub stamps the stream's own.
            return [_equity_bar(5, extended_hours=None), _equity_bar(10, extended_hours=None)]

        async def no_wait(_seconds):
            await asyncio.sleep(0)

        hub = SharedBarStreamHub(SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, sleep=no_wait)
        listen = dict(provider_symbol="AAPL", binding_id="ibkr:aapl", instrument_id="equity:NASDAQ:AAPL", interval="5m")
        async with hub.listen(**listen, extended_hours=True) as ext, hub.listen(**listen, extended_hours=False) as rth:
            seen = {}
            for name, queue in (("ext", ext), ("rth", rth)):
                items = [await queue.get() for _ in range(6)]
                seen[name] = [
                    item.connected if isinstance(item, StreamingStatusUpdate) else (item.start_time, item.extended_hours)
                    for item in items
                ]
            assert hub.upstream_count() == 2
            keys = sorted(item["key"] for item in hub.subscriptions.status())
        return upstream_calls, recover_calls, seen, keys

    upstream_calls, recover_calls, seen, keys = asyncio.run(scenario())

    assert keys == ["ibkr:aapl|equity:NASDAQ:AAPL|5m|ext", "ibkr:aapl|equity:NASDAQ:AAPL|5m|rth"]
    assert sorted(call["extended_hours"] for call in upstream_calls) == [False, False, True, True]
    gap = (T0 + timedelta(minutes=5), T0 + timedelta(minutes=15))
    assert sorted(recover_calls) == [(False, *gap), (True, *gap)]
    for name, scope in (("ext", True), ("rth", False)):
        assert seen[name] == [
            (T0, scope),
            False,
            True,
            (T0 + timedelta(minutes=5), scope),
            (T0 + timedelta(minutes=10), scope),
            (T0 + timedelta(minutes=15), scope),
        ]


def _bar_at(minute: int, interval: str, *, final: bool = True) -> StreamingBarUpdate:
    start = T0 + timedelta(minutes=minute)
    return replace(_bar(0, final=final), interval=interval, start_time=start, end_time=start + timedelta(minutes=2))


def test_intervals_outside_the_recovery_table_still_recover_gaps_instead_of_reopening() -> None:
    async def scenario():
        opens = []
        hold = asyncio.Event()

        async def upstream(**kwargs):
            opens.append(kwargs)
            if len(opens) == 1:
                yield _bar_at(0, "2m")
                raise ConnectionError("gateway restarted")
            yield _bar_at(6, "2m", final=False)
            await hold.wait()

        windows = []

        async def recover(binding_id, instrument_id, interval, start, end):
            windows.append((interval, start, end))
            return [_bar_at(2, "2m"), _bar_at(4, "2m")]

        async def no_wait(_seconds):
            await asyncio.sleep(0)

        hub = SharedBarStreamHub(SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, sleep=no_wait)
        async with hub.listen(provider_symbol="BTCUSDT", binding_id="binance:btc", instrument_id="crypto:BTCUSDT", interval="2m") as queue:
            items = [await queue.get() for _ in range(6)]
        return opens, windows, items

    opens, windows, items = asyncio.run(scenario())

    # 2m is not in gap_recovery's table; its duration comes from the interval itself.
    assert len(opens) == 2
    assert windows == [("2m", T0 + timedelta(minutes=2), T0 + timedelta(minutes=6))]
    starts = [item.start_time for item in items if isinstance(item, StreamingBarUpdate)]
    assert starts == [T0 + timedelta(minutes=minute) for minute in (0, 2, 4, 6)]


def test_an_upstream_that_fails_on_every_opening_backs_off_instead_of_retrying_each_second() -> None:
    async def scenario():
        opens = []

        async def upstream(**_kwargs):
            opens.append(1)
            yield _bar(len(opens) * 5)  # minutes apart, so every reopening has a gap to recover

        async def recover(*_args):
            raise ConnectionError("history unavailable")

        sleeps = []

        async def record(seconds):
            sleeps.append(seconds)
            await asyncio.sleep(0)

        hub = SharedBarStreamHub(SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, sleep=record)
        async with hub.listen(provider_symbol="BTCUSDT", binding_id="binance:btc", instrument_id="crypto:BTCUSDT", interval="1m") as queue:
            await queue.get()
            while len(sleeps) < 4:
                await asyncio.sleep(0)
        return sleeps

    # Delivered once, then every opening fails before delivering: the backoff keeps growing.
    assert asyncio.run(scenario())[:4] == [1.0, 2.0, 4.0, 8.0]


def test_a_client_joining_a_reconnecting_upstream_is_told_it_is_down() -> None:
    async def scenario():
        async def upstream(**_kwargs):
            raise ConnectionError("gateway down")
            yield  # pragma: no cover - makes this an async generator

        async def recover(*_args):
            return []

        release = asyncio.Event()

        async def wait(_seconds):
            await release.wait()

        hub = SharedBarStreamHub(SharedSubscriptionManager(), open_upstream=upstream, recover_gap=recover, sleep=wait)
        listen = dict(provider_symbol="BTCUSDT", binding_id="binance:btc", instrument_id="crypto:BTCUSDT", interval="1m")
        async with hub.listen(**listen) as first:
            assert await first.get() == StreamingStatusUpdate(connected=False)
            async with hub.listen(**listen) as second:
                # A new tab or a reconnected socket must not show the frozen chart as live.
                return second.get_nowait()

    assert asyncio.run(scenario()) == StreamingStatusUpdate(connected=False)
