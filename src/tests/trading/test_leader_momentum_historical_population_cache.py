from datetime import date, datetime, timezone
from decimal import Decimal

from scripts.trade.build_leader_momentum_historical_population_cache import (
    DailyClose,
    build_session_manifest,
    normalize_asset_payload,
    queryable_seed_symbols,
)


def test_normalize_asset_payload_retains_inactive_and_normalizes_nysearca() -> None:
    assets = normalize_asset_payload(
        [
            {"symbol": "OLD", "exchange": "NYSEARCA", "class": "us_equity", "status": "inactive", "id": "2"},
            {"symbol": "NEW", "exchange": "NASDAQ", "class": "us_equity", "status": "active", "id": "1"},
            {"symbol": "OTC", "exchange": "OTC", "class": "us_equity", "status": "active", "id": "3"},
        ]
    )

    assert [item["symbol"] for item in assets] == ["NEW", "OLD"]
    assert assets[1]["exchange"] == "ARCA"
    assert assets[1]["status"] == "inactive"


def test_queryable_seed_keeps_inactive_common_stocks_but_excludes_legacy_products() -> None:
    assets = normalize_asset_payload(
        [
            {"symbol": "DEAD", "exchange": "NASDAQ", "class": "us_equity", "status": "inactive", "id": "1", "name": "Former Corp"},
            {"symbol": "DEADW", "exchange": "NASDAQ", "class": "us_equity", "status": "inactive", "id": "2", "name": "Former Corp Warrant"},
            {"symbol": "0029900E0", "exchange": "NASDAQ", "class": "us_equity", "status": "inactive", "id": "3", "name": "Legacy Contra"},
            {"symbol": "LIVE", "exchange": "NASDAQ", "class": "us_equity", "status": "active", "id": "4", "name": "Live Warrant"},
        ]
    )

    assert queryable_seed_symbols(assets) == ("DEAD", "LIVE")


def test_build_session_manifest_uses_only_prior_closes_and_no_winner_input() -> None:
    session = date(2026, 8, 3)
    assets = [
        {"symbol": "AAA", "exchange": "NASDAQ", "status": "inactive"},
        {"symbol": "BBB", "exchange": "NYSE", "status": "active"},
        {"symbol": "CCC", "exchange": "NASDAQ", "status": "active"},
    ]
    closes = [
        DailyClose("AAA", datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc), date(2026, 7, 31), Decimal("10")),
        DailyClose("BBB", datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc), date(2026, 8, 3), Decimal("20")),
        DailyClose("CCC", datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc), date(2026, 7, 31), Decimal("30")),
    ]

    manifest, envelope = build_session_manifest(
        session_date=session,
        assets=assets,
        closes=closes,
        asset_seed_fingerprint="seed",
    )

    assert manifest.point_in_time is True
    assert manifest.outcome_conditioned is False
    assert manifest.instrument_ids == ("equity:US:AAA", "equity:US:CCC")
    assert envelope["previous_closes"]["AAA"]["close"] == "10"
    assert "winner_labels_used" not in envelope
