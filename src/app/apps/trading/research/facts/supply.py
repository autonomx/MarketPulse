from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal

from ..contracts import SupplyFact, TradingEvidence, fingerprint
from typing import Any, cast

_TYPE_PATTERNS = (
    ("atm", re.compile(r"\b(?:at[- ]the[- ]market|ATM (?:facility|offering|program|sales agreement))\b", re.I)),
    ("warrant", re.compile(r"\bwarrants?\b", re.I)),
    ("resale_registration", re.compile(r"\b(?:resale registration|selling stockholders?|resale prospectus)\b", re.I)),
    ("convertible", re.compile(r"\bconvertible (?:notes?|debt|preferred|securities)\b", re.I)),
    ("shelf_registration", re.compile(r"\b(?:shelf registration|shelf offering)\b", re.I)),
    ("equity_line", re.compile(r"\b(?:equity line|equity purchase agreement)\b", re.I)),
    ("registered_offering", re.compile(r"\b(?:registered direct|public offering|securities offering)\b", re.I)),
)
_TERMINATED = re.compile(r"\b(?:terminated|cancelled|canceled|ended|no longer available)\b", re.I)
_EXHAUSTED = re.compile(r"\b(?:exhausted|fully utilized|fully used|all outstanding warrants? (?:have been |were )?exercised|repaid|paid in full)\b", re.I)
_EXPIRED = re.compile(r"\bexpired\b", re.I)
_REDEEMED = re.compile(r"\bredeemed\b", re.I)
_WITHDRAWN = re.compile(r"\bwithdrawn\b", re.I)
_EXERCISABLE = re.compile(r"\b(?:exercisable|may be exercised|currently outstanding)\b", re.I)
_ACTIVE = re.compile(r"\b(?:active|remains? available|outstanding|effective|may sell|may issue|may offer|entered into|enters? into|enters? (?:an? )?(?:at[- ]the[- ]market|ATM)|commenced|launched|currently available)\b", re.I)
_EFFECTIVE = re.compile(r"\b(?:became|is|was|declared) effective\b", re.I)
_COUNT = r"(?P<n>\d[\d,.]*)\s*(?P<u>thousand|million|billion|k|m|b)?"
_SHARE = re.compile(_COUNT + r"\s+shares?", re.I)
_WARRANT_COUNT = re.compile(_COUNT + r"\s+warrants?", re.I)
_MONEY = re.compile(r"\$\s*" + _COUNT, re.I)
_STRIKE = re.compile(r"(?:exercise|strike) price(?: of| at)?\s*\$\s*(?P<n>\d+(?:\.\d+)?)", re.I)


def _number(match: re.Match[str] | None) -> Decimal | None:
    if match is None:
        return None
    value = Decimal(match.group("n").replace(",", ""))
    unit = (match.groupdict().get("u") or "").lower()
    factor = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000, "b": 1_000_000_000, "billion": 1_000_000_000}.get(unit, 1)
    return value * factor


def _status(statement: str, supply_type: str) -> tuple[str, str, Decimal]:
    # Explicit terminal states outrank generic consequence wording. For example,
    # "facility is exhausted and no longer available" is exhausted, not merely
    # terminated. Generic negative/status-resolving language still precedes words
    # such as "outstanding" so historical facilities are not treated as active.
    if _EXHAUSTED.search(statement): return "exhausted", "resolved", Decimal("0.95")
    if _WITHDRAWN.search(statement): return "withdrawn", "resolved", Decimal("0.95")
    if _REDEEMED.search(statement): return "redeemed", "resolved", Decimal("0.95")
    if _EXPIRED.search(statement): return "expired", "resolved", Decimal("0.95")
    if _TERMINATED.search(statement): return "terminated", "resolved", Decimal("0.95")
    if supply_type == "warrant" and _EXERCISABLE.search(statement): return "exercisable", "resolved", Decimal("0.9")
    if _ACTIVE.search(statement): return "active", "resolved", Decimal("0.8")
    return "unknown", "unresolved", Decimal("0.4")


def extract_supply_facts(evidence: list[TradingEvidence] | tuple[TradingEvidence, ...]) -> tuple[SupplyFact, ...]:
    facts: list[SupplyFact] = []
    generated = datetime.now(timezone.utc)
    for item in evidence:
        statements = [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\n+", item.content) if part.strip()]
        for statement in statements:
            for supply_type, pattern in _TYPE_PATTERNS:
                if not pattern.search(statement):
                    continue
                status, resolution, confidence = _status(statement, supply_type)
                shares = _number(_SHARE.search(statement))
                if shares is None and supply_type == "warrant":
                    shares = _number(_WARRANT_COUNT.search(statement))
                money_matches = list(_MONEY.finditer(statement))
                remaining_capacity = None
                if supply_type == "atm" and money_matches and re.search(r"remaining|available|capacity|up to", statement, re.I):
                    remaining_capacity = _number(money_matches[-1])
                strike_match = _STRIKE.search(statement)
                strike = Decimal(strike_match.group("n")) if strike_match else None
                registration_status = "effective" if _EFFECTIVE.search(statement) else "withdrawn" if status == "withdrawn" else None
                payload = {
                    "instrument_id": item.instrument_id,
                    "supply_type": supply_type,
                    "status": status,
                    "shares": str(shares) if shares is not None else None,
                    "remaining_capacity_usd": str(remaining_capacity) if remaining_capacity is not None else None,
                    "strike_price": str(strike) if strike is not None else None,
                    "registration_status": registration_status,
                    "source_evidence_ids": [item.evidence_id],
                    "extractor_version": "supply-parser-1",
                }
                fp = fingerprint(payload)
                facts.append(SupplyFact(
                    fact_id=f"supply-{hashlib.sha256((item.evidence_id + '|' + supply_type + '|' + fp).encode()).hexdigest()[:24]}",
                    instrument_id=item.instrument_id,
                    supply_type=cast(Any, supply_type),
                    status=cast(Any, status),
                    shares=shares,
                    remaining_capacity_usd=remaining_capacity,
                    strike_price=strike,
                    exercise_status="exercisable" if status == "exercisable" else None,
                    registration_status=registration_status,
                    source_evidence_ids=(item.evidence_id,),
                    resolution_status=cast(Any, resolution),
                    confidence=confidence,
                    generated_at=generated,
                    immutable_fingerprint=fp,
                ))
    unique = {fact.immutable_fingerprint: fact for fact in facts}
    return tuple(unique.values())
