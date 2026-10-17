from __future__ import annotations

import json
from datetime import datetime

from app.security.tenant_context import TenantContext, current_tenant
from app.persistence.unit_of_work import unit_of_work

from .contracts import NoveltyShadowAnnotation


class TradingShadowResearchRepository:
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context=context or current_tenant(); self.uow_factory=uow_factory

    def save(self, item: NoveltyShadowAnnotation) -> bool:
        with self.uow_factory() as uow:
            row=uow.connection.execute("""
                INSERT INTO omnix_trading_research_shadow_annotations
                (workspace_id,annotation_id,instrument_id,observed_at,novelty,relevance,catalyst_class,conflict_summary,
                 confidence,evidence_ids,rationale,shadow_only)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,TRUE)
                ON CONFLICT (workspace_id,annotation_id) DO NOTHING RETURNING annotation_id
            """,(self.context.workspace_id,item.annotation_id,item.instrument_id,item.observed_at,item.novelty,item.relevance,
                  item.catalyst_class,item.conflict_summary,item.confidence,json.dumps(list(item.evidence_ids)),item.rationale)).fetchone();uow.commit()
        return row is not None

    def latest_as_of(self, instrument_id: str, observed_at_lte: datetime) -> NoveltyShadowAnnotation | None:
        with self.uow_factory() as uow:
            r=uow.connection.execute("""
                SELECT annotation_id,instrument_id,observed_at,novelty,relevance,catalyst_class,conflict_summary,confidence,evidence_ids,rationale
                  FROM omnix_trading_research_shadow_annotations
                 WHERE workspace_id=%s AND instrument_id=%s AND observed_at<=%s ORDER BY observed_at DESC LIMIT 1
            """,(self.context.workspace_id,instrument_id,observed_at_lte)).fetchone()
        if r is None:return None
        return NoveltyShadowAnnotation(annotation_id=r[0],instrument_id=r[1],observed_at=r[2],novelty=r[3],relevance=r[4],
            catalyst_class=r[5],conflict_summary=r[6],confidence=r[7],evidence_ids=tuple(r[8]),rationale=r[9],shadow_only=True)


def default_shadow_repository() -> TradingShadowResearchRepository:
    return TradingShadowResearchRepository()
