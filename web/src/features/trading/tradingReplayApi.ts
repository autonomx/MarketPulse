import type { PaperAccountSnapshot, PaperOrder, PaperOrderInput } from './paperTypes';
import type { BacktestRunResult, FrozenDatasetSnapshot } from './replayTypes';
import { barCloseTime } from './replayClock';
import type { MarketBar } from './tradingTypes';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';

const replay = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading replay');

/** A bar sent to the replay kernel, with the feed binding of the replay session's data. */
export type ReplayExecutionMarketBar = MarketBar & { binding_id?: string | null };

/**
 * The kernel's form of a bar. An unreadable end time is sent as the derived
 * close the replay clock uses (start + interval); a bar with no knowable
 * close cannot be executed against.
 */
export function replayExecutionBar(bar: ReplayExecutionMarketBar) {
  const close = barCloseTime(bar);
  if (!Number.isFinite(close)) throw new Error('This replay bar has no known close time.');
  return {
    instrument_id: bar.instrument_id,
    binding_id: bar.binding_id ?? null,
    start_time: bar.start_time,
    end_time: Number.isFinite(Date.parse(bar.end_time)) ? bar.end_time : new Date(close).toISOString(),
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  };
}

export const tradingReplayApi = {
  datasets: async (): Promise<FrozenDatasetSnapshot[]> =>
    (await replay(api.GET('/api/trading/replay/datasets'))).datasets,
  freeze: (input: {
    dataset_id: string;
    instrument_id: string;
    binding_id?: string | null;
    interval: string;
    limit: number;
    gap_policy: 'fail' | 'skip';
  }): Promise<FrozenDatasetSnapshot> => replay(api.POST('/api/trading/replay/datasets', { body: input })),
  backtests: async () =>
    (await replay(api.GET('/api/trading/replay/backtests'))).runs,
  runBacktest: (datasetId: string, input: {
    fast_period: number;
    slow_period: number;
    initial_cash: string;
    commission_bps: string;
    slippage_bps: string;
    /** The strategy may go short on a sell signal (TVP-7.2a). */
    allow_short?: boolean;
  }): Promise<BacktestRunResult> => replay(api.POST('/api/trading/replay/backtests', {
    body: {
      dataset_id: datasetId,
      request: {
        strategy: {
          strategy_id: 'sma_cross',
          fast_period: input.fast_period,
          slow_period: input.slow_period,
        },
        execution_policy: {
          fill_timing: 'next_bar_open',
          commission_bps: input.commission_bps,
          slippage_bps: input.slippage_bps,
          position_size_fraction: '1',
          allow_short: input.allow_short ?? false,
          use_finalized_bars_only: true,
        },
        initial_cash: input.initial_cash,
        formula_version: 'omnix-indicators-v2',
      },
    },
  })),
  backtest: (runId: string): Promise<BacktestRunResult> =>
    replay(api.GET('/api/trading/replay/backtests/{run_id}', { params: { path: { run_id: runId } } })),
  advanceExecution: (snapshot: PaperAccountSnapshot, bar: ReplayExecutionMarketBar): Promise<PaperAccountSnapshot> =>
    replay(api.POST('/api/trading/replay/execution/advance', { body: { snapshot, bar: replayExecutionBar(bar) } })),
  /** `advanceBar: false` when the snapshot has already been advanced through `bar`. */
  placeExecutionOrder: (snapshot: PaperAccountSnapshot, order: PaperOrderInput, bar: ReplayExecutionMarketBar, advanceBar = true): Promise<{ snapshot: PaperAccountSnapshot; order: PaperOrder }> =>
    replay(api.POST('/api/trading/replay/execution/orders', { body: { snapshot, order, bar: replayExecutionBar(bar), advance_bar: advanceBar } })),
};