import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';

export type AnalyticsNumeric = string | number;
export type PaperAnalyticsMode = 'all' | 'shadow' | 'auto_paper';

export type PaperSimulationEpoch = components['schemas']['PaperSimulationEpoch'];

export type PaperEquityPoint = components['schemas']['PaperEquityPoint'];

export type PaperAnalyticsTrade = components['schemas']['PaperAnalyticsTrade'];

export type PaperPerformanceSummary = components['schemas']['PaperPerformanceSummary'];

export type PaperModeComparison = components['schemas']['PaperModeComparison'];

export type PaperDailyR = components['schemas']['PaperDailyR'];

export type PaperDrawdownPoint = components['schemas']['PaperDrawdownPoint'];

export type PaperRollingExpectancyPoint = components['schemas']['PaperRollingExpectancyPoint'];

export type PaperRDistributionBucket = components['schemas']['PaperRDistributionBucket'];

export type PaperMaeMfePoint = components['schemas']['PaperMaeMfePoint'];

export type PaperFunnelStage = components['schemas']['PaperFunnelStage'];

export type PaperExecutionSummary = components['schemas']['PaperExecutionSummary'];

export type PaperFactorBucket = components['schemas']['PaperFactorBucket'];

export type PaperFactorStudy = components['schemas']['PaperFactorStudy'];

export type PaperTradeJournalEvent = components['schemas']['PaperTradeJournalEvent'];

export type PaperTradeJournalEntry = components['schemas']['PaperTradeJournalEntry'];

export type PaperTradeJournalResponse = components['schemas']['PaperTradeJournalResponse'];

export type PaperAnalyticsOverview = components['schemas']['PaperAnalyticsOverview'];

export interface PaperAnalyticsFilters {
  accountId: string;
  strategyId?: string | null;
  epochId?: string | null;
  mode?: PaperAnalyticsMode;
  startDate?: string | null;
  endDate?: string | null;
  rollingWindow?: number;
}

export interface PaperJournalFilters {
  accountId: string;
  strategyId?: string | null;
  epochId?: string | null;
  startDate?: string | null;
  endDate?: string | null;
  limit?: number;
}

const analytics = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Paper analytics');

function defined<T extends Record<string, unknown>>(values: T): Partial<T> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== '')) as Partial<T>;
}

export const tradingPaperAnalyticsApi = {
  epochs: async (accountId: string): Promise<PaperSimulationEpoch[]> =>
    (await analytics(api.GET('/api/trading/paper-analytics/epochs', { params: { query: { account_id: accountId } } }))).epochs,
  overview: (filters: PaperAnalyticsFilters): Promise<PaperAnalyticsOverview> =>
    analytics(api.GET('/api/trading/paper-analytics/overview', {
      params: {
        query: {
          account_id: filters.accountId,
          ...defined({
            strategy_id: filters.strategyId,
            epoch_id: filters.epochId,
            mode: filters.mode,
            start_date: filters.startDate,
            end_date: filters.endDate,
            rolling_window: filters.rollingWindow || undefined,
          }),
        },
      },
    })),
  journal: (filters: PaperJournalFilters): Promise<PaperTradeJournalResponse> =>
    analytics(api.GET('/api/trading/paper-analytics/journal', {
      params: {
        query: {
          account_id: filters.accountId,
          ...defined({
            strategy_id: filters.strategyId,
            epoch_id: filters.epochId,
            start_date: filters.startDate,
            end_date: filters.endDate,
            limit: filters.limit || undefined,
          }),
        },
      },
    })),
};
