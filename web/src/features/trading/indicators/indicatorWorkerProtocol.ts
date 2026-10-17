import type { MarketBar } from '../tradingTypes';
import type { CoreIndicatorInstance, IndicatorOutput } from './coreIndicators';
import type { TradingSessionSpec } from './tradingSessions';

export type IndicatorWorkerRequest = {
  requestId: number;
  bars: MarketBar[];
  indicators: CoreIndicatorInstance[];
  /** Bars of each indicator's `compareSymbol`, keyed by that symbol. */
  compareBars?: Record<string, MarketBar[]>;
  /** The chart instrument's session calendar for session-aware built-ins; UTC when absent. */
  session?: TradingSessionSpec;
};

export type IndicatorWorkerResponse =
  | { requestId: number; outputs: IndicatorOutput[] }
  | { requestId: number; error: string };
