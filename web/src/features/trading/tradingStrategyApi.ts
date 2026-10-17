/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import type {
  FinvizGapperDiscoveryInput,
  GapperUniverse,
  GapperUniverseFreezeInput,
  ProspectiveEconomicHoldoutReviewInput,
  ProspectiveEconomicStatus,
  StrategyCatalystCaptureResponse,
  StrategyEvent,
  StrategyProtection,
  StrategyRangeBacktestInput,
  StrategyRangeBacktestAccepted,
  StrategyRangeBacktestProgress,
  StrategyRangeBacktestResult,
  StrategyResearchReviewResponse,
  TradingStrategyConfig,
  V2ProspectiveQualification,
  YahooGapperDiscoveryInput,
} from './tradingStrategyTypes';
import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';

const DEEP_RECOVERY_EVENT_TYPES = new Set(['deep_recovery_state', 'deep_recovery_shadow']);
const PROSPECTIVE_ECONOMIC_EVENT_TYPES = new Set([
  'prospective_economic_candidate',
  'prospective_economic_signal',
  'prospective_economic_outcome',
  'prospective_economic_evaluation',
  'prospective_economic_holdout_review',
  'prospective_economic_auto_paper_review',
]);

export type StrategyRuntimeMonitorStatus = components['schemas']['StrategyRuntimeMonitorStatus'];

export type TradingStrategyOperationsStatus = components['schemas']['StrategyOperationsStatus'];

const strategyCall = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading strategy');
const strategyPath = (strategyId: string) => ({ strategy_id: strategyId });

/**
 * The contract types a strategy's kind and config separately; the editor works
 * with the discriminated union (the gateway validates the pairing).
 */
function asStrategyConfig(document: components['schemas']['TradingStrategyConfigDocument-Output']): TradingStrategyConfig {
  return document as unknown as TradingStrategyConfig;
}

function strategyBody(config: TradingStrategyConfig): components['schemas']['TradingStrategyConfigDocument-Input'] {
  return config as unknown as components['schemas']['TradingStrategyConfigDocument-Input'];
}

async function strategyEvents(strategyId: string, limit: number): Promise<StrategyEvent[]> {
  return (await strategyCall(api.GET('/api/trading/strategies/{strategy_id}/events', {
    params: { path: strategyPath(strategyId), query: { limit } },
  }))).events;
}

export const tradingStrategyApi = {
  list: async (): Promise<TradingStrategyConfig[]> =>
    (await strategyCall(api.GET('/api/trading/strategies'))).strategies.map(asStrategyConfig),
  get: async (strategyId: string): Promise<TradingStrategyConfig> =>
    asStrategyConfig(await strategyCall(api.GET('/api/trading/strategies/{strategy_id}', { params: { path: strategyPath(strategyId) } }))),
  create: async (config: TradingStrategyConfig): Promise<TradingStrategyConfig> =>
    asStrategyConfig(await strategyCall(api.POST('/api/trading/strategies', { body: strategyBody(config) }))),
  update: async (config: TradingStrategyConfig): Promise<TradingStrategyConfig> =>
    asStrategyConfig(await strategyCall(api.PUT('/api/trading/strategies/{strategy_id}', {
      params: { path: strategyPath(config.strategy_id), header: { 'If-Match': config.revision } },
      body: strategyBody(config),
    }))),
  delete: async (config: TradingStrategyConfig): Promise<void> => {
    await strategyCall(api.DELETE('/api/trading/strategies/{strategy_id}', {
      params: { path: strategyPath(config.strategy_id), header: { 'If-Match': config.revision } },
    }));
  },
  backtestRange: (strategyId: string, input: StrategyRangeBacktestInput): Promise<StrategyRangeBacktestAccepted> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/backtest/range', { params: { path: strategyPath(strategyId) }, body: input })),
  backtestRangeProgress: (strategyId: string, runId: string): Promise<StrategyRangeBacktestProgress> =>
    strategyCall(api.GET('/api/trading/strategies/{strategy_id}/backtest/range/{run_id}', {
      params: { path: { strategy_id: strategyId, run_id: runId } },
    })),
  events: async (strategyId: string, limit = 200) => {
    const rows = await strategyEvents(strategyId, Math.max(limit, 500));
    return rows.filter(
      (event) => !DEEP_RECOVERY_EVENT_TYPES.has(event.event_type)
        && !PROSPECTIVE_ECONOMIC_EVENT_TYPES.has(event.event_type),
    ).slice(0, limit);
  },
  deepRecoveryEvents: async (strategyId: string, limit = 200) => {
    const rows = await strategyEvents(strategyId, Math.max(limit, 500));
    return rows.filter((event) => DEEP_RECOVERY_EVENT_TYPES.has(event.event_type)).slice(0, limit);
  },
  prospectiveEconomicEvents: async (strategyId: string, limit = 500): Promise<StrategyEvent[]> =>
    (await strategyCall(api.GET('/api/trading/strategies/{strategy_id}/prospective-economic/events', {
      params: { path: strategyPath(strategyId), query: { limit } },
    }))).events,
  prospectiveEconomic: (strategyId: string): Promise<ProspectiveEconomicStatus> =>
    strategyCall(api.GET('/api/trading/strategies/{strategy_id}/prospective-economic', { params: { path: strategyPath(strategyId) } })),
  evaluateProspectiveEconomic: (strategyId: string, reviewNote: string): Promise<ProspectiveEconomicStatus> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/prospective-economic/evaluate', {
      params: { path: strategyPath(strategyId) },
      body: { review_note: reviewNote },
    })),
  reviewProspectiveEconomicHoldout: (strategyId: string, input: ProspectiveEconomicHoldoutReviewInput): Promise<ProspectiveEconomicStatus> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/prospective-economic/holdout-review', {
      params: { path: strategyPath(strategyId) },
      body: input,
    })),
  reviewProspectiveEconomicAutoPaper: (strategyId: string, reviewNote: string): Promise<ProspectiveEconomicStatus> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/prospective-economic/auto-paper-review', {
      params: { path: strategyPath(strategyId) },
      body: { review_note: reviewNote },
    })),
  protections: async (strategyId: string): Promise<StrategyProtection[]> =>
    (await strategyCall(api.GET('/api/trading/strategies/{strategy_id}/protections', {
      params: { path: strategyPath(strategyId), query: { active_only: true } },
    }))).protections,
  operationsStatus: (): Promise<TradingStrategyOperationsStatus> =>
    strategyCall(api.GET('/api/trading/strategy-operations/status')),
  v2Qualification: (strategyId: string): Promise<V2ProspectiveQualification> =>
    strategyCall(api.GET('/api/trading/strategies/{strategy_id}/v2/qualification', { params: { path: strategyPath(strategyId) } })),
  reviewV2Qualification: (strategyId: string, reviewNote: string): Promise<V2ProspectiveQualification> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/v2/qualification/review', {
      params: { path: strategyPath(strategyId) },
      body: { review_note: reviewNote },
    })),
  discoverYahooUniverse: (input: YahooGapperDiscoveryInput): Promise<GapperUniverse> =>
    strategyCall(api.POST('/api/trading/strategies/universes/discover-yahoo', { body: input })),
  discoverFinvizUniverse: (input: FinvizGapperDiscoveryInput): Promise<GapperUniverse> =>
    strategyCall(api.POST('/api/trading/strategies/universes/discover-finviz', { body: input })),
  freezeUniverse: (input: GapperUniverseFreezeInput): Promise<GapperUniverse> =>
    strategyCall(api.POST('/api/trading/strategies/universes/freeze', { body: input })),
  universe: (universeId: string): Promise<GapperUniverse> =>
    strategyCall(api.GET('/api/trading/strategies/universes/{universe_id}', { params: { path: { universe_id: universeId } } })),
  captureYahooResearch: (strategyId: string, lookbackHours = 72, maxItemsPerCandidate = 8): Promise<StrategyCatalystCaptureResponse> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/research/capture-yahoo', {
      params: { path: strategyPath(strategyId) },
      body: { lookback_hours: lookbackHours, max_items_per_candidate: maxItemsPerCandidate },
    })),
  runLlmResearch: (strategyId: string, model?: string): Promise<StrategyResearchReviewResponse> =>
    strategyCall(api.POST('/api/trading/strategies/{strategy_id}/research/llm-review', {
      params: { path: strategyPath(strategyId) },
      body: { model: model?.trim() || null },
    })),
};