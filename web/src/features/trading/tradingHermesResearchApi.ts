import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';


// Trading research payloads as the gateway sends them (WP-9.3).
export type HermesResearchCoverage = components['schemas']['ResearchCoverage'];
export type HermesResearchReport = components['schemas']['TradingResearchReport'];
export type HermesResearchEvidence = components['schemas']['TradingEvidence'];
export type HermesMarketBriefItem = components['schemas']['TradingMarketBriefItem'];
export type HermesMarketBrief = components['schemas']['TradingMarketBrief'];
export type HermesSupplyFact = components['schemas']['SupplyFact'];
export type HermesResearchFactSet = components['schemas']['TradingFactSet'];
export type HermesResearchFeatures = components['schemas']['StrategyResearchFeatures'];
export type HermesResearchAction = components['schemas']['ResearchActionRecord'];
export type HermesShadowAnnotation = components['schemas']['NoveltyShadowAnnotation'];
export type HermesResearchAudit = components['schemas']['TradingResearchAuditView'];
export type HermesResearchValidation = components['schemas']['ResearchValidationReport'];
export type HermesResearchStart = components['schemas']['TradingResearchCoordinatorResult'];
export type ResearchCoverageState = HermesResearchCoverage['atm'];
export type ResearchRecommendation = HermesResearchValidation['feature_results'][number]['recommendation'];

const research = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading research');

export const tradingHermesResearchApi = {
  start: (instrumentId: string, strategyId?: string | null): Promise<HermesResearchStart> =>
    research(api.POST('/api/trading/hermes-research/start', {
      body: {
        instrument_id: instrumentId,
        strategy_id: strategyId || null,
        deadline_seconds: 45,
        max_steps: 8,
        max_queries: 5,
        max_sources: 20,
        max_extracts: 8,
        run_shadow_ai: true,
      },
    })),
  audit: (instrumentId: string, asOf?: string): Promise<HermesResearchAudit> =>
    research(api.GET('/api/trading/hermes-research/audit', {
      params: { query: { instrument_id: instrumentId, ...(asOf ? { as_of: asOf } : {}) } },
    })),
  attribution: (strategyId: string): Promise<Record<string, unknown>> =>
    research(api.GET('/api/trading/hermes-research/attribution', { params: { query: { strategy_id: strategyId } } })),
  validate: (strategyId: string): Promise<HermesResearchValidation> =>
    research(api.POST('/api/trading/hermes-research/validate', {
      body: { strategy_id: strategyId, policy_version: 'trading-research-1', minimum_sample: 100, minimum_exact_sample: 50 },
    })),
  validation: (policyVersion = 'trading-research-1'): Promise<HermesResearchValidation | null> =>
    research(api.GET('/api/trading/hermes-research/validation/{policy_version}', { params: { path: { policy_version: policyVersion } } })),
  reviewValidation: (
    sourceValidationId: string,
    approvedRecommendations: Record<string, ResearchRecommendation>,
    reviewNote: string,
  ): Promise<HermesResearchValidation> =>
    research(api.POST('/api/trading/hermes-research/validation/review', {
      body: {
        source_validation_id: sourceValidationId,
        policy_version: 'trading-research-1',
        approved_recommendations: approvedRecommendations,
        review_note: reviewNote,
        confirm_execution_authority: true,
      },
    })),
};
