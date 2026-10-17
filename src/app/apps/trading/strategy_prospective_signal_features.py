from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any

from .indicator_signals import MultiTimeframeIndicatorContext
from .models import MarketBar
from .providers.alpaca_iex_status import AlpacaIexStatusCache, default_alpaca_iex_status_cache
from .research.fact_repository import TradingFactRepository, default_fact_repository
from .research.repository import TradingResearchRepository, default_research_repository
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_SCHEMA_VERSION = "v2-prospective-signal-features-2"
_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_LAST_30M_START = time(9, 0)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return (numerator / denominator - Decimal("1")) * Decimal("100")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _premarket_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    decision_at: datetime,
) -> list[MarketBar]:
    decision_utc = decision_at.astimezone(timezone.utc)
    decision_date = decision_utc.astimezone(_ET).date()
    output: list[MarketBar] = []
    for bar in bars:
        local = bar.start_time.astimezone(_ET)
        if not bar.is_final or bar.end_time > decision_utc:
            continue
        if local.date() != decision_date:
            continue
        if _PREMARKET_OPEN <= local.time() < _REGULAR_OPEN:
            output.append(bar)
    return sorted(output, key=lambda item: item.start_time)


def premarket_structure_snapshot(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    decision_at: datetime,
) -> dict[str, Any]:
    """Summarize only finalized 04:00-09:30 ET bars visible by ``decision_at``."""

    if decision_at.tzinfo is None:
        raise ValueError("prospective feature decision_at must be timezone-aware")
    premarket = _premarket_bars(bars, decision_at=decision_at)
    if not premarket:
        return {
            "available": False,
            "bar_count": 0,
            "first_bar_at": None,
            "last_bar_at": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "high_at": None,
            "low_at": None,
            "range_pct": None,
            "return_pct": None,
            "vwap": None,
            "close_vs_vwap_pct": None,
            "close_vs_high_pct": None,
            "volume": None,
            "dollar_volume": None,
            "last_30m_return_pct": None,
        }

    first = premarket[0]
    last = premarket[-1]
    high_bar = max(premarket, key=lambda item: (item.high, -item.start_time.timestamp()))
    low_bar = min(premarket, key=lambda item: (item.low, item.start_time.timestamp()))
    volume = sum((bar.volume for bar in premarket), Decimal("0"))
    dollar_volume = sum((bar.close * bar.volume for bar in premarket), Decimal("0"))
    typical_dollar = sum(
        (((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume for bar in premarket),
        Decimal("0"),
    )
    vwap = typical_dollar / volume if volume > 0 else None
    last_30m = [bar for bar in premarket if bar.start_time.astimezone(_ET).time() >= _LAST_30M_START]
    last_30m_return = (
        _pct(last_30m[-1].close, last_30m[0].open)
        if len(last_30m) >= 2
        else None
    )

    return {
        "available": True,
        "bar_count": len(premarket),
        "first_bar_at": _iso(first.start_time),
        "last_bar_at": _iso(last.end_time),
        "open": str(first.open),
        "high": str(high_bar.high),
        "low": str(low_bar.low),
        "close": str(last.close),
        "high_at": _iso(high_bar.start_time),
        "low_at": _iso(low_bar.start_time),
        "range_pct": str(_pct(high_bar.high, low_bar.low)) if low_bar.low > 0 else None,
        "return_pct": str(_pct(last.close, first.open)) if first.open > 0 else None,
        "vwap": str(vwap) if vwap is not None else None,
        "close_vs_vwap_pct": str(_pct(last.close, vwap)) if vwap is not None and vwap > 0 else None,
        "close_vs_high_pct": str(_pct(last.close, high_bar.high)) if high_bar.high > 0 else None,
        "volume": str(volume),
        "dollar_volume": str(dollar_volume),
        "last_30m_return_pct": str(last_30m_return) if last_30m_return is not None else None,
    }


def _research_snapshot(
    instrument_id: str,
    *,
    decision_at: datetime,
    research_repository: TradingResearchRepository | None,
    fact_repository: TradingFactRepository | None,
) -> dict[str, Any]:
    try:
        research_repo = research_repository or default_research_repository()
        fact_repo = fact_repository or default_fact_repository()
        report = research_repo.latest_report_as_of(instrument_id, decision_at)
        fact_set = fact_repo.latest_fact_set_as_of(instrument_id, decision_at)
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "report": None,
            "catalyst": None,
            "supply": None,
        }

    report_payload = None
    if report is not None:
        report_payload = {
            "report_id": report.report_id,
            "report_version": report.report_version,
            "omnix_known_at": _iso(report.omnix_known_at),
            "evidence_cutoff_at": _iso(report.evidence_cutoff_at),
            "research_status": report.research_status,
            "catalyst_status": report.catalyst_status,
            "supply_status": report.supply_status,
            "coverage": report.coverage.model_dump(mode="json"),
            "unresolved_facts": list(report.unresolved_facts),
        }

    if fact_set is None:
        return {
            "available": report is not None,
            "error": None,
            "report": report_payload,
            "catalyst": None,
            "supply": None,
        }

    catalyst = fact_set.catalyst
    published = catalyst.source_published_at
    age_minutes = None
    if published is not None:
        age_minutes = max(0, int((decision_at - published).total_seconds() // 60))
    catalyst_payload = {
        "primary_confirmed": catalyst.primary_confirmed,
        "same_day": catalyst.same_day,
        "catalyst_type": catalyst.catalyst_type,
        "source_count_primary": catalyst.source_count_primary,
        "source_count_secondary": catalyst.source_count_secondary,
        "official_filing_present": catalyst.official_filing_present,
        "company_release_present": catalyst.company_release_present,
        "unresolved": catalyst.unresolved,
        "source_published_at": _iso(catalyst.source_published_at),
        "age_minutes": age_minutes,
        "source_evidence_ids": list(catalyst.source_evidence_ids),
    }
    supply_payload = {
        "resolution_status": fact_set.supply_metrics.supply_resolution_status,
        "immediate_supply_risk": fact_set.supply_metrics.immediate_supply_risk,
        "potential_dilution_pct_float": (
            str(fact_set.supply_metrics.potential_dilution_pct_float)
            if fact_set.supply_metrics.potential_dilution_pct_float is not None
            else None
        ),
        "remaining_atm_pct_market_cap": (
            str(fact_set.supply_metrics.remaining_atm_pct_market_cap)
            if fact_set.supply_metrics.remaining_atm_pct_market_cap is not None
            else None
        ),
        "in_the_money_warrant_pct_float": (
            str(fact_set.supply_metrics.in_the_money_warrant_pct_float)
            if fact_set.supply_metrics.in_the_money_warrant_pct_float is not None
            else None
        ),
        "registered_resale_pct_float": (
            str(fact_set.supply_metrics.registered_resale_pct_float)
            if fact_set.supply_metrics.registered_resale_pct_float is not None
            else None
        ),
        "facts": [
            {
                "fact_id": fact.fact_id,
                "supply_type": fact.supply_type,
                "status": fact.status,
                "shares": str(fact.shares) if fact.shares is not None else None,
                "remaining_capacity_usd": (
                    str(fact.remaining_capacity_usd)
                    if fact.remaining_capacity_usd is not None
                    else None
                ),
                "strike_price": str(fact.strike_price) if fact.strike_price is not None else None,
                "exercise_status": fact.exercise_status,
                "registration_status": fact.registration_status,
                "effective_at": _iso(fact.effective_at),
                "expires_at": _iso(fact.expires_at),
                "resolution_status": fact.resolution_status,
                "confidence": str(fact.confidence),
                "omnix_known_at": _iso(fact.omnix_known_at),
            }
            for fact in sorted(fact_set.supply, key=lambda item: (item.supply_type, item.fact_id))
        ],
    }
    return {
        "available": True,
        "error": None,
        "fact_set_id": fact_set.fact_set_id,
        "fact_set_known_at": _iso(fact_set.omnix_known_at),
        "report": report_payload,
        "catalyst": catalyst_payload,
        "supply": supply_payload,
    }


def _halt_snapshot(
    instrument_id: str,
    *,
    decision_at: datetime,
    status_cache: AlpacaIexStatusCache | None,
) -> dict[str, Any]:
    symbol = instrument_id.rsplit(":", 1)[-1].upper()
    cache = status_cache or default_alpaca_iex_status_cache()
    try:
        return cache.history_snapshot(symbol, as_of=decision_at)
    except Exception as exc:
        return {
            "symbol": symbol,
            "available": False,
            "session_history_complete": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _momentum_snapshot(
    context: MultiTimeframeIndicatorContext | None,
    *,
    full_warmup: bool,
) -> dict[str, Any]:
    if context is None:
        return {
            "available": False,
            "full_warmup": False,
            "one_minute": None,
            "five_minute": None,
        }
    return {
        "available": True,
        "full_warmup": full_warmup,
        "source_bar_count": context.source_bar_count,
        "one_minute": context.one_minute.model_dump(mode="json"),
        "five_minute": context.five_minute.model_dump(mode="json"),
    }


def build_prospective_signal_features(
    *,
    instrument_id: str,
    decision_at: datetime,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    indicator_context: MultiTimeframeIndicatorContext | None,
    indicator_full_warmup: bool,
    research_repository: TradingResearchRepository | None = None,
    fact_repository: TradingFactRepository | None = None,
    status_cache: AlpacaIexStatusCache | None = None,
) -> dict[str, Any]:
    """Build one immutable, causal SHADOW feature row at the execution observation cutoff.

    The record is descriptive only. It cannot grant execution authority and it
    deliberately preserves missing/partial evidence rather than reconstructing it
    later with hindsight.
    """

    if decision_at.tzinfo is None:
        raise ValueError("prospective feature decision_at must be timezone-aware")
    cutoff = decision_at.astimezone(timezone.utc)
    premarket = premarket_structure_snapshot(bars, decision_at=cutoff)
    research = _research_snapshot(
        instrument_id,
        decision_at=cutoff,
        research_repository=research_repository,
        fact_repository=fact_repository,
    )
    halt = _halt_snapshot(instrument_id, decision_at=cutoff, status_cache=status_cache)
    momentum = _momentum_snapshot(indicator_context, full_warmup=indicator_full_warmup)
    completeness = {
        "premarket_available": bool(premarket.get("available")),
        "research_available": bool(research.get("available")),
        "halt_history_complete": bool(halt.get("session_history_complete")),
        "momentum_full_warmup": bool(momentum.get("full_warmup")),
    }
    completeness["all_core_available"] = all(completeness.values())
    visible_bars = [
        bar
        for bar in bars
        if bar.is_final and bar.end_time.astimezone(timezone.utc) <= cutoff
    ]
    source_providers = tuple(
        sorted(
            {
                str(bar.provider)
                for bar in visible_bars
                if str(bar.provider or "").strip()
            }
        )
    )
    partial_market = "alpaca_iex" in source_providers
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "instrument_id": instrument_id,
        "decision_at": cutoff.isoformat(),
        "market_data_source": "canonical_causal_bars",
        "source_providers": list(source_providers),
        "partial_market": partial_market,
        "execution_authority": False,
        "premarket": premarket,
        "research": research,
        "halt_history": halt,
        "momentum": momentum,
        "completeness": completeness,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["immutable_fingerprint"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return payload


__all__ = [
    "build_prospective_signal_features",
    "premarket_structure_snapshot",
]
