from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .catalyst_discovery import discover_yahoo_catalyst_headlines
from .catalyst_repository import TradingCatalystRepository
from .gapper_dataset import GapperUniverseSnapshot, freeze_gapper_universe
from .finviz_gapper_discovery import (
    FINVIZ_ATOMIC_SOURCE_LOCATOR,
    discover_finviz_gappers,
)
from .gapper_discovery import discover_yahoo_gappers
from .providers.errors import ProviderDataUnavailableError
from .strategy_data_integrity import assess_universe_integrity
from .strategy_evaluability import assess_session_evaluability
from .strategy_repository import TradingStrategyConfigDocument, TradingStrategyRepository
from .trade_logging import trade_log
from .us_equity_calendar import regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time




def _archive_universe_id(config: TradingStrategyConfigDocument, now_et: datetime) -> str:
    digest = hashlib.sha256(config.strategy_id.encode("utf-8")).hexdigest()[:10]
    return (
        f"auto-archive-{now_et.date().isoformat()}-"
        f"{config.config.universe_scan_time_et.strftime('%H%M')}-"
        f"{config.config.universe_discovery_source}-{digest}"
    )


def _enrich_finviz_catalysts(
    snapshot: GapperUniverseSnapshot,
    repository: TradingStrategyRepository,
    *,
    catalyst_repository: TradingCatalystRepository | None = None,
    catalyst_discovery: Callable[..., tuple] = discover_yahoo_catalyst_headlines,
) -> tuple[GapperUniverseSnapshot, int, dict[str, str]]:
    """Attach current Yahoo headline evidence before the immutable archive is saved."""

    if snapshot.discovery_source != "finviz" or not snapshot.candidates:
        return snapshot, 0, {}

    active_repository = catalyst_repository
    if active_repository is None:
        context = getattr(repository, "context", None)
        if context is None:
            return snapshot, 0, {}
        active_repository = TradingCatalystRepository(
            context=context,
            uow_factory=getattr(repository, "uow_factory"),
        )

    enriched = []
    evidence_count = 0
    errors: dict[str, str] = {}
    for candidate in snapshot.candidates:
        symbol = candidate.instrument_id.split(":")[-1]
        captured_at = datetime.now(timezone.utc)
        try:
            evidence = catalyst_discovery(
                instrument_id=candidate.instrument_id,
                symbol=symbol,
                evaluation_time=captured_at,
                lookback_hours=72,
                max_items=5,
            )
        except Exception as exc:
            errors[candidate.instrument_id] = f"{type(exc).__name__}: {exc}"
            evidence = ()

        persisted_evidence = []
        for item in evidence:
            try:
                active_repository.save_evidence(item)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                previous = errors.get(candidate.instrument_id)
                errors[candidate.instrument_id] = (
                    f"{previous}; evidence_save={detail}"
                    if previous
                    else f"evidence_save={detail}"
                )
                continue
            persisted_evidence.append(item)

        evidence_count += len(persisted_evidence)
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *candidate.catalyst_evidence_ids,
                    *(item.evidence_id for item in persisted_evidence),
                )
            )
        )
        dilution_flags = tuple(
            sorted(
                set(candidate.dilution_flags).union(
                    *(set(item.dilution_flags) for item in persisted_evidence)
                )
            )
        )
        evidence_times = dict(candidate.evidence_observed_at)
        for item in persisted_evidence:
            evidence_times[f"catalyst:{item.evidence_id}"] = item.captured_at
        enriched.append(
            candidate.model_copy(
                update={
                    "catalyst_evidence_ids": evidence_ids,
                    "dilution_flags": dilution_flags,
                    "evidence_observed_at": evidence_times,
                }
            )
        )

    freeze_time = datetime.now(timezone.utc)
    return (
        freeze_gapper_universe(
            universe_id=snapshot.universe_id,
            session_date=snapshot.session_date,
            evaluation_time=freeze_time,
            discovery_source="finviz",
            source_locator=snapshot.source_locator,
            source_candidate_symbols=snapshot.source_candidate_symbols,
            source_member_dispositions=snapshot.source_member_dispositions,
            candidates=enriched,
            allow_empty=not enriched,
        ),
        evidence_count,
        errors,
    )


def archive_daily_universe_if_due(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    now: datetime | None = None,
    catalyst_repository: TradingCatalystRepository | None = None,
    catalyst_discovery: Callable[..., tuple] = discover_yahoo_catalyst_headlines,
    allow_late_recovery: bool = False,
) -> GapperUniverseSnapshot | None:
    """Create one configured morning archive, with explicit startup recovery.

    Normal polling is strictly limited to the configured scan/grace window. A
    caller may opt into ``allow_late_recovery`` during application startup; only
    Finviz archives may then be reconstructed later in the same regular session.
    Their real evaluation timestamp is preserved, so integrity assessment marks
    them non-preopen/non-prospective and they can never qualify AUTO PAPER.

    Archival is evidence-only: it never changes ``active_universe_id`` and cannot
    authorize a trade. Source members and their dispositions are immutable so a
    later provider failure cannot be mistaken for a legitimate source filter.
    """

    # Kinds without a daily gapper universe (e.g. overnight_shadow_v1) never archive one.
    if not config.enabled or not getattr(config.config, "auto_archive_daily_universe", False):
        return None
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("strategy universe archive clock must be timezone-aware")
    now_et = observed.astimezone(_ET)
    if now_et.weekday() >= 5 or now_et.date() in regular_holidays(now_et.year):
        return None
    scan_start = datetime.combine(
        now_et.date(),
        config.config.universe_scan_time_et,
        tzinfo=_ET,
    )
    scan_end = scan_start + timedelta(minutes=config.config.universe_archive_grace_minutes)
    if now_et < scan_start:
        return None
    late_recovery = now_et > scan_end
    if late_recovery:
        if (
            not allow_late_recovery
            or config.config.universe_discovery_source != "finviz"
            or now_et.time() >= regular_close_time(now_et.date())
        ):
            return None

    universe_id = _archive_universe_id(config, now_et)
    try:
        repository.get_universe(universe_id)
    except ValueError as exc:
        if str(exc) != "gapper_universe_not_found":
            raise
    else:
        return None

    discovery_source = config.config.universe_discovery_source
    discover = discover_finviz_gappers if discovery_source == "finviz" else discover_yahoo_gappers
    try:
        discovery_kwargs = {
            "universe_id": universe_id,
            "evaluation_time": observed.astimezone(timezone.utc),
            "count": config.config.universe_discovery_count,
            "minimum_gap_pct": config.config.minimum_gap_pct,
            "minimum_price": config.config.minimum_price,
            "maximum_price": config.config.maximum_price,
        }
        if discovery_source == "finviz" and config.config.strategy_version == "2.0.0":
            discovery_kwargs["membership_only"] = True
        snapshot = discover(**discovery_kwargs)
    except ProviderDataUnavailableError as exc:
        if discovery_source == "finviz":
            raise
        if "no qualifying listed equities" not in str(exc).lower():
            raise
        snapshot = freeze_gapper_universe(
            universe_id=universe_id,
            session_date=now_et.date(),
            evaluation_time=observed.astimezone(timezone.utc),
            discovery_source="provider",
            candidates=[],
            allow_empty=True,
        )

    catalyst_evidence_count = 0
    catalyst_errors: dict[str, str] = {}
    if discovery_source == "finviz":
        snapshot, catalyst_evidence_count, catalyst_errors = _enrich_finviz_catalysts(
            snapshot,
            repository,
            catalyst_repository=catalyst_repository,
            catalyst_discovery=catalyst_discovery,
        )

    integrity = assess_universe_integrity(snapshot)
    evaluability = assess_session_evaluability(snapshot, config.config)
    saved = repository.save_universe(snapshot)
    disposition_counts: dict[str, int] = {}
    for item in saved.source_member_dispositions:
        disposition_counts[item.status] = disposition_counts.get(item.status, 0) + 1
    trade_log(
        "auto_trading",
        "daily_universe_archived",
        strategy_id=config.strategy_id,
        universe_id=saved.universe_id,
        session_date=saved.session_date,
        evaluation_time=saved.evaluation_time,
        source_fingerprint=saved.source_fingerprint,
        candidate_count=len(saved.candidates),
        zero_candidate_scan=evaluability.status == "zero_candidate_scan",
        source_candidate_count=len(saved.source_candidate_symbols),
        source_member_disposition_counts=disposition_counts,
        source_member_failure_count=integrity.source_member_failure_count,
        source_members_accounted=integrity.source_members_accounted,
        catalyst_evidence_count=catalyst_evidence_count,
        catalyst_capture_errors=catalyst_errors,
        capture_on_time=integrity.capture_on_time,
        cohort_complete=integrity.cohort_complete,
        cohort_integrity=integrity.cohort_integrity,
        market_data_complete=integrity.market_data_complete,
        prospective_eligible=integrity.prospective_eligible,
        integrity_reason_codes=integrity.reason_codes,
        session_evaluability=evaluability.model_dump(mode="json"),
        configured_scan_time_et=config.config.universe_scan_time_et,
        configured_discovery_source=config.config.universe_discovery_source,
        configured_discovery_count=config.config.universe_discovery_count,
        grace_minutes=config.config.universe_archive_grace_minutes,
        late_recovery=late_recovery,
        recovery_reason=(
            "startup_after_archive_window" if late_recovery else None
        ),
        execution_authority=False,
    )
    return saved


__all__ = ["archive_daily_universe_if_due"]
