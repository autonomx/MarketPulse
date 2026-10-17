"""The premarket handoff import is explicit and off by default (WP-8.3)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.apps.trading import prospective_gap_inputs as inputs_module
from app.apps.trading.prospective_gap_runtime import ProspectiveGapRuntime


def test_the_scheduled_github_import_runs_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OMNIX_TRADING_PROSPECTIVE_GAP_HANDOFF_IMPORT", raising=False)
    assert inputs_module.handoff_import_task(None).task_id == "trading.prospective_gap_handoff_import"
    monkeypatch.setenv("OMNIX_TRADING_PROSPECTIVE_GAP_HANDOFF_IMPORT", "0")
    assert inputs_module.handoff_import_task(None) is None


class _Inputs:
    def __init__(self, existing=None, climatology=None) -> None:
        self.existing = existing
        self.climatology = climatology
        self.imported = []
        self.climatology_imported = []

    def handoff(self, session_date):
        return self.existing

    def climatology_before(self, session_date):
        return self.climatology

    def import_handoff(self, content, *, source, imported_by=None):
        self.imported.append((content, source, imported_by))

    def import_climatology(self, content, *, source):
        self.climatology_imported.append(source)


def test_the_import_runs_only_in_the_premarket_window_and_only_once(monkeypatch) -> None:
    fetched = []
    monkeypatch.setattr(
        inputs_module,
        "fetch_handoff_from_github",
        lambda session_date: fetched.append(session_date) or ("{}", "github:test"),
    )
    monkeypatch.setattr(inputs_module, "fetch_from_github", lambda path: None)
    after_open = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)  # 10:00 ET
    premarket = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # 08:00 ET

    assert inputs_module.import_todays_handoff_from_github(_Inputs(), now=lambda: after_open) is False
    assert inputs_module.import_todays_handoff_from_github(_Inputs(existing=object()), now=lambda: premarket) is False
    assert fetched == []

    store = _Inputs()
    assert inputs_module.import_todays_handoff_from_github(store, now=lambda: premarket) is True
    assert store.imported == [("{}", "github:test", "trading.prospective_gap_handoff_import")]


def test_the_runtime_no_longer_fetches_from_github_or_reads_input_files() -> None:
    assert not hasattr(ProspectiveGapRuntime, "_fetch_scheduler_handoff_from_github")
    assert not hasattr(ProspectiveGapRuntime, "_load_climatology_state")


def _climatology(through: str) -> str:
    return '{"through_session": "%s", "observation_count": 10, "positive_count": 4, "probability": "0.4"}' % through


def test_the_published_climatology_is_imported_once_it_is_newer(monkeypatch) -> None:
    from datetime import date

    from app.apps.trading.prospective_gap_runtime import ProspectiveClimatologyState

    monday = date(2026, 10, 5)
    assert inputs_module.previous_session(monday) == date(2026, 10, 2)
    fetches = []

    def fetch(path):
        fetches.append(path)
        return _climatology("2026-10-02"), "github:test"

    stale = ProspectiveClimatologyState.model_validate_json(_climatology("2026-10-01"))
    store = _Inputs(climatology=stale)
    assert inputs_module.import_climatology_from_github(store, monday, fetch=fetch) is True
    assert store.climatology_imported == ["github:test"]

    current = ProspectiveClimatologyState.model_validate_json(_climatology("2026-10-02"))
    assert inputs_module.import_climatology_from_github(_Inputs(climatology=current), monday, fetch=fetch) is False
    assert len(fetches) == 1  # up to date: GitHub is not asked

    # A published state older than the stored one is ignored.
    monkeypatch.setattr(inputs_module, "previous_session", lambda session: date(2026, 10, 3))
    store = _Inputs(climatology=current)
    assert inputs_module.import_climatology_from_github(
        store, monday, fetch=lambda path: (_climatology("2026-10-01"), "github:old")
    ) is False
    assert store.climatology_imported == []
