import type { MarketBar } from '../tradingTypes';
import type { CoreIndicatorInstance, IndicatorOutput } from './coreIndicators';
import type { components } from '../api/generated';
import { api } from '../api/gateway';
import { PRODUCT_NAME } from '../../../shared/productIdentity';

export type ExternalIndicatorScope = 'binance-crypto' | 'equity' | 'bitcoin' | 'market';
export type ExternalIndicatorDefinition = {
  id: string;
  metric: string;
  pane: 0 | 1;
  scope: ExternalIndicatorScope;
  /** Every series the indicator draws, by the metric's series keys; its output keys are `<id>:<series key>`. */
  seriesKeys: readonly string[];
  refreshMs: number;
  requirement: string;
};

type MetricPoint = { time: string; value: string | number };
type MetricSeries = {
  key: string;
  title: string;
  unit?: string | null;
  kind: 'line' | 'histogram';
  points: MetricPoint[];
};
type MetricResponse = components['schemas']['MarketMetricResponse'];

const BINANCE_REQUIREMENT = 'Available on Binance crypto symbols using public USD-M Futures market data.';
const EQUITY_REQUIREMENT = 'Available on equity symbols with a Yahoo market-data binding.';
const BITCOIN_REQUIREMENT = 'Available on BTC instruments using Blockchain.com public on-chain charts.';
const BREADTH_REQUIREMENT = `Market breadth of NYSE and Nasdaq stocks, computed by ${PRODUCT_NAME} from Alpaca daily bars after each close; on any symbol.`;

const EXTERNAL_INDICATORS: Record<string, ExternalIndicatorDefinition> = {
  'tv-open-interest': { id: 'tv-open-interest', metric: 'binance.open_interest', pane: 1, scope: 'binance-crypto', seriesKeys: ['open-interest'], refreshMs: 30_000, requirement: BINANCE_REQUIREMENT },
  'tv-understanding-crypto-open-interest': { id: 'tv-understanding-crypto-open-interest', metric: 'binance.open_interest', pane: 1, scope: 'binance-crypto', seriesKeys: ['open-interest'], refreshMs: 30_000, requirement: BINANCE_REQUIREMENT },
  'tv-funding-rate-a-guide-to-market-sentiment': { id: 'tv-funding-rate-a-guide-to-market-sentiment', metric: 'binance.funding_rate', pane: 1, scope: 'binance-crypto', seriesKeys: ['funding-rate'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-liquidation-data-what-to-watch-and-why-it-matters': { id: 'tv-liquidation-data-what-to-watch-and-why-it-matters', metric: 'binance.liquidations', pane: 1, scope: 'binance-crypto', seriesKeys: ['long-liquidations', 'short-liquidations'], refreshMs: 5_000, requirement: `${BINANCE_REQUIREMENT} Liquidation history starts when the ${PRODUCT_NAME} runtime collector starts.` },
  'tv-long-short-ratio-accounts': { id: 'tv-long-short-ratio-accounts', metric: 'binance.global_long_short_accounts', pane: 1, scope: 'binance-crypto', seriesKeys: ['ratio'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-long-short-accounts': { id: 'tv-long-short-accounts', metric: 'binance.global_long_short_accounts', pane: 1, scope: 'binance-crypto', seriesKeys: ['long-percent', 'short-percent'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-top-trader-long-short-accounts': { id: 'tv-top-trader-long-short-accounts', metric: 'binance.top_long_short_accounts', pane: 1, scope: 'binance-crypto', seriesKeys: ['long-percent', 'short-percent'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-top-trader-long-short-accounts-ratio': { id: 'tv-top-trader-long-short-accounts-ratio', metric: 'binance.top_long_short_accounts', pane: 1, scope: 'binance-crypto', seriesKeys: ['ratio'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-top-trader-long-short-positions': { id: 'tv-top-trader-long-short-positions', metric: 'binance.top_long_short_positions', pane: 1, scope: 'binance-crypto', seriesKeys: ['long-percent', 'short-percent'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-top-trader-long-short-positions-ratio': { id: 'tv-top-trader-long-short-positions-ratio', metric: 'binance.top_long_short_positions', pane: 1, scope: 'binance-crypto', seriesKeys: ['ratio'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-basis': { id: 'tv-basis', metric: 'binance.basis', pane: 1, scope: 'binance-crypto', seriesKeys: ['basis'], refreshMs: 60_000, requirement: BINANCE_REQUIREMENT },
  'tv-mark-price': { id: 'tv-mark-price', metric: 'binance.mark_price', pane: 0, scope: 'binance-crypto', seriesKeys: ['mark'], refreshMs: 30_000, requirement: BINANCE_REQUIREMENT },
  'tv-index-price': { id: 'tv-index-price', metric: 'binance.index_price', pane: 0, scope: 'binance-crypto', seriesKeys: ['index'], refreshMs: 30_000, requirement: BINANCE_REQUIREMENT },
  'tv-premium': { id: 'tv-premium', metric: 'binance.premium', pane: 1, scope: 'binance-crypto', seriesKeys: ['premium'], refreshMs: 30_000, requirement: BINANCE_REQUIREMENT },

  'tv-analyst-price-forecast': { id: 'tv-analyst-price-forecast', metric: 'yahoo.analyst_price_forecast', pane: 0, scope: 'equity', seriesKeys: ['target-low', 'target-mean', 'target-high'], refreshMs: 15 * 60_000, requirement: `${EQUITY_REQUIREMENT} Historical analyst-target snapshots are not fabricated.` },
  'tv-price-target-indicator': { id: 'tv-price-target-indicator', metric: 'yahoo.price_target', pane: 0, scope: 'equity', seriesKeys: ['target-low', 'target-mean', 'target-high'], refreshMs: 15 * 60_000, requirement: `${EQUITY_REQUIREMENT} Historical price-target snapshots are not fabricated.` },
  'tv-dividend-yield': { id: 'tv-dividend-yield', metric: 'yahoo.dividend_yield', pane: 1, scope: 'equity', seriesKeys: ['dividend-yield'], refreshMs: 60 * 60_000, requirement: `${EQUITY_REQUIREMENT} ${PRODUCT_NAME} calculates trailing-12-month dividends divided by current price.` },

  // TVP-6.6: market breadth (breadth.py), one line per exchange, on any chart.
  'tv-advance-decline-line': { id: 'tv-advance-decline-line', metric: 'breadth.ad_line', pane: 1, scope: 'market', seriesKeys: ['nyse', 'nasdaq'], refreshMs: 60 * 60_000, requirement: BREADTH_REQUIREMENT },
  'tv-advance-decline-ratio': { id: 'tv-advance-decline-ratio', metric: 'breadth.ad_ratio', pane: 1, scope: 'market', seriesKeys: ['nyse', 'nasdaq'], refreshMs: 60 * 60_000, requirement: BREADTH_REQUIREMENT },
  'tv-cumulative-volume-index-cvi': { id: 'tv-cumulative-volume-index-cvi', metric: 'breadth.cvi', pane: 1, scope: 'market', seriesKeys: ['nyse', 'nasdaq'], refreshMs: 60 * 60_000, requirement: BREADTH_REQUIREMENT },

  'tv-hash-rate': { id: 'tv-hash-rate', metric: 'blockchain.hash_rate', pane: 1, scope: 'bitcoin', seriesKeys: ['hash-rate'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-difficulty': { id: 'tv-difficulty', metric: 'blockchain.difficulty', pane: 1, scope: 'bitcoin', seriesKeys: ['difficulty'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-total-utxos': { id: 'tv-total-utxos', metric: 'blockchain.total_utxos', pane: 1, scope: 'bitcoin', seriesKeys: ['utxo-count'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-transaction-fees': { id: 'tv-transaction-fees', metric: 'blockchain.transaction_fees', pane: 1, scope: 'bitcoin', seriesKeys: ['transaction-fees'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-transaction-rate': { id: 'tv-transaction-rate', metric: 'blockchain.transaction_rate', pane: 1, scope: 'bitcoin', seriesKeys: ['transactions-per-second'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-blocks-mined': { id: 'tv-blocks-mined', metric: 'blockchain.blocks_mined', pane: 1, scope: 'bitcoin', seriesKeys: ['n-blocks-mined'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-mean-block-size-in-bytes': { id: 'tv-mean-block-size-in-bytes', metric: 'blockchain.mean_block_size_bytes', pane: 1, scope: 'bitcoin', seriesKeys: ['avg-block-size'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
  'tv-total-block-size-in-bytes': { id: 'tv-total-block-size-in-bytes', metric: 'blockchain.total_block_size_bytes', pane: 1, scope: 'bitcoin', seriesKeys: ['blocks-size'], refreshMs: 15 * 60_000, requirement: BITCOIN_REQUIREMENT },
};

const responseCache = new Map<string, { expiresAt: number; promise: Promise<MetricResponse | null> }>();

export function externalIndicatorDefinition(id: string): ExternalIndicatorDefinition | undefined {
  return EXTERNAL_INDICATORS[id];
}

export function isExternalIndicatorId(id: string): boolean {
  return Boolean(EXTERNAL_INDICATORS[id]);
}

export function externalIndicatorAvailableForInstrument(id: string, instrumentId: string): boolean {
  const definition = externalIndicatorDefinition(id);
  if (!definition) return false;
  if (definition.scope === 'binance-crypto') return /^crypto:BINANCE:/i.test(instrumentId);
  if (definition.scope === 'equity') return /^equity:/i.test(instrumentId);
  if (definition.scope === 'market') return true;
  return /^crypto:[^:]+:(?:spot|perpetual):BTC-/i.test(instrumentId);
}

/** An external indicator's output keys (`<id>:<series key>`), as the chart names its lines and the server evaluates them. */
export function externalIndicatorOutputKeys(id: string): string[] {
  return (externalIndicatorDefinition(id)?.seriesKeys ?? []).map((key) => `${id}:${key}`);
}

export function externalIndicatorRequirement(id: string): string | undefined {
  return externalIndicatorDefinition(id)?.requirement;
}

function defaultColor(seriesKey: string, index: number): string {
  if (seriesKey.includes('long-liquidations') || seriesKey.includes('short-percent') || seriesKey.includes('target-low')) return '#f23645';
  if (seriesKey.includes('short-liquidations') || seriesKey.includes('long-percent') || seriesKey.includes('target-high')) return '#20c997';
  if (seriesKey.includes('target-mean')) return '#4dabf7';
  const colors = ['#74c0fc', '#ffd43b', '#e599f7', '#ff922b', '#20c997', '#ff6b6b'];
  return colors[index % colors.length];
}

async function requestMetric(
  definition: ExternalIndicatorDefinition,
  instrumentId: string,
  interval: string,
  limit: number,
  endTime: string | undefined,
): Promise<MetricResponse | null> {
  const endBucket = endTime ? Math.floor(Date.parse(endTime) / Math.max(1_000, definition.refreshMs)) : 0;
  const key = `${definition.id}:${instrumentId}:${interval}:${limit}:${endBucket}`;
  const now = Date.now();
  const cached = responseCache.get(key);
  if (cached && cached.expiresAt > now) return cached.promise;

  const promise = api.GET('/api/trading/metrics', {
    params: {
      query: {
        instrument_id: instrumentId,
        metric: definition.metric,
        interval,
        limit: Math.max(1, Math.min(limit, 1_500)),
        ...(endTime ? { end_time: endTime } : {}),
      },
    },
  }).then(({ data, response }) => (response.ok && data ? data : null)).catch(() => null);
  responseCache.set(key, { expiresAt: now + definition.refreshMs, promise });
  return promise;
}

function visiblePoints(series: MetricSeries, bars: readonly MarketBar[]): Array<{ time: string; value: number }> {
  const points = series.points
    .map((point) => ({ time: point.time, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value) && Number.isFinite(Date.parse(point.time)))
    .sort((left, right) => Date.parse(left.time) - Date.parse(right.time));
  if (points.length !== 1 || bars.length < 2) return points;

  const first = bars[0]?.start_time;
  const last = bars.at(-1)?.end_time ?? bars.at(-1)?.start_time;
  if (!first || !last) return points;
  return [
    { time: first, value: points[0].value },
    { time: last, value: points[0].value },
  ];
}

export async function calculateExternalIndicatorOutputs(
  bars: readonly MarketBar[],
  indicator: CoreIndicatorInstance,
): Promise<IndicatorOutput[]> {
  const id = String(indicator.id);
  const definition = externalIndicatorDefinition(id);
  const instrumentId = bars[0]?.instrument_id;
  const interval = bars[0]?.interval;
  if (!definition || !instrumentId || !interval || !externalIndicatorAvailableForInstrument(id, instrumentId)) return [];

  const endTime = bars.at(-1)?.end_time ?? bars.at(-1)?.start_time;
  const response = await requestMetric(definition, instrumentId, interval, Math.max(100, bars.length), endTime);
  if (!response) return [];

  const allowed = new Set(definition.seriesKeys);
  return response.series
    .filter((series) => allowed.has(series.key))
    .map((series, index) => {
      const key = `${id}:${series.key}`;
      return {
        key,
        title: series.unit ? `${series.title} · ${series.unit}` : series.title,
        pane: definition.pane,
        kind: series.kind,
        points: visiblePoints(series, bars),
        visible: indicator.style?.plots?.[key] !== false,
        color: indicator.style?.colors?.[key] ?? defaultColor(series.key, index),
        lineStyle: indicator.style?.lineStyles?.[key] ?? 'solid',
        lineWidth: indicator.style?.lineWidth ?? 2,
        precision: indicator.style?.precision ?? null,
        labelsOnPriceScale: definition.pane === 0 && (indicator.style?.labelsOnPriceScale ?? true),
        valuesInStatusLine: indicator.style?.valuesInStatusLine ?? true,
        inputsInStatusLine: false,
      } satisfies IndicatorOutput;
    })
    .filter((output) => output.visible !== false && output.points.length > 0);
}

export const EXTERNAL_INDICATOR_IDS = Object.freeze(Object.keys(EXTERNAL_INDICATORS));
