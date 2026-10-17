from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.apps.trading.strategy_ai_shadow_v2_monitor import _research_selection_is_due
from app.apps.trading.strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from app.apps.trading.strategy_research_monitor import _ai_shadow_v2_owns_research


ET = ZoneInfo("America/New_York")


def _candidate(observed_at: datetime | None):
    return SimpleNamespace(observed_at=observed_at)


def test_legacy_research_monitor_does_not_research_interday_profile() -> None:
    assert _ai_shadow_v2_owns_research(
        SimpleNamespace(strategy_id=MANAGED_FINVIZ_SHADOW_STRATEGY_ID)
    ) is True
    assert _ai_shadow_v2_owns_research(SimpleNamespace(strategy_id="other-strategy")) is False


def test_premarket_candidate_is_researched_once() -> None:
    selected = datetime(2026, 9, 17, 9, 20, tzinfo=ET)
    first_run = datetime(2026, 9, 17, 9, 25, tzinfo=ET)
    later_run = datetime(2026, 9, 17, 10, 0, tzinfo=ET)

    assert _research_selection_is_due(
        candidate=_candidate(selected), last_refresh=None, now=first_run
    ) is True
    assert _research_selection_is_due(
        candidate=_candidate(selected), last_refresh=object(), now=later_run
    ) is False


def test_new_intraday_candidate_is_researched_at_selection() -> None:
    selected = datetime(2026, 9, 17, 10, 0, tzinfo=ET)
    first_run = datetime(2026, 9, 17, 10, 1, tzinfo=ET)

    assert _research_selection_is_due(
        candidate=_candidate(selected), last_refresh=None, now=first_run
    ) is True


def test_premarket_candidate_not_seen_until_regular_session_is_not_intraday_new() -> None:
    selected = datetime(2026, 9, 17, 9, 20, tzinfo=ET)
    late_run = datetime(2026, 9, 17, 10, 0, tzinfo=ET)

    assert _research_selection_is_due(
        candidate=_candidate(selected), last_refresh=None, now=late_run
    ) is False


def test_research_is_closed_after_last_entry_boundary() -> None:
    selected = datetime(2026, 9, 17, 15, 31, tzinfo=ET)
    late_run = datetime(2026, 9, 17, 15, 35, tzinfo=ET)

    assert _research_selection_is_due(
        candidate=_candidate(selected), last_refresh=None, now=late_run
    ) is False
