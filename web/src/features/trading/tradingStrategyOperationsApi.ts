import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';
export type TradingHealthState = 'healthy' | 'degraded' | 'blocked' | 'unknown';

export type StrategyRuntimeMonitorStatus = components['schemas']['StrategyRuntimeMonitorStatus'];

export type TradingStrategyOperationsStatus = components['schemas']['StrategyOperationsStatus'];

export type AccountRiskHealth = components['schemas']['AccountRiskHealth'];

export type ExecutionHealth = components['schemas']['ExecutionHealth'];

export type TradingOperationalHealth = components['schemas']['TradingOperationalHealth'];

const operations = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading operations');

export const tradingStrategyOperationsApi = {
  status: (): Promise<TradingStrategyOperationsStatus> => operations(api.GET('/api/trading/strategy-operations/status')),
  health: (
    accountId: string,
    options: { instrumentId?: string | null; bindingId?: string | null } = {},
  ): Promise<TradingOperationalHealth> => operations(api.GET('/api/trading/strategy-operations/health', {
    params: {
      query: {
        account_id: accountId,
        ...(options.instrumentId ? { instrument_id: options.instrumentId } : {}),
        ...(options.bindingId ? { binding_id: options.bindingId } : {}),
      },
    },
  })),
};
