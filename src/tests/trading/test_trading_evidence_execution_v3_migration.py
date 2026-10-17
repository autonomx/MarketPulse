from pathlib import Path


def test_v3_migration_persists_binding_authority_trigger_plans_and_manifests():
    migration = Path(
        "src/app/apps/trading/migrations/0083_trading_evidence_execution_v3.sql"
    ).read_text(encoding="utf-8")

    for token in (
        "binding_purpose",
        "'quarantined'",
        "omnix_trading_trigger_plans",
        "TRIGGER_ORDER_UNRESOLVED",
        "omnix_trading_session_evidence_manifests",
        "PERMANENTLY_UNSCORABLE",
    ):
        assert token in migration


def test_v3_follow_up_migration_repairs_late_binding_and_strategy_changes():
    migration = Path(
        "src/app/apps/trading/migrations/0089_trading_evidence_execution_v3_checksum_repair.sql"
    ).read_text(encoding="utf-8")

    for token in (
        "omnix_trading_strategy_protections_status_check",
        "ibkr:%",
        "binding_purpose = 'LIVE_DATA'",
        "binding_purpose_not_execution",
    ):
        assert token in migration
