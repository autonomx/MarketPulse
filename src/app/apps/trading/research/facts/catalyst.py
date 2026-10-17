from __future__ import annotations

import re
from datetime import datetime, timezone

from ..contracts import CatalystFactSet, TradingEvidence
from app.apps.trading.us_equity_calendar import EASTERN as _ET

_SUPPLY_ONLY_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "424B3", "424B5", "RW", "EFFECT"}


def _classify(text: str) -> str:
    lowered = text.lower()
    patterns = (
        ("clinical", r"\b(?:fda|clinical trial|phase [123]|drug|therapy)\b"),
        ("earnings", r"\b(?:earnings|revenue|quarter|eps)\b"),
        ("contract_partnership", r"\b(?:contract|partnership|agreement|award)\b"),
        ("financing", r"\b(?:offering|financing|warrant|convertible|atm)\b"),
        ("regulatory", r"\b(?:regulatory|approval|clearance)\b"),
    )
    return next((name for name, pattern in patterns if re.search(pattern, lowered)), "other")


def _catalyst_candidate(item: TradingEvidence) -> bool:
    if item.source_type == "company_ir":
        return True
    if item.source_type == "sec":
        form = str(item.metadata.get("form") or "").upper()
        return form not in _SUPPLY_ONLY_FORMS
    # Tier-2 current news can support a probable catalyst but does not count as
    # primary confirmation.
    return item.source_type in {"news", "web"}


def extract_catalyst_facts(
    evidence: list[TradingEvidence] | tuple[TradingEvidence, ...],
    *,
    decision_at: datetime,
) -> CatalystFactSet:
    generated = datetime.now(timezone.utc)
    eligible = [item for item in evidence if (item.omnix_known_at is None or item.omnix_known_at <= decision_at) and _catalyst_candidate(item)]
    primary = [item for item in eligible if item.source_authority_tier == 1]
    secondary = [item for item in eligible if item.source_authority_tier == 2]
    published = [item.source_published_at for item in eligible if item.source_published_at is not None]
    latest = max(published) if published else None
    same_day = bool(latest and latest.astimezone(_ET).date() == decision_at.astimezone(_ET).date())
    combined = " ".join(item.title or "" for item in eligible) + " " + " ".join(item.content[:2000] for item in eligible)
    return CatalystFactSet(
        primary_confirmed=bool(primary),
        same_day=same_day,
        source_count_primary=len(primary),
        source_count_secondary=len(secondary),
        catalyst_type=_classify(combined) if eligible else "unknown",
        source_published_at=latest,
        official_filing_present=any(item.source_type == "sec" for item in eligible),
        company_release_present=any(item.source_type == "company_ir" for item in eligible),
        unresolved=not bool(primary),
        source_evidence_ids=tuple(item.evidence_id for item in eligible),
        generated_at=generated,
    )
