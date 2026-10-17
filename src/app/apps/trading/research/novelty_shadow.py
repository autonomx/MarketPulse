from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.providers.base import ChatMessage
from app.apps.trading.research import _call_provider, _json_payload, default_research_provider

from .contracts import NoveltyShadowAnnotation, TradingEvidence
from decimal import Decimal


def generate_novelty_shadow(
    instrument_id: str,
    evidence: list[TradingEvidence] | tuple[TradingEvidence, ...],
    *,
    observed_at: datetime,
    provider_factory: Callable[[], Any] = default_research_provider,
    model: str | None = None,
) -> NoveltyShadowAnnotation:
    visible = tuple(sorted((x for x in evidence if x.omnix_known_at is not None and x.omnix_known_at <= observed_at), key=lambda x:(x.source_published_at or x.captured_at,x.evidence_id)))
    if not visible:
        return NoveltyShadowAnnotation(annotation_id=f"novelty-{hashlib.sha256((instrument_id+'|empty|'+observed_at.isoformat()).encode()).hexdigest()[:24]}",
            instrument_id=instrument_id, observed_at=observed_at, novelty="uncertain", relevance="uncertain", catalyst_class="unknown",
            conflict_summary="No causally visible evidence was available.", confidence=Decimal("0"), evidence_ids=(), rationale="Evidence unavailable.")
    supplied=[{"evidence_id":x.evidence_id,"source_type":x.source_type,"authority_tier":x.source_authority_tier,
               "source_published_at":x.source_published_at.isoformat() if x.source_published_at else None,"title":x.title,
               "content":x.content[:8000]} for x in visible]
    messages=[ChatMessage(role="system",content=("You are a read-only shadow trading-research classifier. Use only supplied evidence. "
        "Do not recommend trades, prices, orders, sizing, strategy changes, or actions. Return exactly JSON keys novelty, relevance, "
        "catalyst_class, conflict_summary, confidence, rationale. novelty=new|incremental|recycled|uncertain; relevance=high|medium|low|uncertain; confidence 0..1.")),
        ChatMessage(role="user",content=json.dumps({"instrument_id":instrument_id,"evidence":supplied},sort_keys=True,default=str))]
    payload=_json_payload(_call_provider(provider_factory(),messages,model))
    expected={"novelty","relevance","catalyst_class","conflict_summary","confidence","rationale"}
    if set(payload)!=expected: raise ValueError("novelty_shadow_output_keys_invalid")
    raw=f"{instrument_id}|{observed_at.isoformat()}|{'|'.join(x.evidence_id for x in visible)}|{json.dumps(payload,sort_keys=True)}"
    return NoveltyShadowAnnotation(annotation_id=f"novelty-{hashlib.sha256(raw.encode()).hexdigest()[:24]}",instrument_id=instrument_id,
        observed_at=observed_at,novelty=payload["novelty"],relevance=payload["relevance"],catalyst_class=str(payload["catalyst_class"]),
        conflict_summary=str(payload["conflict_summary"]),confidence=payload["confidence"],evidence_ids=tuple(x.evidence_id for x in visible),
        rationale=str(payload["rationale"]),shadow_only=True)
