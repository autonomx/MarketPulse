/** Module-level helpers of the chart panel: visible ranges, comparisons, stream bars and its props type (WP-9.5 split them out of TradingChartPanel). */
import type { QueryClient } from '@tanstack/react-query';
import { type IChartApi } from 'lightweight-charts';
import { tradingApi } from './tradingApi';
import { DEFAULT_TRADING_RIGHT_OFFSET, type TradingChartType } from './chart/chartAdapter';
import type { TradingChartSynchronization } from './chart/chartSynchronization';
import { type ChartAlertPlacement } from './drawings/TradingDrawingOverlay';
import { type CoreIndicatorId, type CoreIndicatorInstance } from './indicators/coreIndicators';
import { type TradingIndicatorMove } from './tradingStore';
import type { BarsResponse, MarketBar, TradingAlertIndicatorId, TradingStreamMessage } from './tradingTypes';
import { type TradingComparison } from './tradingComparisons';
import { intervalCompactLabel, tradingIntervalMinutes } from './tradingIntervals';
import { zonedDateTimeToUtc } from './tradingTime';

export type TradingContextMenuState = ChartAlertPlacement & {
  contextIndicatorId?: CoreIndicatorId;
};

export function isAlertIndicatorId(id: CoreIndicatorId): id is TradingAlertIndicatorId {
  return id === 'sma'
    || id === 'ema'
    || id === 'rsi'
    || id === 'macd'
    || id === 'bollinger'
    || id === 'atr'
    || id === 'vwap'
    || id === 'stochastic-rsi';
}

export const indicatorContextNames: Partial<Record<CoreIndicatorId, string>> = {
  atr: 'ATR',
  bollinger: 'Bollinger Bands',
  'bull-market-band': 'Bull Market Support Band',
  'death-cross': 'Death Cross',
  ema: 'EMA',
  'ema-stack': 'EMA Stack',
  'fair-value-gap': 'Fair Value Gap',
  'golden-cross': 'Golden Cross',
  'ideal-bb': 'IDEAL BB',
  'log-macd': 'Log MACD',
  'macd-dema': 'MACD DEMA',
  macd: 'MACD',
  rsi: 'RSI',
  'rsi-divergence': 'RSI Divergence',
  sma: 'SMA',
  'stochastic-rsi': 'Stoch RSI',
  'swing-liquidity': 'Swing Levels and Liquidity',
  'volume-profile': 'Volume Profile',
  vwap: 'VWAP',
};

export function indicatorContextLabel(indicator: CoreIndicatorInstance): string {
  const name = indicatorContextNames[indicator.id] ?? indicator.id.toUpperCase();
  if (indicator.id === 'stochastic-rsi') {
    return `${name} (${indicator.fastPeriod ?? 3}, ${indicator.signalPeriod ?? 3}, ${indicator.period}, ${indicator.period}, close)`;
  }
  if (indicator.id === 'macd' || indicator.id === 'log-macd' || indicator.id === 'macd-dema') {
    return `${name} (${indicator.fastPeriod ?? 12}, ${indicator.slowPeriod ?? 26}, ${indicator.signalPeriod ?? 9}, close)`;
  }
  if (indicator.id === 'bollinger') return `${name} (${indicator.period}, ${indicator.standardDeviations ?? 2}, close)`;
  if (indicator.id === 'death-cross' || indicator.id === 'golden-cross') {
    return `${name} (${indicator.fastPeriod ?? 50}, ${indicator.slowPeriod ?? 200}, close)`;
  }
  if (indicator.id === 'bull-market-band') {
    return `${name} (${indicator.fastPeriod ?? 20}W SMA, ${indicator.slowPeriod ?? 21}W EMA)`;
  }
  return `${name} (${indicator.period}, close)`;
}

export const ranges = [
  { label: '1D', days: 1, interval: '1m', tooltip: '1 day in 1 minute intervals' },
  { label: '5D', days: 5, interval: '5m', tooltip: '5 days in 5 minute intervals' },
  { label: '1M', days: 30, interval: '30m', tooltip: '1 month in 30 minute intervals' },
  { label: '3M', days: 90, interval: '1h', tooltip: '3 months in 1 hour intervals' },
  { label: '6M', days: 180, interval: '2h', tooltip: '6 months in 2 hour intervals' },
  { label: 'YTD', days: 250, interval: '1d', tooltip: 'Year to date in 1 day intervals' },
  { label: '1Y', days: 365, interval: '1d', tooltip: '1 year in 1 day intervals' },
  { label: '5Y', days: 1_825, interval: '1w', tooltip: '5 years in 1 week intervals' },
  { label: 'All', days: null, interval: '1mo', tooltip: 'All available data in 1 month intervals' },
] as const;

export type CustomVisibleRange = {
  from: string;
  to: string;
};

export type SelectedVisibleRange = number | null | CustomVisibleRange;

export const rightOffsetStorageKey = 'omnix.trading.chart.right-offset';
export const rightOffsetOptions = [0, 5, DEFAULT_TRADING_RIGHT_OFFSET, 20, 50] as const;
export const Y_AXIS_DRAG_ZOOM_SENSITIVITY = 2;

export function readTradingRightOffset(): number {
  if (typeof window === 'undefined') return DEFAULT_TRADING_RIGHT_OFFSET;
  try {
    const stored = window.localStorage.getItem(rightOffsetStorageKey);
    if (stored === null) return DEFAULT_TRADING_RIGHT_OFFSET;
    const value = Number(stored);
    return rightOffsetOptions.includes(value as typeof rightOffsetOptions[number])
      ? value
      : DEFAULT_TRADING_RIGHT_OFFSET;
  } catch {
    return DEFAULT_TRADING_RIGHT_OFFSET;
  }
}

export function normalizeStreamBar(
  message: Extract<TradingStreamMessage, { type: 'bar' }>,
  provider: string,
  ingestionRevision: number,
): MarketBar {
  return {
    instrument_id: message.bar.instrument_id,
    interval: message.bar.interval,
    start_time: message.bar.start_time,
    end_time: message.bar.end_time,
    open: message.bar.open,
    high: message.bar.high,
    low: message.bar.low,
    close: message.bar.close,
    volume: message.bar.volume,
    is_final: message.bar.is_final,
    adjustment_mode: 'raw',
    session: '24x7',
    provider,
    provider_event_id: message.bar.provider_event_id,
    provider_sequence: message.bar.provider_sequence,
    ingestion_revision: ingestionRevision,
    received_at: new Date().toISOString(),
  };
}

export function price(value?: string | null): string {
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) return String(value ?? '—');
  const digits = Math.abs(parsed) >= 1_000 ? 2 : Math.abs(parsed) >= 1 ? 4 : 6;
  return parsed.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function convertedPrice(value: string | number | null | undefined, multiplier: number): string {
  const parsed = Number(value ?? 0);
  return price(Number.isFinite(parsed) ? String(parsed * multiplier) : null);
}

export function intervalMinutes(interval: string): number {
  return tradingIntervalMinutes(interval) ?? 1_440;
}

export function intervalLabel(interval: string): string {
  return intervalCompactLabel(interval);
}

export function closestSupportedInterval(target: string, supported: readonly string[]): string {
  if (supported.length === 0 || supported.includes(target)) return target;
  const targetMinutes = intervalMinutes(target);
  return [...supported].sort((left, right) => (
    Math.abs(intervalMinutes(left) - targetMinutes) - Math.abs(intervalMinutes(right) - targetMinutes)
  ))[0] ?? target;
}

export function applyVisibleRange(
  chart: IChartApi,
  selection: SelectedVisibleRange,
  bars: readonly MarketBar[],
  interval: string,
  rightOffset = DEFAULT_TRADING_RIGHT_OFFSET,
  timeZone = 'UTC',
): void {
  if (typeof selection === 'object' && selection !== null) {
    const fromTime = zonedDateTimeToUtc(selection.from, timeZone);
    const toTime = zonedDateTimeToUtc(selection.to, timeZone, true);
    if (fromTime === null || toTime === null || bars.length === 0) return;
    const startIndex = bars.findIndex((bar) => Date.parse(bar.start_time) >= fromTime);
    const endIndex = [...bars].reverse().findIndex((bar) => Date.parse(bar.start_time) <= toTime);
    const from = startIndex >= 0 ? startIndex : fromTime < Date.parse(bars[0].start_time) ? 0 : bars.length - 1;
    const to = endIndex >= 0 ? bars.length - 1 - endIndex : toTime < Date.parse(bars[0].start_time) ? 0 : bars.length - 1;
    const safeRightOffset = Math.max(0, Math.min(100, Math.round(rightOffset)));
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(-0.5, Math.min(from, to) - 0.5),
      to: Math.max(Math.max(from, to) + 0.5, Math.max(from, to) + 0.5 + safeRightOffset),
    });
    return;
  }
  const days = selection;
  const total = bars.length;
  if (total === 0) return;
  const safeRightOffset = Math.max(0, Math.min(100, Math.round(rightOffset)));
  if (days === null) {
    chart.timeScale().setVisibleLogicalRange({
      from: -0.5,
      to: total - 0.5 + safeRightOffset,
    });
    return;
  }
  const requested = Math.max(1, Math.ceil(days * 1_440 / intervalMinutes(interval)));
  const count = Math.min(total, requested);
  chart.timeScale().setVisibleLogicalRange({
    from: Math.max(-0.5, total - count - 0.5),
    to: total - 0.5 + safeRightOffset,
  });
}

export function comparisonPercent(bars: readonly MarketBar[]): string {
  const first = Number(bars[0]?.close);
  const last = Number(bars.at(-1)?.close);
  if (!Number.isFinite(first) || !Number.isFinite(last) || first === 0) return '—';
  return `${((last / first - 1) * 100).toFixed(2)}%`;
}

export const comparisonCurrencyNames: Record<string, string> = {
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  USD: 'U.S. Dollar',
  USDT: 'Tether',
  USDC: 'USD Coin',
};

export function comparisonLabel(instrument: { display_symbol: string; venue: string; asset_class?: string | null; base_currency?: string | null; quote_currency?: string | null } | undefined, fallback: string): string {
  if (!instrument) return fallback;
  if (instrument.asset_class === 'crypto') {
    const base = comparisonCurrencyNames[instrument.base_currency ?? ''] ?? instrument.base_currency ?? instrument.display_symbol;
    const quote = comparisonCurrencyNames[instrument.quote_currency ?? ''] ?? instrument.quote_currency ?? 'U.S. Dollar';
    return `${base} / ${quote} · ${instrument.venue}`;
  }
  return `${instrument.display_symbol} · ${instrument.venue}`;
}

export function chartHistoryLimit(
  instrumentId: string,
  interval: string,
  indicators: readonly CoreIndicatorInstance[],
): number {
  const needsExtendedHistory = indicators.some((indicator) => indicator.enabled && indicator.id === 'bull-market-band');
  if (instrumentId.startsWith('index:CRYPTOCAP:') && interval === '1d') return 5_000;
  if (instrumentId.startsWith('crypto:BINANCE:') && (['1d', '1w'].includes(interval) || needsExtendedHistory)) return 5_000;
  if (instrumentId.startsWith('equity:') && interval === '1d') return 2_000;
  return 1_000;
}

/** Bars for a comparison series, noting which instrument supplied older history. */
export type ComparisonBars = BarsResponse & { historySourceInstrumentId?: string };

export function longHistoryEquivalent(instrumentId: string, interval: string): string | null {
  if (!['1d', '1w'].includes(interval)) return null;
  const match = /^crypto:([^:]+):spot:([^:]+)-USD$/i.exec(instrumentId);
  if (!match) return null;
  return `crypto:${match[1]}:spot:${match[2]}-USDT`;
}

export function firstBarTime(response: BarsResponse): number {
  const value = Date.parse(response.bars[0]?.start_time ?? '');
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}

/** Comparison bars on the same clock-aligned buckets as the main chart, so derived intervals (7m, 2h) line up. */
export async function comparisonBars(
  instrumentId: string,
  interval: string,
  limit: number,
  extendedHours = true,
): Promise<ComparisonBars> {
  const options = { alignment: 'clock', extendedHours } as const;
  const selected = await tradingApi.bars(instrumentId, interval, limit, undefined, options);
  const equivalent = longHistoryEquivalent(instrumentId, interval);
  if (!equivalent || equivalent === instrumentId) return selected;
  try {
    const candidate = await tradingApi.bars(equivalent, interval, limit, undefined, options);
    if (candidate.bars.length > selected.bars.length && firstBarTime(candidate) < firstBarTime(selected)) {
      return { ...selected, bars: candidate.bars, historySourceInstrumentId: candidate.instrument.instrument_id };
    }
  } catch {
    // The selected instrument remains a valid comparison if its equivalent
    // long-history market is unavailable.
  }
  return selected;
}

/** Query key of a comparison's bars; compare series and compare-symbol indicators share it (placement does not change the data). */
export function comparisonBarsQueryKey(instrumentId: string, interval: string, limit: number, extendedHours = true): readonly unknown[] {
  return ['trading', 'comparison-bars-v3', instrumentId, interval, limit, 'clock', extendedHours];
}

const COMPARE_BAR_LIMITS = [1_000, 2_000, 5_000] as const;

/**
 * How many of the latest bars cover a time range from its start to now: the comparison series' own history size when that is
 * enough, else the next of 1,000, 2,000 and 5,000 (the API maximum). The few sizes keep the shared query cache small.
 */
export function compareBarsLimit(instrumentId: string, interval: string, from: number, now = Date.now()): number {
  const base = chartHistoryLimit(instrumentId, interval, []);
  const minutes = tradingIntervalMinutes(interval);
  if (!minutes || !Number.isFinite(from)) return base;
  const needed = Math.ceil((now - from) / (minutes * 60_000)) + 1;
  if (needed <= base) return base;
  return COMPARE_BAR_LIMITS.find((limit) => limit >= needed) ?? COMPARE_BAR_LIMITS[COMPARE_BAR_LIMITS.length - 1];
}

/** Bars of an indicator's compare symbol (Correlation Coefficient) over the chart's time range, through the comparison query cache. */
export async function loadCompareSymbolBars(
  queryClient: QueryClient,
  instrumentId: string,
  interval: string,
  range: { from: number; to: number },
  now = Date.now(),
  extendedHours = true,
): Promise<MarketBar[]> {
  const limit = compareBarsLimit(instrumentId, interval, range.from, now);
  const response = await queryClient.fetchQuery({
    queryKey: comparisonBarsQueryKey(instrumentId, interval, limit, extendedHours),
    queryFn: () => comparisonBars(instrumentId, interval, limit, extendedHours),
    staleTime: 15_000,
  });
  return response.bars as MarketBar[];
}

export type TradingChartPanelProps = {
  sessionId?: string;
  chartId: string;
  chartNumber: number;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  chartType: TradingChartType;
  indicators: CoreIndicatorInstance[];
  comparisons: TradingComparison[];
  active: boolean;
  chartFocusMode: boolean;
  onActivate: () => void;
  onChartFocusChange: (focused: boolean) => void;
  onOpenSymbolSearch: () => void;
  onChangeInterval: (interval: string) => void;
  onChangeChartType: (chartType: TradingChartType) => void;
  onToggleIndicator: (id: CoreIndicatorId) => void;
  onClearIndicators: () => void;
  onToggleIndicatorVisibility: (id: CoreIndicatorId) => void;
  onUpdateIndicator: (id: CoreIndicatorId, patch: Partial<CoreIndicatorInstance>) => void;
  onMoveIndicator: (id: CoreIndicatorId, direction: TradingIndicatorMove) => void;
  onUpdateComparisons: (comparisons: TradingComparison[]) => void;
  onOpenPineScript: (id: CoreIndicatorId) => void;
  onOpenMarketDataSettings?: () => void;
  synchronization: TradingChartSynchronization;
  paperAccountId?: string | null;
};
