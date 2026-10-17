/**
 * Omnix Scripts over HTTP (TVP-11.1–11.3): check a script, run it on a chart's bars on the server, the names the
 * editor completes, and a script's saved versions. Scripts themselves are trading documents (`tradingApi`, kind
 * `scripts`, payload {@link ScriptPayload}).
 */
import { unwrapLabelled } from '../../../api/http';
import type { components } from '../api/generated';
import { api } from '../api/gateway';
import { PRODUCT_NAME } from '../../../shared/productIdentity';

const scripts = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, `${PRODUCT_NAME} Scripts`);

export type ScriptDiagnostic = components['schemas']['ScriptDiagnostic'];
export type ScriptCheckResult = components['schemas']['ScriptCheckResponse'];
export type ScriptReference = components['schemas']['ScriptReferenceResponse'];
export type ScriptVersionSummary = components['schemas']['ScriptVersionSummary'];
export type ScriptVersion = components['schemas']['ScriptVersion'];
export type ScriptScreenResponse = components['schemas']['ScriptScreenResponse'];
export type ScriptScreenRow = components['schemas']['ScriptScreenRow'];
export type ScriptScreenOutput = components['schemas']['ScriptScreenOutput'];

/** A script document's payload. */
export type ScriptPayload = { name: string; source: string };

/** An input a script declares (`input.*`), as the server reports it. */
export type ScriptInput = { title: string; type: string; default: unknown; options?: Record<string, unknown> };
/** One `plot*`, `bgcolor`, `barcolor` or `alertcondition` call: a value per bar, and a colour per bar where it varies. */
export type ScriptPlot = {
  index: number;
  kind: string;
  title: string;
  options: Record<string, unknown>;
  values: unknown[];
  colors: unknown[] | null;
};
export type ScriptDrawing = { kind: string; id: number; fields: Record<string, unknown> };
/** A strategy's closed or open trade (TVP-11.5); times are epoch milliseconds, bars the run's bar indexes. */
export type StrategyTrade = {
  number: number; entry_id: string; direction: 'long' | 'short'; qty: number; entry_bar: number; entry_time: number; entry_price: number;
  entry_comment: string | null; exit_id: string | null; exit_bar: number | null; exit_time: number | null; exit_price: number | null;
  exit_comment: string | null; profit: number; profit_percent: number; cum_profit: number; runup: number; drawdown: number; bars: number;
  commission: number;
};
export type StrategySummary = Record<string, number | null>;
/** A strategy script's backtest (`scripts/strategy.py` `Broker.report`): a simulated account, never an order. */
export type StrategyReport = {
  settings: Record<string, string | number | boolean>;
  summary: { all: StrategySummary; long: StrategySummary; short: StrategySummary };
  trades: StrategyTrade[];
  trades_total: number;
  open_trades: StrategyTrade[];
  fills: Array<{ bar: number; time: number; price: number; qty: number; side: 'buy' | 'sell'; id: string; comment: string | null; position: number }>;
  equity: number[];
  drawdown: number[];
  buy_hold: number[];
};

/** A run's result (`scripts/worker.py` `result_payload`). */
export type ScriptRunResult = {
  declaration: { kind?: string; title?: string; shorttitle?: string; overlay?: boolean; precision?: number } & Record<string, unknown>;
  inputs: ScriptInput[];
  plots: ScriptPlot[];
  hlines: Array<{ price: unknown } & Record<string, unknown>>;
  fills: Array<Record<string, unknown>>;
  drawings: ScriptDrawing[];
  alerts: Array<Record<string, unknown>>;
  logs: Array<{ time: number | null; bar: number; level: string; message: string }>;
  profile: Array<{ line: number; seconds: number }>;
  bars: number;
  seconds: number;
  /** A strategy() script's backtest on the run's bars (TVP-11.5). */
  strategy?: StrategyReport | null;
};
export type ScriptRunOutput = { times: string[]; result: ScriptRunResult | null; error: ScriptDiagnostic | null };
export type ScriptRunOptions = {
  source: string;
  instrumentId: string;
  bindingId?: string | null;
  interval: string;
  inputs?: Record<string, unknown>;
  limit?: number;
  profile?: boolean;
};

export const scriptsApi = {
  check: (source: string): Promise<ScriptCheckResult> => scripts(api.POST('/api/trading/scripts/check', { body: { source } })),
  run: async (request: ScriptRunOptions): Promise<ScriptRunOutput> => {
    const response = await scripts(api.POST('/api/trading/scripts/run', {
      body: {
        source: request.source,
        instrument_id: request.instrumentId,
        binding_id: request.bindingId ?? null,
        interval: request.interval,
        inputs: request.inputs ?? {},
        limit: Math.max(10, Math.min(5_000, Math.round(request.limit ?? 1_000))),
        profile: request.profile ?? false,
      },
    }));
    return { times: response.times, result: (response.result ?? null) as ScriptRunResult | null, error: response.error ?? null };
  },
  /** A strategy over all the history the provider serves, up to `bars` (TVP-11.5); the result has no plots. */
  backtest: async (request: Omit<ScriptRunOptions, 'limit' | 'profile'> & { bars?: number }): Promise<ScriptRunOutput> => {
    const response = await scripts(api.POST('/api/trading/scripts/backtest', {
      body: {
        source: request.source, instrument_id: request.instrumentId, binding_id: request.bindingId ?? null, interval: request.interval,
        inputs: request.inputs ?? {}, bars: Math.max(10, Math.min(20_000, Math.round(request.bars ?? 20_000))),
      },
    }));
    return { times: response.times, result: (response.result ?? null) as ScriptRunResult | null, error: response.error ?? null };
  },
  /** One script on each symbol of a list (TVP-11.6): every output's last two values per symbol. */
  screen: (request: { source: string; instrumentIds: string[]; interval: string; inputs?: Record<string, unknown>; limit?: number }): Promise<ScriptScreenResponse> =>
    scripts(api.POST('/api/trading/scripts/screen', {
      body: { source: request.source, instrument_ids: request.instrumentIds, interval: request.interval, inputs: request.inputs ?? {}, limit: request.limit ?? 500 },
    })),
  reference: (): Promise<ScriptReference> => scripts(api.GET('/api/trading/scripts/reference')),
  versions: async (scriptId: string): Promise<ScriptVersionSummary[]> =>
    (await scripts(api.GET('/api/trading/scripts/{record_id}/versions', { params: { path: { record_id: scriptId } }, cache: 'no-store' }))).versions,
  version: (scriptId: string, revision: number): Promise<ScriptVersion> =>
    scripts(api.GET('/api/trading/scripts/{record_id}/versions/{revision}', { params: { path: { record_id: scriptId, revision } } })),
};
