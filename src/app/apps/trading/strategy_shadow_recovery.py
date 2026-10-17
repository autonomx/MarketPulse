"""Shared, fail-closed market-data recovery for shadow strategy consumers."""

from __future__ import annotations

import logging

import copy
from datetime import time
from types import SimpleNamespace
from typing import Any

from .market_data_recovery import (
    StrategyDataRequirement,
    assess_data_requirement,
    latest_clean_bars,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET

logger = logging.getLogger(__name__)

_REGULAR_OPEN = time(9, 30)


def _copy_response(response: Any, bars: list[Any]):
    if response is None:
        return SimpleNamespace(bars=bars, provenance=None)
    if hasattr(response, "model_copy"):
        return response.model_copy(update={"bars": bars})
    cloned = copy.copy(response)
    setattr(cloned, "bars", bars)
    return cloned


def _empty_response(response: Any):
    return _copy_response(response, [])


def _recovered_response(recovered, *, bars=None):
    selected = list(recovered.bars if bars is None else bars)
    response = recovered.primary_response
    if response is not None:
        return _copy_response(response, selected)
    report = recovered.report
    provenance = SimpleNamespace(
        requested_binding=report.requested_binding,
        resolved_binding=report.resolved_binding,
        fallback_reason=(
            "shared_market_data_recovery"
            if report.recovered_bar_count
            else report.primary_error
        ),
        dataset_fingerprint=report.dataset_fingerprint,
        freshness_mode="fallback" if report.recovered_bar_count else "polled",
        as_of=(selected[-1].end_time if selected else report.as_of),
        received_at=report.as_of,
        cached=False,
        history_complete=not report.unresolved_gaps,
    )
    return SimpleNamespace(bars=selected, provenance=provenance)


def _shared_recovery(proxy, instrument_id, interval, limit, binding_id, cancellation):
    recovery = getattr(proxy._delegate, "recovered_bars", None)
    if not callable(recovery):
        return None
    try:
        return recovery(
            instrument_id,
            interval,
            limit,
            binding_id,
            session_date=proxy._session_date,
            as_of=proxy._observed_at,
            max_primary_attempts=2,
            cancellation=cancellation,
        )
    except Exception:
        logger.debug("suppressed error in %s", "_shared_recovery", exc_info=True)
        return None


def recover_bars_for_requirement(
    proxy,
    instrument_id,
    interval,
    limit=500,
    binding_id=None,
    cancellation=None,
    *,
    requirement: StrategyDataRequirement,
    base_bars,
):
    """Return only bars proven sufficient for a declared strategy dependency."""

    if requirement.interval != interval:
        raise ValueError("strategy data requirement interval must match requested interval")

    recovered = _shared_recovery(
        proxy,
        instrument_id,
        interval,
        limit,
        binding_id,
        cancellation,
    )
    if recovered is None:
        try:
            response = base_bars(
                instrument_id,
                interval,
                limit,
                binding_id,
                cancellation,
            )
        except Exception:
            return _empty_response(None)
        assessment = assess_data_requirement(
            list(getattr(response, "bars", ()) or ()),
            session_date=proxy._session_date,
            as_of=proxy._observed_at,
            requirement=requirement,
        )
        if not assessment.evaluable:
            return _empty_response(response)
        if requirement.continuity == "rolling" and assessment.reset_required:
            suffix = latest_clean_bars(
                list(getattr(response, "bars", ()) or ()),
                session_date=proxy._session_date,
                interval=interval,
                as_of=proxy._observed_at,
            )
            return _copy_response(response, suffix)
        return response

    assessment = assess_data_requirement(
        recovered.bars,
        session_date=proxy._session_date,
        as_of=proxy._observed_at,
        requirement=requirement,
        recovery_report=recovered.report,
        bucket_evidence=recovered.bucket_evidence,
    )
    if not assessment.evaluable:
        return _empty_response(_recovered_response(recovered))

    selected = list(recovered.bars)
    if requirement.continuity == "rolling" and assessment.reset_required:
        selected = latest_clean_bars(
            recovered.bars,
            session_date=proxy._session_date,
            interval=interval,
            as_of=proxy._observed_at,
        )
    return _recovered_response(recovered, bars=selected)


def recover_shadow_bars(
    proxy,
    instrument_id,
    interval,
    limit=500,
    binding_id=None,
    cancellation=None,
    *,
    base_bars,
):
    """Apply the conservative session dependency to the default bars surface."""

    if proxy._observed_at.astimezone(_ET).time() < _REGULAR_OPEN:
        try:
            return base_bars(
                instrument_id,
                interval,
                limit,
                binding_id,
                cancellation,
            )
        except Exception:
            return _empty_response(None)

    return recover_bars_for_requirement(
        proxy,
        instrument_id,
        interval,
        limit,
        binding_id,
        cancellation,
        requirement=StrategyDataRequirement(
            interval=interval,
            continuity="session",
            minimum_clean_bars=1,
            required_fields=("ohlc", "volume"),
            reset_on_gap=False,
            allow_partial_market_price=False,
            allow_partial_market_volume=False,
        ),
        base_bars=base_bars,
    )


__all__ = ["recover_bars_for_requirement", "recover_shadow_bars"]
