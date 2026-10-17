from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.apps.trading import strategy_ai_shadow_provider as persistence
from app.apps.trading.strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from app.apps.trading.strategy_repository import StrategyEvent


class MemoryRepository:
    def __init__(self, events=None):
        self.events = list(events or [])

    def recent_events(self, strategy_id, limit):
        del strategy_id, limit
        return list(self.events)

    def append_event(self, event):
        self.events.append(event)
        return True


def _health_event(*, state: str, failure_count: int, open_until: datetime | None):
    observed_at = datetime.now(timezone.utc)
    return StrategyEvent(
        strategy_id=MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
        event_id=f"health-{state}-{failure_count}",
        run_id="fixture",
        instrument_id="__provider__",
        event_type="ai_shadow_provider_health",
        state=state,
        reason_code="fixture",
        observed_at=observed_at,
        idempotency_key=f"health-{state}-{failure_count}",
        payload={
            "failure_count": failure_count,
            "backoff_seconds": 300,
            "open_until_utc": open_until.isoformat() if open_until else None,
            "research_only": True,
            "execution_authority": False,
        },
    )


def _enable_persistence(monkeypatch):
    # The Trading workflow globally runs legacy_test persistence. These focused
    # tests use an in-memory repository double and must exercise the production
    # circuit-persistence branch explicitly.
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "postgresql")
    monkeypatch.setenv("OMNIX_TRADING_AI_SHADOW_CIRCUIT_PERSISTENCE", "1")


def test_persistent_circuit_hydrates_open_state_after_process_restart(monkeypatch):
    _enable_persistence(monkeypatch)
    future = datetime.now(timezone.utc) + timedelta(seconds=240)
    repository = MemoryRepository([
        _health_event(state="open", failure_count=2, open_until=future)
    ])
    monkeypatch.setattr(
        persistence,
        "default_strategy_repository",
        lambda: repository,
    )

    circuit = persistence.PersistentCircuitState()

    assert circuit.is_open(1000.0) is True
    assert circuit.failure_count == 2
    assert 230 <= circuit.retry_after(1000.0) <= 240


def test_persistent_circuit_records_open_and_recovery_without_authority(monkeypatch):
    _enable_persistence(monkeypatch)
    repository = MemoryRepository()
    monkeypatch.setattr(
        persistence,
        "default_strategy_repository",
        lambda: repository,
    )
    circuit = persistence.PersistentCircuitState()
    circuit._hydrated = True

    delay = circuit.trip(100.0)

    assert delay == 120
    opened = repository.events[-1]
    assert opened.event_type == "ai_shadow_provider_health"
    assert opened.state == "open"
    assert opened.payload["failure_count"] == 1
    assert opened.payload["execution_authority"] is False
    assert opened.payload["research_only"] is True

    circuit.success()

    recovered = repository.events[-1]
    assert recovered.state == "closed"
    assert recovered.reason_code == "AI_SHADOW_PROVIDER_CIRCUIT_RECOVERED"
    assert recovered.payload["execution_authority"] is False
