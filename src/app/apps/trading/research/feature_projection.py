from __future__ import annotations

import hashlib
from datetime import datetime

from .contracts import StrategyResearchFeatures, TradingFactSet, TradingResearchReport, fingerprint


def project_research_features(
    fact_set: TradingFactSet,
    *,
    decision_at: datetime,
    report: TradingResearchReport | None = None,
    research_policy_version: str = "trading-research-1-shadow",
) -> StrategyResearchFeatures:
    known = fact_set.omnix_known_at
    if known is not None and known > decision_at:
        raise ValueError("fact_set_not_known_at_decision")
    published = fact_set.catalyst.source_published_at
    age = None
    if published is not None:
        age = max(0, int((decision_at - published).total_seconds() // 60))
    status = report.research_status if report is not None else "partial"
    payload = {
        "instrument_id": fact_set.instrument_id,
        "fact_set_id": fact_set.fact_set_id,
        "decision_at": decision_at.isoformat(),
        "primary_catalyst_confirmed": fact_set.catalyst.primary_confirmed,
        "catalyst_same_day": fact_set.catalyst.same_day,
        "catalyst_age_minutes": age,
        "immediate_supply_risk": fact_set.supply_metrics.immediate_supply_risk,
        "supply_resolution_status": fact_set.supply_metrics.supply_resolution_status,
        "research_status": status,
        "projection_version": "research-features-1",
        "research_policy_version": research_policy_version,
    }
    fp = fingerprint(payload)
    return StrategyResearchFeatures(
        feature_id=f"rf-{hashlib.sha256((fact_set.instrument_id + '|' + fp).encode()).hexdigest()[:24]}",
        research_policy_version=research_policy_version,
        strategy_id=fact_set.strategy_id,
        instrument_id=fact_set.instrument_id,
        fact_set_id=fact_set.fact_set_id,
        decision_at=decision_at,
        primary_catalyst_confirmed=fact_set.catalyst.primary_confirmed,
        catalyst_same_day=fact_set.catalyst.same_day,
        catalyst_fresh=bool(fact_set.catalyst.same_day and age is not None and age <= 24 * 60),
        catalyst_age_minutes=age,
        immediate_supply_risk=fact_set.supply_metrics.immediate_supply_risk,
        supply_resolution_status=fact_set.supply_metrics.supply_resolution_status,
        research_status=status,
        unresolved_supply=fact_set.supply_metrics.supply_resolution_status == "unresolved",
        source_authority_sufficient=fact_set.catalyst.primary_confirmed,
        immutable_fingerprint=fp,
    )
