from __future__ import annotations

from datetime import datetime, timezone

from app.apps.trading.gapper_dataset import freeze_gapper_universe
from app.apps.trading.market_evidence import SourceMemberDisposition
from app.apps.trading.strategies.models import StrategyRiskProfile
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_universe_archiver import _archive_universe_id
from app.apps.trading.strategy_v2_qualification import (
    frozen_v2_config,
    managed_finviz_v2_config,
    v2_profile_fingerprint,
)
from app.apps.trading.strategy_v2_qualification_monitor import replay_v2_shadow_session


SESSION_NOW = datetime(2026, 8, 24, 20, 30, tzinfo=timezone.utc)  # after 16:00 ET + grace


class FakeRepository:
    def __init__(self, universe) -> None:
        self.universe = universe
        self.events: list[StrategyEvent] = []
        self.writes = 0

    def get_universe(self, universe_id: str):
        if universe_id != self.universe.universe_id:
            raise ValueError("gapper_universe_not_found")
        return self.universe

    def recent_events(self, strategy_id: str, limit: int = 200):
        assert strategy_id == "v2-prospective"
        return list(reversed(self.events[-limit:]))

    def append_event(self, event: StrategyEvent) -> bool:
        if any(existing.idempotency_key == event.idempotency_key for existing in self.events):
            return False
        self.events.append(event)
        self.writes += 1
        return True


def _strategy(
    *,
    mode: str = "shadow",
    active_universe_id: str | None = None,
    config=None,
):
    config = config or frozen_v2_config()
    return TradingStrategyConfigDocument(
        strategy_id="v2-prospective",
        account_id="paper-1",
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode=mode,
        active_universe_id=active_universe_id,
        config=config,
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def test_post_session_zero_candidate_scan_is_terminal_but_not_promotion_evidence() -> None:
    strategy = _strategy()
    universe_id = _archive_universe_id(strategy, SESSION_NOW.astimezone())
    universe = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=SESSION_NOW.astimezone().date(),
        evaluation_time=datetime(2026, 8, 24, 13, 20, tzinfo=timezone.utc),
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository(universe)

    result = replay_v2_shadow_session(
        strategy,
        repository,
        universe.session_date,
        observed_at=SESSION_NOW,
        bar_loader=lambda candidates, session_date: {},
    )

    assert result is None
    assert repository.writes == 1
    session_event = repository.events[0]
    assert session_event.event_type == "v2_shadow_replay_session"
    assert session_event.payload["status"] == "zero_candidate_scan"
    assert session_event.payload["qualification_eligible"] is False
    assert session_event.payload["universe_source"] == "auto_archive_shadow"
    assert session_event.payload["execution_authority"] is False

    second = replay_v2_shadow_session(
        strategy,
        repository,
        universe.session_date,
        observed_at=SESSION_NOW,
        bar_loader=lambda candidates, session_date: {},
    )
    assert second is None
    assert repository.writes == 1


def test_post_session_zero_candidate_scan_remains_nonqualifying_after_auto_paper_promotion() -> None:
    strategy = _strategy(mode="auto_paper", active_universe_id="selected-universe")
    universe_id = _archive_universe_id(strategy, SESSION_NOW.astimezone())
    universe = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=SESSION_NOW.astimezone().date(),
        evaluation_time=datetime(2026, 8, 24, 13, 20, tzinfo=timezone.utc),
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository(universe)

    result = replay_v2_shadow_session(
        strategy,
        repository,
        universe.session_date,
        observed_at=SESSION_NOW,
        bar_loader=lambda candidates, session_date: {},
    )

    assert result is None
    assert repository.writes == 1
    event = repository.events[0]
    assert event.event_type == "v2_shadow_replay_session"
    assert event.payload["status"] == "zero_candidate_scan"
    assert event.payload["qualification_eligible"] is False
    assert event.payload["execution_authority"] is False
    assert strategy.active_universe_id == "selected-universe"


def test_post_session_replay_refuses_noncanonical_v2_profile() -> None:
    strategy = _strategy()
    strategy = strategy.model_copy(
        update={
            "config": strategy.config.model_copy(update={"v2_maximum_l2_to_signal_minutes": 9}),
        }
    )
    universe_id = _archive_universe_id(strategy, SESSION_NOW.astimezone())
    universe = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=SESSION_NOW.astimezone().date(),
        evaluation_time=datetime(2026, 8, 24, 13, 20, tzinfo=timezone.utc),
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository(universe)

    assert replay_v2_shadow_session(
        strategy,
        repository,
        universe.session_date,
        observed_at=SESSION_NOW,
        bar_loader=lambda candidates, session_date: {},
    ) is None
    assert repository.writes == 0


def test_post_session_exact_managed_finviz_profile_records_truthful_zero_scan() -> None:
    strategy = _strategy(config=managed_finviz_v2_config())
    marker = datetime.combine(
        SESSION_NOW.astimezone().date(),
        strategy.config.universe_scan_time_et,
        tzinfo=SESSION_NOW.astimezone().tzinfo,
    )
    universe_id = _archive_universe_id(strategy, marker)
    universe = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=SESSION_NOW.astimezone().date(),
        evaluation_time=datetime(2026, 8, 24, 13, 15, tzinfo=timezone.utc),
        discovery_source="finviz",
        source_locator="https://finviz.com/screener#omnix-atomic-first-page-v1",
        source_candidate_symbols=("TEST",),
        source_member_dispositions=(
            SourceMemberDisposition(
                symbol="TEST",
                source_rank=1,
                status="filtered_gap",
                reason_codes=("GAP_BELOW_MINIMUM",),
            ),
        ),
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository(universe)

    result = replay_v2_shadow_session(
        strategy,
        repository,
        universe.session_date,
        observed_at=SESSION_NOW,
        bar_loader=lambda candidates, session_date: {},
    )

    assert result is None
    assert repository.writes == 1
    event = repository.events[0]
    assert event.payload["status"] == "zero_candidate_scan"
    assert event.payload["qualification_eligible"] is False
    assert event.payload["profile_fingerprint"] == v2_profile_fingerprint(strategy.config)
