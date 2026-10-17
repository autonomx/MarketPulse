from datetime import datetime, timedelta, timezone

from app.apps.trading import strategy_ai_shadow_monitor as monitor


def test_data_gap_heartbeat_cache_is_bounded_expiring_and_invalidatable(monkeypatch) -> None:
    monitor.clear_data_gap_heartbeat_cache()
    monkeypatch.setattr(monitor, "MAX_DATA_GAP_HEARTBEATS", 1)
    monkeypatch.setattr(monitor, "DATA_GAP_HEARTBEAT_TTL_SECONDS", 5.0)
    now = {"value": 10.0}
    monkeypatch.setattr(monitor._clock, "monotonic", lambda: now["value"])
    first = ("strategy", "instrument-1", "gap", "missing", "policy")
    second = ("strategy", "instrument-2", "gap", "missing", "policy")
    observed = datetime(2026, 9, 30, tzinfo=timezone.utc)

    assert monitor._allow_data_gap_heartbeat(first, observed) is True
    assert monitor._allow_data_gap_heartbeat(first, observed + timedelta(minutes=1)) is False
    assert monitor._allow_data_gap_heartbeat(second, observed) is True
    assert list(monitor._GAP_LAST_EMITTED) == [second]

    now["value"] = 16.0
    assert monitor._allow_data_gap_heartbeat(first, observed) is True
    monitor.clear_data_gap_heartbeat_cache()
    assert not monitor._GAP_LAST_EMITTED
