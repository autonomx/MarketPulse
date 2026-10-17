from __future__ import annotations

import logging

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .contracts import ResearchOutcome, StrategyResearchFeatures, fingerprint

logger = logging.getLogger(__name__)


def research_context_as_of(
    *,
    instrument_id: str,
    decision_at: datetime,
    fact_repository: Any,
    research_repository: Any | None = None,
    shadow_repository: Any | None = None,
) -> dict[str, Any]:
    """Return only research context Omnix durably knew by ``decision_at``.

    This helper performs repository as-of reads only. It never launches web/SEC
    retrieval and therefore cannot backfill historical knowledge with hindsight.
    """
    context: dict[str, Any] = {}
    try:
        fact_set = fact_repository.latest_fact_set_as_of(instrument_id, decision_at)
    except Exception:
        fact_set = None
    if fact_set is not None:
        context.update({
            "fact_set_id": fact_set.fact_set_id,
            "fact_schema_version": fact_set.schema_version,
            "extractor_version": fact_set.extractor_version,
            "catalyst": fact_set.catalyst.model_dump(mode="json"),
            "supply_metrics": fact_set.supply_metrics.model_dump(mode="json"),
            "coverage": fact_set.completeness.model_dump(mode="json"),
            "unresolved_facts": list(fact_set.unresolved_facts),
        })
    if research_repository is None:
        try:
            from .repository import default_research_repository
            research_repository = default_research_repository()
        except Exception:
            research_repository = None
    if research_repository is not None:
        try:
            report = research_repository.latest_report_as_of(instrument_id, decision_at)
        except Exception:
            report = None
        if report is not None:
            context["report"] = {
                "report_id": report.report_id,
                "report_version": report.report_version,
                "research_status": report.research_status,
                "catalyst_status": report.catalyst_status,
                "supply_status": report.supply_status,
                "omnix_known_at": report.omnix_known_at.isoformat() if report.omnix_known_at else None,
            }
    if shadow_repository is None:
        try:
            from .shadow_repository import default_shadow_repository
            shadow_repository = default_shadow_repository()
        except Exception:
            shadow_repository = None
    if shadow_repository is not None:
        try:
            shadow = shadow_repository.latest_as_of(instrument_id, decision_at)
        except Exception:
            shadow = None
        if shadow is not None:
            context["novelty_shadow"] = {
                "annotation_id": shadow.annotation_id,
                "novelty": shadow.novelty,
                "relevance": shadow.relevance,
                "catalyst_class": shadow.catalyst_class,
                "confidence": str(shadow.confidence),
                "shadow_only": True,
            }
    return context


def build_research_outcome(
    *,
    session_date: date,
    instrument_id: str,
    strategy_version: str,
    features: StrategyResearchFeatures | None,
    strategy_id: str | None = None,
    market_fidelity: str = "captured",
    research_fidelity: str = "captured_exact",
    strategy_state: str | None = None,
    rejection_reason: str | None = None,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
    mfe_r: Decimal | None = None,
    mae_r: Decimal | None = None,
    r_result: Decimal | None = None,
    two_r_before_minus_one_r: bool | None = None,
    time_to_mfe_minutes: Decimal | None = None,
    time_to_stop_minutes: Decimal | None = None,
    data_quality_flags: tuple[str, ...] = (),
    research_context: dict[str, Any] | None = None,
) -> ResearchOutcome:
    feature_payload = features.model_dump(mode="json") if features else {}
    if research_context:
        feature_payload["_research_context"] = research_context
    research_policy = features.research_policy_version if features else "unavailable"
    projection = features.projection_version if features else "unavailable"
    research_status = features.research_status if features else str(
        ((research_context or {}).get("report") or {}).get("research_status") or "unavailable"
    )
    payload = {
        "session_date": session_date.isoformat(),
        "instrument_id": instrument_id,
        "strategy_version": strategy_version,
        "features": feature_payload,
        "strategy_state": strategy_state,
        "rejection_reason": rejection_reason,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "r_result": r_result,
        "market_fidelity": market_fidelity,
        "research_fidelity": research_fidelity,
    }
    fp = fingerprint(payload)
    return ResearchOutcome(
        outcome_id=f"rout-{hashlib.sha256((instrument_id+'|'+fp).encode()).hexdigest()[:24]}",
        session_date=session_date,
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        research_policy_version=research_policy,
        feature_projection_version=projection,
        market_fidelity=market_fidelity,
        research_fidelity=research_fidelity,
        research_status=research_status,
        features=feature_payload,
        strategy_state=strategy_state,
        rejection_reason=rejection_reason,
        entry_time=entry_time,
        exit_time=exit_time,
        mfe_r=mfe_r,
        mae_r=mae_r,
        r_result=r_result,
        two_r_before_minus_one_r=two_r_before_minus_one_r,
        time_to_mfe_minutes=time_to_mfe_minutes,
        time_to_stop_minutes=time_to_stop_minutes,
        data_quality_flags=data_quality_flags,
        immutable_fingerprint=fp,
    )


def persist_backtest_trade_outcomes(
    *,
    strategy_id: str | None,
    strategy_version: str,
    session_date: date,
    trades: list[Any] | tuple[Any, ...],
    candidate_decisions: list[Any] | tuple[Any, ...] = (),
    market_fidelity: str,
    fact_repository: Any,
    reward_multiple: Decimal = Decimal("2"),
) -> int:
    """Append causally attributed HTR outcomes for completed simulated trades."""
    saved = 0
    traded_instruments: set[str] = set()
    for trade in trades:
        traded_instruments.add(trade.instrument_id)
        features = fact_repository.research_features_as_of(trade.instrument_id, trade.entry_time)
        context = research_context_as_of(
            instrument_id=trade.instrument_id,
            decision_at=trade.entry_time,
            fact_repository=fact_repository,
        )
        research_fidelity = "captured_exact" if features is not None else "unavailable"
        two_r_label = None
        time_to_mfe = None
        if reward_multiple == Decimal("2"):
            # The backtest protection engine is pessimistic stop-before-target on
            # ambiguous bars, so a target exit establishes +2R before -1R.
            two_r_label = trade.exit_reason == "target"
            if trade.exit_reason == "target":
                time_to_mfe = trade.hold_minutes
        time_to_stop = trade.hold_minutes if trade.exit_reason == "stop" else None
        outcome = build_research_outcome(
            session_date=session_date,
            strategy_id=strategy_id,
            instrument_id=trade.instrument_id,
            strategy_version=strategy_version,
            features=features,
            market_fidelity=market_fidelity,
            research_fidelity=research_fidelity,
            strategy_state="traded",
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            mfe_r=trade.mfe_r,
            mae_r=trade.mae_r,
            r_result=trade.r_multiple,
            two_r_before_minus_one_r=two_r_label,
            time_to_mfe_minutes=time_to_mfe,
            time_to_stop_minutes=time_to_stop,
            data_quality_flags=(() if features is not None else ("research_features_unavailable_as_of_entry",)),
            research_context=context,
        )
        saved += int(bool(fact_repository.save_outcome(outcome)))

    for decision in candidate_decisions:
        if decision.instrument_id in traded_instruments or getattr(decision, "selected_trade", False):
            continue
        decision_at = decision.decision_at
        features = fact_repository.research_features_as_of(decision.instrument_id, decision_at)
        context = research_context_as_of(
            instrument_id=decision.instrument_id,
            decision_at=decision_at,
            fact_repository=fact_repository,
        )
        research_fidelity = "captured_exact" if features is not None else "unavailable"
        flags = ["unlabeled_non_trade_observation"]
        if features is None:
            flags.append("research_features_unavailable_as_of_decision")
        outcome = build_research_outcome(
            session_date=session_date,
            strategy_id=strategy_id,
            instrument_id=decision.instrument_id,
            strategy_version=strategy_version,
            features=features,
            market_fidelity=market_fidelity,
            research_fidelity=research_fidelity,
            strategy_state=decision.state,
            rejection_reason=decision.rejection_reason,
            entry_time=decision.entry_time,
            exit_time=decision.exit_time,
            data_quality_flags=tuple(flags),
            research_context=context,
        )
        saved += int(bool(fact_repository.save_outcome(outcome)))
    return saved


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [row for row in rows if _decimal(row.get("r_result")) is not None]
    r_values = [_decimal(row.get("r_result")) for row in labeled]
    two_r = [row.get("two_r_before_minus_one_r") for row in rows if row.get("two_r_before_minus_one_r") is not None]
    return {
        "n": len(rows),
        "labeled_n": len(labeled),
        "expectancy_r": sum((value or Decimal("0") for value in r_values), Decimal("0")) / Decimal(len(r_values)) if r_values else None,
        "two_r_probability": Decimal(sum(1 for value in two_r if value)) / Decimal(len(two_r)) if two_r else None,
        "mean_mfe_r": _mean_decimal([_decimal(row.get("mfe_r")) for row in rows]),
        "mean_mae_r": _mean_decimal([_decimal(row.get("mae_r")) for row in rows]),
    }


def _mean_decimal(values: list[Decimal | None]) -> Decimal | None:
    clean = [value for value in values if value is not None]
    return sum(clean, Decimal("0")) / Decimal(len(clean)) if clean else None


def attribution_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """HTR-13 descriptive attribution; never feeds same-decision feature extraction."""
    boolean_features = (
        "primary_catalyst_confirmed",
        "catalyst_same_day",
        "immediate_supply_risk",
        "unresolved_supply",
        "source_authority_sufficient",
    )
    comparisons: dict[str, Any] = {}
    for key in boolean_features:
        comparisons[key] = {
            "true": _group_stats([row for row in outcomes if (row.get("features") or {}).get(key) is True]),
            "false": _group_stats([row for row in outcomes if (row.get("features") or {}).get(key) is False]),
        }

    research_status = {
        status: _group_stats([row for row in outcomes if row.get("research_status") == status])
        for status in ("complete", "partial", "timed_out", "failed", "unavailable")
    }
    market_fidelity = {
        fidelity: _group_stats([row for row in outcomes if row.get("market_fidelity") == fidelity])
        for fidelity in sorted({str(row.get("market_fidelity")) for row in outcomes})
    }
    novelty_values = sorted({
        str((((row.get("features") or {}).get("_research_context") or {}).get("novelty_shadow") or {}).get("novelty"))
        for row in outcomes
        if (((row.get("features") or {}).get("_research_context") or {}).get("novelty_shadow") or {}).get("novelty")
    })
    novelty = {
        value: _group_stats([
            row for row in outcomes
            if str(((((row.get("features") or {}).get("_research_context") or {}).get("novelty_shadow") or {}).get("novelty"))) == value
        ])
        for value in novelty_values
    }

    def warrant_bucket(row: dict[str, Any]) -> str:
        metrics = (((row.get("features") or {}).get("_research_context") or {}).get("supply_metrics") or {})
        value = _decimal(metrics.get("in_the_money_warrant_pct_float"))
        if value is None: return "unavailable"
        if value == 0: return "0%"
        if value < 25: return "0-25%"
        if value < 100: return "25-100%"
        return ">=100%"

    supply_buckets = {
        bucket: _group_stats([row for row in outcomes if warrant_bucket(row) == bucket])
        for bucket in ("0%", "0-25%", "25-100%", ">=100%", "unavailable")
    }
    exact = [
        row for row in outcomes
        if row.get("market_fidelity") in {"captured", "captured_point_in_time", "exact", "paper-execution-v2"}
        and row.get("research_fidelity") in {"captured_exact", "exact"}
    ]
    structure_only = [row for row in outcomes if str(row.get("strategy_version") or "") in {"1.0.0", "1.1.0"}]
    same_day_primary = [
        row for row in outcomes
        if (row.get("features") or {}).get("primary_catalyst_confirmed") is True
        and (row.get("features") or {}).get("catalyst_same_day") is True
    ]
    secondary_only = [
        row for row in outcomes
        if (row.get("features") or {}).get("primary_catalyst_confirmed") is False
        and int(((((row.get("features") or {}).get("_research_context") or {}).get("catalyst") or {}).get("source_count_secondary") or 0)) > 0
    ]
    resolved_supply = [row for row in outcomes if (row.get("features") or {}).get("supply_resolution_status") == "clear"]
    unresolved_supply_rows = [row for row in outcomes if (row.get("features") or {}).get("supply_resolution_status") == "unresolved"]
    strategy_states = {
        state: _group_stats([row for row in outcomes if str(row.get("strategy_state") or "unavailable") == state])
        for state in sorted({str(row.get("strategy_state") or "unavailable") for row in outcomes})
    }
    return {
        "sample_size": len(outcomes),
        "baseline": _group_stats(outcomes),
        "structure_only_baseline": _group_stats(structure_only),
        "exact_causal_subset": _group_stats(exact),
        "same_day_primary_vs_secondary_only": {
            "same_day_primary": _group_stats(same_day_primary),
            "secondary_only": _group_stats(secondary_only),
        },
        "supply_resolution": {
            "clear": _group_stats(resolved_supply),
            "unresolved": _group_stats(unresolved_supply_rows),
        },
        "strategy_states": strategy_states,
        "feature_comparisons": comparisons,
        "research_status": research_status,
        "market_fidelity": market_fidelity,
        "novelty_shadow": novelty,
        "itm_warrant_pct_float_buckets": supply_buckets,
        "anti_leakage": "descriptive outcome labels are downstream-only and never feed fact extraction/projection",
    }
