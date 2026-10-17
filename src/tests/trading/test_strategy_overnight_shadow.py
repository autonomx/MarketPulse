from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.apps.trading.strategies.models import OvernightArmConfig, OvernightShadowConfig
from app.apps.trading.strategies.registrations import STRATEGY_REGISTRY
from app.apps.trading.strategy_monitor import strategy_monitor_owns
from app.apps.trading.strategy_overnight_monitor import (
    EVENT_OUTCOME,
    EVENT_PICK,
    EVENT_SCAN,
    EVENT_SCORECARD,
    OVERNIGHT_SHADOW_ACCOUNT_ID,
    OvernightShadowMonitor,
    overnight_shadow_document,
    provision_overnight_shadow_strategy,
)
from app.apps.trading.strategy_overnight_scan import OvernightScanResult, is_common_stock
from app.apps.trading.strategy_overnight_shadow import (
    OVERNIGHT_TRADING_STRATEGY_ID,
    OvernightBar,
    build_candidate,
    label_outcome,
    population,
    select_arm_picks,
)
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument
from app.apps.trading.strategy_universe_archiver import archive_daily_universe_if_due
from app.apps.trading.us_equity_calendar import EASTERN as ET

D = Decimal
SESSION = date(2026, 10, 13)      # Tuesday
NEXT = date(2026, 10, 14)


def _bar(day: date, hh: int, mm: int, o, h, l, c, v) -> OvernightBar:
    return OvernightBar(datetime.combine(day, time(hh, mm), tzinfo=ET), D(str(o)), D(str(h)), D(str(l)), D(str(c)), D(str(v)))


def _candidate(symbol="RUNR", *, change="0.40", price="4.20", close_location="0.9", volume_ratio="8", dollar_volume="5000000"):
    from app.apps.trading.strategy_overnight_shadow import OvernightCandidate
    prior = D(price) / (1 + D(change))
    return OvernightCandidate(
        symbol=symbol, instrument_id=f"equity:NASDAQ:{symbol}", prior_close=prior, price=D(price), change=D(change),
        high=D(price), low=prior, close_location=D(close_location), volume=D("1000000"), adv=D("100000"),
        volume_ratio=None if volume_ratio is None else D(volume_ratio), dollar_volume=D(dollar_volume),
    )


# ------------------------------------------------------------ config and registry
def test_overnight_kind_is_registered_shadow_only_and_not_strategy_monitor_owned() -> None:
    entry = STRATEGY_REGISTRY.get("overnight_shadow_v1")
    assert entry.allowed_modes == ("off", "shadow")
    doc = overnight_shadow_document("acct")
    assert doc.strategy_id == OVERNIGHT_TRADING_STRATEGY_ID and doc.mode == "shadow"
    payload = doc.model_dump(mode="python")
    payload["mode"] = "auto_paper"
    with pytest.raises(ValueError, match="overnight_shadow_is_shadow_only"):
        TradingStrategyConfigDocument.model_validate(payload)
    assert strategy_monitor_owns(doc) is False
    gap = TradingStrategyConfigDocument(strategy_id="g", account_id="a")
    assert strategy_monitor_owns(gap) is True


def test_overnight_config_rejects_inconsistent_times_and_duplicate_arms() -> None:
    with pytest.raises(ValueError, match="data_cutoff_et < decision_et < entry_et"):
        OvernightShadowConfig(decision_et=time(15, 56))
    arm = OvernightArmConfig(arm_id="day2-runner", min_change=D("0.3"), min_volume_ratio=D("5"), min_close_location=D("0.8"))
    with pytest.raises(ValueError, match="unique"):
        OvernightShadowConfig(arms=(arm, arm))
    with pytest.raises(ValueError, match="max_change must exceed min_change"):
        OvernightArmConfig(arm_id="strong-close", min_change=D("0.3"), max_change=D("0.1"),
                           min_volume_ratio=D("3"), min_close_location=D("0.9"))


def test_universe_archiver_skips_kinds_without_a_gapper_universe() -> None:
    assert archive_daily_universe_if_due(overnight_shadow_document("acct"), repository=None) is None


# ------------------------------------------------------------ pure logic
def test_build_candidate_uses_only_bars_ended_by_the_cutoff() -> None:
    bars = [
        _bar(SESSION, 9, 30, 2.0, 2.5, 1.9, 2.4, 100_000),
        _bar(SESSION, 15, 0, 2.4, 3.0, 2.3, 2.9, 200_000),
        _bar(SESSION, 15, 30, 2.9, 9.0, 0.5, 8.0, 999_999),  # ends 16:00: unknown at decision time
    ]
    c = build_candidate(symbol="ABC", instrument_id="equity:NASDAQ:ABC", prior_close=D("2"), bars=bars,
                        adv_volumes=[D("30000")] * 20, session_date=SESSION, config=OvernightShadowConfig())
    assert c is not None
    assert c.price == D("2.9") and c.high == D("3.0") and c.low == D("1.9")
    assert c.change == D("0.450000")
    assert c.volume == D("300000") and c.volume_ratio == D("10.000000")
    short = build_candidate(symbol="ABC", instrument_id="x", prior_close=D("2"), bars=bars[:2],
                            adv_volumes=[D("1")] * 5, session_date=SESSION, config=OvernightShadowConfig())
    assert short is not None and short.adv is None and short.volume_ratio is None  # < min_adv_sessions


def test_arm_selection_applies_rules_and_ranks_by_dollar_volume() -> None:
    cands = [
        _candidate("RUN1", change="0.40", dollar_volume="9000000"),
        _candidate("RUN2", change="0.35", dollar_volume="1000000"),
        _candidate("WEAK", change="0.40", close_location="0.5"),           # fails day2 close location
        _candidate("THIN", change="0.40", volume_ratio="2"),               # fails volume ratio
        _candidate("NOADV", change="0.40", volume_ratio=None),             # no ADV -> never qualifies
        _candidate("MID1", change="0.15", close_location="0.95"),
        _candidate("EDGE", change="0.30", close_location="0.95"),          # max_change is exclusive
        _candidate("PRICY", change="0.40", price="25"),                    # outside price band
    ]
    picks = select_arm_picks(cands, OvernightShadowConfig())
    assert [p.candidate.symbol for p in picks["day2-runner"]] == ["RUN1", "EDGE", "RUN2"]  # $9M, $5M, $1M
    assert [p.rank for p in picks["day2-runner"]] == [1, 2, 3]
    assert [p.candidate.symbol for p in picks["strong-close"]] == ["MID1"]
    assert [c.symbol for c in population(cands, OvernightShadowConfig())] == sorted(
        ["RUN1", "RUN2", "WEAK", "THIN", "NOADV", "MID1", "EDGE"])


def test_label_outcome_entry_exit_costs_and_missing_bars() -> None:
    c = _candidate("RUN1", price="4.00", change="0.40")
    cfg = OvernightShadowConfig()
    session = [_bar(SESSION, 15, 54, 4.0, 4.0, 4.0, 4.0, 1), _bar(SESSION, 15, 55, 4.10, 4.2, 4.0, 4.1, 1)]
    morning = [_bar(NEXT, 7, 59, 4.8, 4.8, 4.8, 4.9, 1), _bar(NEXT, 9, 30, 5.00, 5.1, 4.9, 5.0, 1),
               _bar(NEXT, 9, 45, 5.2, 5.2, 5.2, 5.2, 1)]
    o = label_outcome(arm_id="day2-runner", candidate=c, session_date=SESSION, next_session_date=NEXT,
                      session_bars=session, next_bars=morning, config=cfg)
    assert o.status == "labelled" and o.entry_price == D("4.10") and o.exit_open == D("5.00")
    assert o.premarket_0800 == D("4.9") and o.price_0945 == D("5.2")
    expected = D("5.00") * (1 - D("0.01")) / (D("4.10") * (1 + D("0.01"))) - 1
    assert o.net_return == expected.quantize(D("0.000001"))
    halted = label_outcome(arm_id="day2-runner", candidate=c, session_date=SESSION, next_session_date=NEXT,
                           session_bars=session, next_bars=[], config=cfg)
    assert halted.status == "no_open" and halted.net_return == D("-0.50")
    no_entry = label_outcome(arm_id="day2-runner", candidate=c, session_date=SESSION, next_session_date=NEXT,
                             session_bars=[], next_bars=morning, config=cfg)
    assert no_entry.status == "no_entry" and no_entry.net_return is None


def test_common_stock_filter() -> None:
    assert is_common_stock({"symbol": "ABCD", "name": "Abcd Inc. Common Stock", "exchange": "NASDAQ"})
    assert not is_common_stock({"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "exchange": "NASDAQ"})
    assert not is_common_stock({"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "ARCA"})
    assert not is_common_stock({"symbol": "ABCDW", "name": "Abcd Inc. Warrant", "exchange": "NASDAQ"})
    assert is_common_stock({"symbol": "SNOWR", "name": "Snowr Holdings Ordinary Shares", "exchange": "NASDAQ"})


# ------------------------------------------------------------ monitor with fakes
class FakeRepository:
    def __init__(self, config=None) -> None:
        self.config = config
        self.events = {}

    def get_config(self, strategy_id):
        if self.config is None or strategy_id != self.config.strategy_id:
            raise ValueError("strategy_config_not_found")
        return self.config

    def create_config(self, document):
        self.config = document
        return document

    def append_event(self, event) -> bool:
        if event.idempotency_key in self.events:
            return False
        self.events[event.idempotency_key] = event
        return True

    def events_by_types_between(self, strategy_id, *, event_types, start_time, end_time, limit=10_000):
        return [e for e in self.events.values() if e.strategy_id == strategy_id and e.event_type in event_types
                and start_time <= e.observed_at < end_time]

    def of(self, event_type):
        return [e for e in self.events.values() if e.event_type == event_type]


class FakeMarketData:
    def __init__(self) -> None:
        self.scans = 0

    def scan(self, session_date, prior_session, config):
        self.scans += 1
        return OvernightScanResult(session_date=session_date, eligible=10, scanned=9, candidates=(
            _candidate("RUN1", price="4.00", change="0.40"), _candidate("MID1", change="0.15", close_location="0.95")))

    def minute_bars(self, symbols, start, end):
        day = start.astimezone(ET).date()
        if day == SESSION:
            return {s: [_bar(SESSION, 15, 55, 4.0, 4, 4, 4, 1)] for s in symbols}
        return {s: [_bar(NEXT, 9, 30, 5.0, 5, 5, 5, 1)] for s in symbols}


def _monitor(repo, data, clock):
    return OvernightShadowMonitor(strategy_repository_factory=lambda: repo, market_data_factory=lambda: data,
                                  now_factory=lambda: clock[0])


def test_monitor_scans_once_in_window_then_labels_next_morning_once() -> None:
    repo, data = FakeRepository(overnight_shadow_document("acct")), FakeMarketData()
    clock = [datetime.combine(SESSION, time(15, 49), tzinfo=ET).astimezone(timezone.utc)]
    monitor = _monitor(repo, data, clock)
    assert asyncio.run(monitor.run_once()) == 0 and data.scans == 0          # before decision time
    clock[0] = datetime.combine(SESSION, time(15, 51), tzinfo=ET).astimezone(timezone.utc)
    assert asyncio.run(monitor.run_once()) == 1
    assert asyncio.run(monitor.run_once()) == 0 and data.scans == 1          # idempotent per session
    assert len(repo.of(EVENT_SCAN)) == 1
    assert {(e.payload["arm_id"], e.payload["candidate"]["symbol"]) for e in repo.of(EVENT_PICK)} == {
        ("day2-runner", "RUN1"), ("strong-close", "MID1")}
    clock[0] = datetime.combine(NEXT, time(10, 0), tzinfo=ET).astimezone(timezone.utc)
    assert asyncio.run(monitor.run_once()) == 0                               # before label_after_et
    clock[0] = datetime.combine(NEXT, time(10, 6), tzinfo=ET).astimezone(timezone.utc)
    assert asyncio.run(monitor.run_once()) == 1
    assert asyncio.run(monitor.run_once()) == 0                               # scorecard found: no relabel
    outcomes = repo.of(EVENT_OUTCOME)
    assert len(outcomes) == 2 and all(e.state == "labelled" for e in outcomes)
    card = repo.of(EVENT_SCORECARD)[0].payload
    assert card["execution_authority"] is False and card["population_10pct"]["picks"] == 2
    assert {a["arm_id"]: a["picks"] for a in card["arms"]} == {"day2-runner": 1, "strong-close": 1}


def test_a_timed_out_scan_is_not_started_twice_and_its_failure_is_logged(monkeypatch) -> None:
    from app.apps.trading import monitor_task
    from app.apps.trading.monitor_task import TradingMonitorTask, scheduled_task_spec

    logged = []
    monkeypatch.setattr(monitor_task, "trade_log", lambda *args, **fields: logged.append((args, fields)))
    started, release = threading.Event(), threading.Event()

    class StalledMarketData(FakeMarketData):
        def scan(self, session_date, prior_session, config):
            self.scans += 1
            started.set()
            release.wait(5)
            raise RuntimeError("alpaca stalled")

    repo, data = FakeRepository(overnight_shadow_document("acct")), StalledMarketData()
    clock = [datetime.combine(SESSION, time(15, 51), tzinfo=ET).astimezone(timezone.utc)]
    monitor = _monitor(repo, data, clock)
    spec = scheduled_task_spec(TradingMonitorTask(name="tests.overnight", monitor=monitor, enabled=lambda: True))
    # The scan window is 15:50-16:00; a timeout retries instead of stopping the monitor.
    assert spec.timeout_seconds == 600.0 and spec.disable_on_timeout is False

    async def scenario():
        first = asyncio.create_task(monitor.run_once())
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()  # what the scheduler does when the cycle times out
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await monitor.run_once() == 0  # the abandoned scan is still running
        release.set()

    asyncio.run(scenario())  # waits for the abandoned worker thread to exit
    assert data.scans == 1 and not repo.of(EVENT_SCAN)
    assert logged == [(
        ("auto_trading", "overnight_shadow_monitor_error"),
        {"error_type": "RuntimeError", "detail": "alpaca stalled", "execution_authority": False,
         "strategy_id": OVERNIGHT_TRADING_STRATEGY_ID},
    )]


def test_monitor_skips_early_close_off_mode_and_missing_config() -> None:
    data = FakeMarketData()
    early = date(2026, 11, 27)  # day after Thanksgiving: 13:00 close
    clock = [datetime.combine(early, time(15, 51), tzinfo=ET).astimezone(timezone.utc)]
    assert asyncio.run(_monitor(FakeRepository(overnight_shadow_document("a")), data, clock).run_once()) == 0
    off = overnight_shadow_document("a").model_copy(update={"mode": "off"})
    clock[0] = datetime.combine(SESSION, time(15, 51), tzinfo=ET).astimezone(timezone.utc)
    assert asyncio.run(_monitor(FakeRepository(off), data, clock).run_once()) == 0
    assert asyncio.run(_monitor(FakeRepository(None), data, clock).run_once()) == 0
    assert data.scans == 0


# ------------------------------------------------------------ provisioning
class FakePaperRepository:
    def __init__(self) -> None:
        self.accounts = []

    def list_accounts(self, limit=500):
        return [type("A", (), {"account_id": a})() for a in self.accounts]

    def create_account(self, request):
        self.accounts.append(request.account_id)


def test_provisioning_creates_once_and_never_overwrites(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_TRADING_OVERNIGHT_SHADOW_AUTOPROVISION", "1")
    monkeypatch.setenv("OMNIX_TRADING_OVERNIGHT_SHADOW_AUTOPROVISION_IN_TESTS", "1")
    repo, paper = FakeRepository(None), FakePaperRepository()
    assert provision_overnight_shadow_strategy(strategy_repository=repo, paper_repository=paper) == "created"
    assert paper.accounts == [OVERNIGHT_SHADOW_ACCOUNT_ID] and repo.config.mode == "shadow"
    repo.config = repo.config.model_copy(update={"mode": "off"})   # operator change survives restart
    assert provision_overnight_shadow_strategy(strategy_repository=repo, paper_repository=paper) == "unchanged"
    assert repo.config.mode == "off" and paper.accounts == [OVERNIGHT_SHADOW_ACCOUNT_ID]
