from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_dynamic_discovery import (
    CandidateLifecycleState,
    DiscoveryEvent,
    DiscoveryTriggerType,
    DynamicCandidate,
    EvaluationTier,
    INTERDAY_TRADING_STRATEGY_ID,
)
from app.apps.trading.strategy_dynamic_discovery_repository import EVENT_CANDIDATE, EVENT_DISCOVERY
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_shadow_universe import (
    resolve_v2_evidence_archive_for_session,
    resolve_v2_runtime_archive,
    resolve_v2_shadow_archive,
)
from app.apps.trading.strategy_universe_archiver import _archive_universe_id


NOW = datetime(2026, 8, 24, 13, 25, tzinfo=timezone.utc)  # 09:25 ET


class FakeRepository:
    def __init__(self, universes=None, events=None) -> None:
        self.universes = universes or {}
        self.events = list(events or [])
        self.reads: list[str] = []
        self.writes = 0

    def get_universe(self, universe_id: str):
        self.reads.append(universe_id)
        if universe_id not in self.universes:
            raise ValueError("gapper_universe_not_found")
        return self.universes[universe_id]

    def events_by_types_between(
        self,
        strategy_id: str,
        *,
        event_types,
        start_time,
        end_time,
        limit: int = 50_000,
    ):
        rows = [
            event
            for event in self.events
            if event.strategy_id == strategy_id
            and event.event_type in set(event_types)
            and start_time <= event.observed_at < end_time
        ]
        rows.sort(key=lambda event: (event.observed_at, event.event_id))
        return rows[:limit]


def _config(
    *,
    mode: str = "shadow",
    version: str = "2.0.0",
    active: str | None = None,
    strategy_id: str = "prospective-v2",
    parent_strategy_id: str | None = None,
):
    config = GapPullbackConfig(
        strategy_version=version,
        structure_interval="1m" if version == "2.0.0" else "5m",
        execution_interval="1m",
    )
    return TradingStrategyConfigDocument(
        strategy_id=strategy_id,
        parent_strategy_id=parent_strategy_id,
        account_id="paper-1",
        strategy_kind="gap_pullback_v1",
        strategy_version=version,
        mode=mode,
        active_universe_id=active,
        enabled=True,
        config=config,
        risk=StrategyRiskProfile(),
    )


def _candidate(instrument_id: str, *, rank: int) -> GapperCandidate:
    return GapperCandidate(
        instrument_id=instrument_id,
        observed_at=NOW,
        previous_close=Decimal("5"),
        premarket_price=Decimal("7"),
        gap_pct=Decimal("40"),
        premarket_volume=Decimal("1000000"),
        premarket_dollar_volume=Decimal("7000000"),
        discovery_rank=rank,
    )


def test_v2_shadow_resolves_today_raw_archive_without_mutating_config() -> None:
    config = _config()
    universe_id = _archive_universe_id(config, NOW.astimezone())
    snapshot = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=NOW.astimezone().date(),
        evaluation_time=NOW,
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository({universe_id: snapshot})

    resolved = resolve_v2_shadow_archive(config, repository, now=NOW)

    assert resolved == snapshot
    assert repository.reads == [universe_id]
    assert repository.writes == 0
    assert config.active_universe_id is None


def test_v2_shadow_waits_when_archive_is_not_ready() -> None:
    repository = FakeRepository()
    assert resolve_v2_shadow_archive(_config(), repository, now=NOW) is None
    assert len(repository.reads) == 1


def test_shadow_archive_fallback_never_applies_to_auto_paper_or_explicit_universe() -> None:
    repository = FakeRepository()
    assert resolve_v2_shadow_archive(_config(mode="auto_paper"), repository, now=NOW) is None
    assert resolve_v2_shadow_archive(_config(active="selected-universe"), repository, now=NOW) is None
    assert repository.reads == []


def test_v2_evidence_archive_remains_read_only_after_auto_paper_promotion() -> None:
    config = _config(mode="auto_paper", active="selected-universe")
    universe_id = _archive_universe_id(config, NOW.astimezone())
    snapshot = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=NOW.astimezone().date(),
        evaluation_time=NOW,
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository({universe_id: snapshot})

    resolved = resolve_v2_evidence_archive_for_session(
        config,
        repository,
        session_date=NOW.astimezone().date(),
    )

    assert resolved == snapshot
    assert repository.reads == [universe_id]
    assert repository.writes == 0
    assert config.active_universe_id == "selected-universe"


def test_v2_runtime_archive_allows_already_promoted_auto_paper_without_attaching_universe() -> None:
    config = _config(mode="auto_paper")
    universe_id = _archive_universe_id(config, NOW.astimezone())
    snapshot = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=NOW.astimezone().date(),
        evaluation_time=NOW,
        discovery_source="provider",
        candidates=[],
        allow_empty=True,
    )
    repository = FakeRepository({universe_id: snapshot})

    resolved = resolve_v2_runtime_archive(config, repository, now=NOW)

    assert resolved == snapshot
    assert repository.reads == [universe_id]
    assert repository.writes == 0
    assert config.active_universe_id is None


def test_v2_runtime_archive_does_not_override_explicit_auto_paper_universe() -> None:
    repository = FakeRepository()
    config = _config(mode="auto_paper", active="operator-selected")

    assert resolve_v2_runtime_archive(config, repository, now=NOW) is None
    assert repository.reads == []


def test_dynamic_tier_ab_candidate_is_added_in_shadow_but_never_auto_paper() -> None:
    session_date = NOW.date()
    frozen_candidate = _candidate("equity:NASDAQ:FROZEN", rank=1)
    dynamic_candidate = _candidate("equity:NASDAQ:DYNAMIC", rank=2)

    shadow_config = _config(strategy_id=INTERDAY_TRADING_STRATEGY_ID, mode="shadow")
    auto_config = _config(strategy_id=INTERDAY_TRADING_STRATEGY_ID, mode="auto_paper")
    universe_id = _archive_universe_id(shadow_config, NOW.astimezone())
    frozen = freeze_gapper_universe(
        universe_id=universe_id,
        session_date=session_date,
        evaluation_time=NOW,
        discovery_source="provider",
        candidates=[frozen_candidate],
    )

    discovery = DiscoveryEvent(
        event_id="discovery-dynamic-000000000000001",
        session_date=session_date,
        instrument_id=dynamic_candidate.instrument_id,
        discovered_at=NOW,
        trigger_type=DiscoveryTriggerType.MARKET_ANOMALY,
        source="fixture",
        causal_as_of=NOW,
        attention_score=90,
        payload={"candidate": dynamic_candidate.model_dump(mode="json")},
    )
    state = DynamicCandidate(
        session_date=session_date,
        instrument_id=dynamic_candidate.instrument_id,
        first_seen_at=NOW,
        discovered_at=NOW,
        last_observed_at=NOW,
        lifecycle=CandidateLifecycleState.ACTIVE,
        tier=EvaluationTier.A,
        trigger_types=(DiscoveryTriggerType.MARKET_ANOMALY,),
        attention_score=90,
        common_priority=90,
    )
    events = [
        StrategyEvent(
            strategy_id=INTERDAY_TRADING_STRATEGY_ID,
            event_id=discovery.event_id,
            instrument_id=dynamic_candidate.instrument_id,
            event_type=EVENT_DISCOVERY,
            state=DiscoveryTriggerType.MARKET_ANOMALY.value,
            observed_at=NOW,
            idempotency_key="discovery-dynamic",
            payload=discovery.model_dump(mode="json"),
        ),
        StrategyEvent(
            strategy_id=INTERDAY_TRADING_STRATEGY_ID,
            event_id="candidate-dynamic-000000000000001",
            instrument_id=dynamic_candidate.instrument_id,
            event_type=EVENT_CANDIDATE,
            state=CandidateLifecycleState.ACTIVE.value,
            observed_at=NOW,
            idempotency_key="candidate-dynamic",
            payload=state.model_dump(mode="json"),
        ),
    ]
    repository = FakeRepository({universe_id: frozen}, events=events)

    shadow = resolve_v2_runtime_archive(shadow_config, repository, now=NOW)
    auto = resolve_v2_runtime_archive(auto_config, repository, now=NOW)

    assert shadow is not None
    assert [candidate.instrument_id for candidate in shadow.candidates] == [
        "equity:NASDAQ:FROZEN",
        "equity:NASDAQ:DYNAMIC",
    ]
    assert auto == frozen
    assert [candidate.instrument_id for candidate in auto.candidates] == ["equity:NASDAQ:FROZEN"]
