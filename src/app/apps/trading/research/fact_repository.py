from __future__ import annotations

import json
from datetime import datetime

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .contracts import (
    CatalystFactSet, ResearchCoverage, ResearchOutcome, ResearchValidationReport, StrategyResearchFeatures,
    SupplyFact, SupplyMetrics, TradingFactSet, ValidationFeatureResult,
)


class TradingFactRepository:
    context = RequestTenant()
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context = context; self.uow_factory = uow_factory

    def save_supply_fact(self, item: SupplyFact) -> SupplyFact:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_supply_facts
                (workspace_id,fact_id,schema_version,extractor_version,instrument_id,supply_type,status,shares,remaining_capacity_usd,
                 strike_price,exercise_status,registration_status,effective_at,expires_at,source_evidence_ids,resolution_status,
                 confidence,generated_at,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id,item.fact_id,item.schema_version,item.extractor_version,item.instrument_id,item.supply_type,
                  item.status,item.shares,item.remaining_capacity_usd,item.strike_price,item.exercise_status,item.registration_status,
                  item.effective_at,item.expires_at,json.dumps(list(item.source_evidence_ids)),item.resolution_status,item.confidence,
                  item.generated_at,item.immutable_fingerprint)).fetchone(); uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def save_fact_set(self, item: TradingFactSet) -> TradingFactSet:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_fact_sets
                (workspace_id,fact_set_id,schema_version,extractor_version,strategy_id,instrument_id,report_id,generated_at,catalyst,
                 supply,supply_metrics,completeness,unresolved_facts,evidence_ids,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id,item.fact_set_id,item.schema_version,item.extractor_version,item.strategy_id,item.instrument_id,
                  item.report_id,item.generated_at,json.dumps(item.catalyst.model_dump(mode="json")),json.dumps([x.model_dump(mode="json") for x in item.supply]),
                  json.dumps(item.supply_metrics.model_dump(mode="json")),json.dumps(item.completeness.model_dump(mode="json")),
                  json.dumps(list(item.unresolved_facts)),json.dumps(list(item.evidence_ids)),item.immutable_fingerprint)).fetchone(); uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def latest_fact_set_as_of(self, instrument_id: str, known_at_lte: datetime) -> TradingFactSet | None:
        with self.uow_factory() as uow:
            r = uow.connection.execute("""
                SELECT fact_set_id,schema_version,extractor_version,strategy_id,instrument_id,report_id,generated_at,omnix_known_at,
                       catalyst,supply,supply_metrics,completeness,unresolved_facts,evidence_ids,immutable_fingerprint
                  FROM omnix_trading_fact_sets WHERE workspace_id=%s AND instrument_id=%s AND omnix_known_at<=%s
                 ORDER BY omnix_known_at DESC LIMIT 1
            """, (self.context.workspace_id,instrument_id,known_at_lte)).fetchone()
        if r is None: return None
        return TradingFactSet(fact_set_id=r[0],schema_version=r[1],extractor_version=r[2],strategy_id=r[3],instrument_id=r[4],report_id=r[5],
            generated_at=r[6],omnix_known_at=r[7],catalyst=CatalystFactSet.model_validate(r[8]),supply=tuple(SupplyFact.model_validate(x) for x in r[9]),
            supply_metrics=SupplyMetrics.model_validate(r[10]),completeness=ResearchCoverage.model_validate(r[11]),unresolved_facts=tuple(r[12]),
            evidence_ids=tuple(r[13]),immutable_fingerprint=r[14])

    def save_features(self, item: StrategyResearchFeatures) -> StrategyResearchFeatures:
        with self.uow_factory() as uow:
            row = uow.connection.execute("""
                INSERT INTO omnix_trading_research_features
                (workspace_id,feature_id,projection_version,research_policy_version,strategy_id,instrument_id,fact_set_id,decision_at,features,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (workspace_id, immutable_fingerprint) DO UPDATE SET immutable_fingerprint=EXCLUDED.immutable_fingerprint
                RETURNING omnix_known_at
            """, (self.context.workspace_id,item.feature_id,item.projection_version,item.research_policy_version,item.strategy_id,item.instrument_id,
                  item.fact_set_id,item.decision_at,json.dumps(item.model_dump(mode="json",exclude={"omnix_known_at"})),item.immutable_fingerprint)).fetchone(); uow.commit()
        return item.model_copy(update={"omnix_known_at": row[0]})

    def research_features_as_of(self, instrument_id: str, decision_at: datetime, projection_version: str = "research-features-1") -> StrategyResearchFeatures | None:
        with self.uow_factory() as uow:
            r = uow.connection.execute("""
                SELECT features,omnix_known_at FROM omnix_trading_research_features
                 WHERE workspace_id=%s AND instrument_id=%s AND projection_version=%s AND decision_at<=%s AND omnix_known_at<=%s
                 ORDER BY decision_at DESC,omnix_known_at DESC LIMIT 1
            """, (self.context.workspace_id,instrument_id,projection_version,decision_at,decision_at)).fetchone()
        if r is None: return None
        payload=dict(r[0]); payload["omnix_known_at"]=r[1]
        return StrategyResearchFeatures.model_validate(payload)

    def save_outcome(self, item: ResearchOutcome) -> bool:
        with self.uow_factory() as uow:
            row=uow.connection.execute("""
                INSERT INTO omnix_trading_research_outcomes
                (workspace_id,outcome_id,session_date,strategy_id,instrument_id,strategy_version,research_policy_version,
                 feature_projection_version,market_fidelity,research_fidelity,research_status,features,strategy_state,rejection_reason,
                 entry_time,exit_time,mfe_r,mae_r,r_result,two_r_before_minus_one_r,time_to_mfe_minutes,time_to_stop_minutes,
                 data_quality_flags,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (workspace_id,immutable_fingerprint) DO NOTHING RETURNING outcome_id
            """, (self.context.workspace_id,item.outcome_id,item.session_date,item.strategy_id,item.instrument_id,item.strategy_version,
                  item.research_policy_version,item.feature_projection_version,item.market_fidelity,item.research_fidelity,item.research_status,
                  json.dumps(item.features,default=str),item.strategy_state,item.rejection_reason,item.entry_time,item.exit_time,item.mfe_r,item.mae_r,
                  item.r_result,item.two_r_before_minus_one_r,item.time_to_mfe_minutes,item.time_to_stop_minutes,json.dumps(list(item.data_quality_flags)),
                  item.immutable_fingerprint)).fetchone(); uow.commit()
        return row is not None

    def outcomes(self, strategy_id: str | None = None, limit: int = 10000) -> list[dict]:
        where="workspace_id=%s"; params=[self.context.workspace_id]
        if strategy_id: where += " AND strategy_id=%s"; params.append(strategy_id)
        params.append(limit)
        with self.uow_factory() as uow:
            rows=uow.connection.execute(f"""
                SELECT outcome_id,session_date,instrument_id,features,research_status,r_result,two_r_before_minus_one_r,
                       market_fidelity,research_fidelity,mfe_r,mae_r,data_quality_flags
                  FROM omnix_trading_research_outcomes
                 WHERE {where}
                 ORDER BY session_date DESC,created_at DESC LIMIT %s
            """,tuple(params)).fetchall()
        return [{
            "outcome_id":r[0],"session_date":r[1],"instrument_id":r[2],"features":r[3],"research_status":r[4],
            "r_result":r[5],"two_r_before_minus_one_r":r[6],"market_fidelity":r[7],"research_fidelity":r[8],
            "mfe_r":r[9],"mae_r":r[10],"data_quality_flags":tuple(r[11]),
        } for r in rows]

    def save_validation_report(self, item: ResearchValidationReport) -> bool:
        with self.uow_factory() as uow:
            row=uow.connection.execute("""
                INSERT INTO omnix_trading_research_validation_reports
                (workspace_id,validation_id,policy_version,generated_at,sample_size,exact_sample_size,feature_results,promotion_allowed,notes,immutable_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)
                ON CONFLICT (workspace_id,immutable_fingerprint) DO NOTHING RETURNING validation_id
            """,(self.context.workspace_id,item.validation_id,item.policy_version,item.generated_at,item.sample_size,item.exact_sample_size,
                  json.dumps([x.model_dump(mode="json") for x in item.feature_results]),item.promotion_allowed,json.dumps(list(item.notes)),item.immutable_fingerprint)).fetchone();uow.commit()
        return row is not None

    def promoted_validation_report(self, policy_version: str) -> ResearchValidationReport | None:
        with self.uow_factory() as uow:
            r=uow.connection.execute("SELECT validation_id,policy_version,generated_at,sample_size,exact_sample_size,feature_results,promotion_allowed,notes,immutable_fingerprint FROM omnix_trading_research_validation_reports WHERE workspace_id=%s AND policy_version=%s AND promotion_allowed=TRUE ORDER BY generated_at ASC LIMIT 1",(self.context.workspace_id,policy_version)).fetchone()
        if r is None:return None
        return ResearchValidationReport(validation_id=r[0],policy_version=r[1],generated_at=r[2],sample_size=r[3],exact_sample_size=r[4],
            feature_results=tuple(ValidationFeatureResult.model_validate(x) for x in r[5]),promotion_allowed=r[6],notes=tuple(r[7]),immutable_fingerprint=r[8])

    def latest_validation_report(self, policy_version: str) -> ResearchValidationReport | None:
        with self.uow_factory() as uow:
            r=uow.connection.execute("SELECT validation_id,policy_version,generated_at,sample_size,exact_sample_size,feature_results,promotion_allowed,notes,immutable_fingerprint FROM omnix_trading_research_validation_reports WHERE workspace_id=%s AND policy_version=%s ORDER BY generated_at DESC LIMIT 1",(self.context.workspace_id,policy_version)).fetchone()
        if r is None:return None
        return ResearchValidationReport(validation_id=r[0],policy_version=r[1],generated_at=r[2],sample_size=r[3],exact_sample_size=r[4],
            feature_results=tuple(ValidationFeatureResult.model_validate(x) for x in r[5]),promotion_allowed=r[6],notes=tuple(r[7]),immutable_fingerprint=r[8])


def default_fact_repository() -> TradingFactRepository:
    return TradingFactRepository()
