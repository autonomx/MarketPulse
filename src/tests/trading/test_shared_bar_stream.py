"""Browser clients share one upstream stream that reconnects and fills gaps (WP-8.3)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.streaming.manager import SharedBarStreamHub, SharedSubscriptionManager, StreamingBarUpdate

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
        async with hub.listen(**listen) as first, hub.listen(**listen) as second:
            seen_first = [(await first.get()).start_time for _ in range(6)]
            seen_second = [(await second.get()).start_time for _ in range(6)]
            assert hub.upstream_count() == 1
            assert hub.subscriptions.status()[0]["reconnects"] == 1
        return upstream_calls, windows, seen_first, seen_second, hub

    upstream_calls, windows, seen_first, seen_second, hub = asyncio.run(scenario())

    expected = [T0 + timedelta(minutes=minute) for minute in range(6)]
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
