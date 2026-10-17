from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from ..contracts import ResearchCoverage, TradingEvidence, TradingFactSet, fingerprint
from .catalyst import extract_catalyst_facts
from .metrics import derive_supply_metrics
from .supply import extract_supply_facts


def build_fact_set(
    *,
    instrument_id: str,
    evidence: list[TradingEvidence] | tuple[TradingEvidence, ...],
    decision_at: datetime,
    strategy_id: str | None = None,
    report_id: str | None = None,
    coverage: ResearchCoverage | None = None,
    float_shares: Decimal | None = None,
    market_cap: Decimal | None = None,
    market_price: Decimal | None = None,
) -> TradingFactSet:
    generated = datetime.now(timezone.utc)
    visible = tuple(item for item in evidence if item.omnix_known_at is None or item.omnix_known_at <= decision_at)
    catalyst = extract_catalyst_facts(visible, decision_at=decision_at)
    supply = extract_supply_facts(visible)
    metrics = derive_supply_metrics(supply, float_shares=float_shares, market_cap=market_cap, market_price=market_price)
    unresolved: list[str] = []
    if catalyst.unresolved: unresolved.append("catalyst_primary_confirmation")
    if metrics.supply_resolution_status == "unresolved": unresolved.append("supply_status")
    active_coverage = coverage or ResearchCoverage()
    payload = {
        "strategy_id": strategy_id,
        "instrument_id": instrument_id,
        "report_id": report_id,
        "catalyst": catalyst.model_dump(mode="json"),
        "supply": [item.model_dump(mode="json") for item in supply],
        "metrics": metrics.model_dump(mode="json"),
        "evidence_ids": [item.evidence_id for item in visible],
        "schema_version": "trading-facts-1",
    }
    fp = fingerprint(payload)
    return TradingFactSet(
        fact_set_id=f"facts-{hashlib.sha256((instrument_id + '|' + fp).encode()).hexdigest()[:24]}",
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        report_id=report_id,
        generated_at=generated,
        catalyst=catalyst,
        supply=supply,
        supply_metrics=metrics,
        completeness=active_coverage,
        unresolved_facts=tuple(unresolved),
        evidence_ids=tuple(item.evidence_id for item in visible),
        immutable_fingerprint=fp,
    )
