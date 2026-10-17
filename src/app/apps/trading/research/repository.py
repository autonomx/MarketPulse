from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .contracts import (
    IssuerIdentity, ResearchActionRecord, ResearchCoverage, TradingEvidence, TradingResearchReport,
)


class TradingResearchRepository:
    context = RequestTenant()
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def save_identity(self, item: IssuerIdentity) -> IssuerIdentity:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_issuer_identities
                (workspace_id, identity_id, instrument_id, symbol, exchange, legal_name, cik, source,
                 source_available_at, captured_at, confidence, immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id, item.identity_id, item.instrument_id, item.symbol, item.exchange,
                  item.legal_name, item.cik, item.source, item.source_available_at, item.captured_at,
                  item.confidence, item.immutable_fingerprint)).fetchone()
            uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def identity_as_of(self, instrument_id: str, known_at_lte: datetime) -> IssuerIdentity | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                SELECT identity_id,instrument_id,symbol,exchange,legal_name,cik,source,source_available_at,
                       captured_at,omnix_known_at,confidence,immutable_fingerprint
                  FROM omnix_trading_issuer_identities
                 WHERE workspace_id=%s AND instrument_id=%s AND omnix_known_at<=%s
                 ORDER BY omnix_known_at DESC LIMIT 1
            """, (self.context.workspace_id, instrument_id, known_at_lte)).fetchone()
        if row is None: return None
        return IssuerIdentity(identity_id=row[0], instrument_id=row[1], symbol=row[2], exchange=row[3], legal_name=row[4],
            cik=row[5], source=row[6], source_available_at=row[7], captured_at=row[8], omnix_known_at=row[9],
            confidence=Decimal(row[10]), immutable_fingerprint=row[11])

    def save_evidence(self, item: TradingEvidence) -> TradingEvidence:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_research_evidence
                (workspace_id,evidence_id,instrument_id,issuer_identity_id,evidence_type,source_type,source_locator,
                 source_authority_tier,source_published_at,source_available_at,captured_at,title,content,content_hash,
                 extraction_status,metadata,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING evidence_id, omnix_known_at
            """, (self.context.workspace_id,item.evidence_id,item.instrument_id,item.issuer_identity_id,item.evidence_type,
                  item.source_type,item.source_locator,item.source_authority_tier,item.source_published_at,item.source_available_at,
                  item.captured_at,item.title,item.content,item.content_hash,item.extraction_status,json.dumps(item.metadata,default=str),
                  item.immutable_fingerprint)).fetchone()
            uow.commit()
        return item.model_copy(update={"evidence_id": row[0], "omnix_known_at": row[1]})

    def list_evidence_as_of(self, instrument_id: str, known_at_lte: datetime, limit: int = 200) -> list[TradingEvidence]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute("""
                SELECT evidence_id,instrument_id,issuer_identity_id,evidence_type,source_type,source_locator,
                       source_authority_tier,source_published_at,source_available_at,captured_at,omnix_known_at,title,
                       content,content_hash,extraction_status,metadata,immutable_fingerprint
                  FROM omnix_trading_research_evidence
                 WHERE workspace_id=%s AND instrument_id=%s AND omnix_known_at<=%s
                 ORDER BY omnix_known_at DESC LIMIT %s
            """, (self.context.workspace_id,instrument_id,known_at_lte,limit)).fetchall()
        return [TradingEvidence(evidence_id=r[0],instrument_id=r[1],issuer_identity_id=r[2],evidence_type=r[3],source_type=r[4],
            source_locator=r[5],source_authority_tier=r[6],source_published_at=r[7],source_available_at=r[8],captured_at=r[9],
            omnix_known_at=r[10],title=r[11],content=r[12],content_hash=r[13],extraction_status=r[14],metadata=r[15],
            immutable_fingerprint=r[16]) for r in rows]

    def evidence_by_ids_as_of(
        self,
        instrument_id: str,
        evidence_ids: tuple[str, ...] | list[str],
        known_at_lte: datetime,
    ) -> list[TradingEvidence]:
        ids = tuple(dict.fromkeys(evidence_ids))
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        with self.uow_factory() as uow:
            rows = uow.connection.execute(f"""
                SELECT evidence_id,instrument_id,issuer_identity_id,evidence_type,source_type,source_locator,
                       source_authority_tier,source_published_at,source_available_at,captured_at,omnix_known_at,title,
                       content,content_hash,extraction_status,metadata,immutable_fingerprint
                  FROM omnix_trading_research_evidence
                 WHERE workspace_id=%s AND instrument_id=%s AND omnix_known_at<=%s
                   AND evidence_id IN ({placeholders})
            """, (self.context.workspace_id, instrument_id, known_at_lte, *ids)).fetchall()
        by_id = {
            r[0]: TradingEvidence(evidence_id=r[0],instrument_id=r[1],issuer_identity_id=r[2],evidence_type=r[3],source_type=r[4],
                source_locator=r[5],source_authority_tier=r[6],source_published_at=r[7],source_available_at=r[8],captured_at=r[9],
                omnix_known_at=r[10],title=r[11],content=r[12],content_hash=r[13],extraction_status=r[14],metadata=r[15],
                immutable_fingerprint=r[16])
            for r in rows
        }
        return [by_id[evidence_id] for evidence_id in ids if evidence_id in by_id]

    def save_action(self, item: ResearchActionRecord) -> ResearchActionRecord:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_research_actions
                (workspace_id,action_id,trace_id,strategy_id,instrument_id,step,operation,args,reason,status,result_summary,
                 evidence_ids,requested_at,completed_at,error_code,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id,item.action_id,item.trace_id,item.strategy_id,item.instrument_id,item.step,item.operation,
                  json.dumps(item.args,default=str),item.reason,item.status,json.dumps(item.result_summary,default=str),
                  json.dumps(list(item.evidence_ids)),item.requested_at,item.completed_at,item.error_code,item.immutable_fingerprint)).fetchone()
            uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def action_trace(self, trace_id: str) -> list[ResearchActionRecord]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute("""
                SELECT action_id,trace_id,strategy_id,instrument_id,step,operation,args,reason,status,result_summary,
                       evidence_ids,requested_at,completed_at,omnix_known_at,error_code,immutable_fingerprint
                  FROM omnix_trading_research_actions WHERE workspace_id=%s AND trace_id=%s ORDER BY step
            """, (self.context.workspace_id,trace_id)).fetchall()
        return [ResearchActionRecord(action_id=r[0],trace_id=r[1],strategy_id=r[2],instrument_id=r[3],step=r[4],operation=r[5],
            args=r[6],reason=r[7] or "",status=r[8],result_summary=r[9],evidence_ids=tuple(r[10]),requested_at=r[11],completed_at=r[12],
            omnix_known_at=r[13],error_code=r[14],immutable_fingerprint=r[15]) for r in rows]

    def next_report_version(self, instrument_id: str) -> int:
        with self.uow_factory() as uow:
            row = uow.connection.execute("SELECT COALESCE(MAX(report_version),0)+1 FROM omnix_trading_research_reports WHERE workspace_id=%s AND instrument_id=%s",
                (self.context.workspace_id,instrument_id)).fetchone()
        return int(row[0])

    def save_report(self, item: TradingResearchReport) -> TradingResearchReport:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_research_reports
                (workspace_id,report_id,report_version,contract_version,strategy_id,instrument_id,research_started_at,
                 research_completed_at,evidence_cutoff_at,catalyst_status,supply_status,research_status,coverage,
                 unresolved_facts,source_evidence_ids,hermes_trace_id,planner_backend,stop_reason,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id,item.report_id,item.report_version,item.contract_version,item.strategy_id,item.instrument_id,
                  item.research_started_at,item.research_completed_at,item.evidence_cutoff_at,item.catalyst_status,item.supply_status,
                  item.research_status,json.dumps(item.coverage.model_dump(mode="json")),json.dumps(list(item.unresolved_facts)),
                  json.dumps(list(item.source_evidence_ids)),item.hermes_trace_id,item.planner_backend,item.stop_reason,item.immutable_fingerprint)).fetchone()
            uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def report_timeline(self, instrument_id: str, limit: int = 100) -> list[TradingResearchReport]:
        return self._reports(instrument_id, None, limit)

    def latest_report_as_of(self, instrument_id: str, known_at_lte: datetime) -> TradingResearchReport | None:
        values = self._reports(instrument_id, known_at_lte, 1)
        return values[0] if values else None

    def _reports(self, instrument_id: str, cutoff: datetime | None, limit: int) -> list[TradingResearchReport]:
        condition = " AND omnix_known_at<=%s" if cutoff is not None else ""
        params = [self.context.workspace_id,instrument_id]
        if cutoff is not None: params.append(cutoff)
        params.append(limit)
        with self.uow_factory() as uow:
            rows = uow.connection.execute(f"""
                SELECT report_id,report_version,contract_version,strategy_id,instrument_id,research_started_at,research_completed_at,
                       evidence_cutoff_at,omnix_known_at,catalyst_status,supply_status,research_status,coverage,unresolved_facts,
                       source_evidence_ids,hermes_trace_id,planner_backend,stop_reason,immutable_fingerprint
                  FROM omnix_trading_research_reports WHERE workspace_id=%s AND instrument_id=%s{condition}
                 ORDER BY omnix_known_at DESC,report_version DESC LIMIT %s
            """, tuple(params)).fetchall()
        return [TradingResearchReport(report_id=r[0],report_version=r[1],contract_version=r[2],strategy_id=r[3],instrument_id=r[4],
            research_started_at=r[5],research_completed_at=r[6],evidence_cutoff_at=r[7],omnix_known_at=r[8],catalyst_status=r[9],
            supply_status=r[10],research_status=r[11],coverage=ResearchCoverage.model_validate(r[12]),unresolved_facts=tuple(r[13]),
            source_evidence_ids=tuple(r[14]),hermes_trace_id=r[15],planner_backend=r[16],stop_reason=r[17],immutable_fingerprint=r[18]) for r in rows]


def default_research_repository() -> TradingResearchRepository:
    return TradingResearchRepository()
