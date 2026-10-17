from __future__ import annotations

from pathlib import Path


def test_alert_lifecycle_migration_preserves_observation_history_only_for_lifecycle_edits() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0027_trading_alert_lifecycle_history.sql"
    ).read_text(encoding="utf-8")
    assert "BEFORE UPDATE ON omnix_trading_alerts" in migration
    assert "NEW.enabled" not in migration
    assert "NEW.expires_at" not in migration
    assert "NEW.cooldown_seconds" not in migration
    for authoritative_condition_field in (
        "instrument_id",
        "binding_id",
        "condition_type",
        "threshold",
        "condition_parameters",
        "evaluation_policy",
    ):
        assert f"NEW.{authoritative_condition_field}" in migration
    for preserved_field in (
        "last_observed_price",
        "last_observed_value",
        "last_triggered_at",
    ):
        assert f"NEW.{preserved_field} := OLD.{preserved_field}" in migration
