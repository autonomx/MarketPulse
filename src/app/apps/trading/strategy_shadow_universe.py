from __future__ import annotations

from datetime import date, datetime, timezone

from .strategy_dynamic_discovery import INTERDAY_TRADING_STRATEGY_ID
from .strategy_repository import TradingStrategyConfigDocument, TradingStrategyRepository
from .strategy_universe_archiver import _archive_universe_id
from app.apps.trading.us_equity_calendar import EASTERN as _ET


def _is_interday_group(config: TradingStrategyConfigDocument) -> bool:
    return config.strategy_id == INTERDAY_TRADING_STRATEGY_ID or config.parent_strategy_id == INTERDAY_TRADING_STRATEGY_ID


def _dynamic_shadow_union(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    frozen,
    *,
    session_date: date,
    observed_at: datetime | None = None,
):
    """Compose the persisted, causal discovery candidates into the SHADOW view."""

    from .strategy_dynamic_discovery_quality import _union_refined

    return _union_refined(
        config,
        repository,
        frozen,
        session_date=session_date,
        observed_at=observed_at,
    )


def resolve_v2_evidence_archive_for_session(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    session_date: date,
):
    """Return the immutable strategy-owned V2 raw archive for qualification evidence.

    This resolver is deliberately read-only and independent of ``active_universe_id``.
    It may be used while V2 is in SHADOW or AUTO PAPER so post-session evidence keeps
    accumulating after promotion. It never attaches the archive to the strategy and
    therefore cannot grant order authority.
    """

    if config.mode not in {"shadow", "auto_paper"} or config.config.strategy_version != "2.0.0":
        return None

    marker = datetime.combine(session_date, config.config.universe_scan_time_et, tzinfo=_ET)
    universe_id = _archive_universe_id(config, marker)
    try:
        snapshot = repository.get_universe(universe_id)
    except ValueError as exc:
        if str(exc) == "gapper_universe_not_found":
            return None
        raise
    if snapshot.session_date != session_date:
        return None
    return snapshot


def resolve_v2_shadow_archive_for_session(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    session_date: date,
):
    """Return frozen + live discovery candidates only for V2 SHADOW evaluation."""

    if config.mode != "shadow" or config.active_universe_id is not None:
        return None
    frozen = resolve_v2_evidence_archive_for_session(
        config,
        repository,
        session_date=session_date,
    )
    return _dynamic_shadow_union(config, repository, frozen, session_date=session_date)


def resolve_v2_runtime_archive(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    now: datetime | None = None,
):
    """Return today's runtime archive without broadening AUTO PAPER authority.

    SHADOW interday strategies receive the frozen benchmark plus causally admitted
    live Tier A/B candidates. AUTO PAPER receives only the immutable frozen archive
    that was used for qualification.
    """

    if config.active_universe_id is not None:
        return None
    if config.mode not in {"shadow", "auto_paper"}:
        return None
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("runtime archive clock must be timezone-aware")
    session_date = observed.astimezone(_ET).date()
    frozen = resolve_v2_evidence_archive_for_session(
        config,
        repository,
        session_date=session_date,
    )
    if config.mode == "shadow":
        return _dynamic_shadow_union(
            config,
            repository,
            frozen,
            session_date=session_date,
            observed_at=observed,
        )
    return frozen


def resolve_stoch_rsi_5m_runtime_archive(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    now: datetime | None = None,
):
    """Return frozen + live discovery candidates for shadow-only Stoch RSI."""

    if (
        config.strategy_kind != "stoch_rsi_5m_v1"
        or config.mode != "shadow"
        or config.active_universe_id is not None
    ):
        return None
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("stoch-rsi-5min archive clock must be timezone-aware")
    session_date = observed.astimezone(_ET).date()
    marker = datetime.combine(session_date, config.config.universe_scan_time_et, tzinfo=_ET)
    universe_id = _archive_universe_id(config, marker)
    try:
        frozen = repository.get_universe(universe_id)
    except ValueError as exc:
        if str(exc) == "gapper_universe_not_found":
            return None
        raise
    if frozen.session_date != session_date:
        return None
    return _dynamic_shadow_union(
        config,
        repository,
        frozen,
        session_date=session_date,
        observed_at=observed,
    )


def resolve_v2_shadow_archive(
    config: TradingStrategyConfigDocument,
    repository: TradingStrategyRepository,
    *,
    now: datetime | None = None,
):
    """Return today's frozen + causally discovered universe for V2 SHADOW."""

    if config.mode != "shadow" or config.active_universe_id is not None:
        return None
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("shadow archive clock must be timezone-aware")
    session_date = observed.astimezone(_ET).date()
    frozen = resolve_v2_evidence_archive_for_session(config, repository, session_date=session_date)
    return _dynamic_shadow_union(
        config,
        repository,
        frozen,
        session_date=session_date,
        observed_at=observed,
    )


__all__ = [
    "resolve_v2_evidence_archive_for_session",
    "resolve_v2_runtime_archive",
    "resolve_v2_shadow_archive",
    "resolve_v2_shadow_archive_for_session",
    "resolve_stoch_rsi_5m_runtime_archive",
]
