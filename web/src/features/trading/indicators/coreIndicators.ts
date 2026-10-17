/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import type { IndicatorSourceRef } from './indicatorSources';
import type { MarketBar } from '../tradingTypes';
import { autoChartPatternLines, isAutoChartPatternId, type AutoChartPatternId } from './autoPatterns';

export const CORE_INDICATOR_FORMULA_VERSION = 'omnix-indicators-v2';

/** A value at a bar; `color` paints that bar's column, line segment, bar or background (TVP-6.2); `label` marks it. */
/** A value per bar; a `candles` output's points also carry the candle's open, high and low (`value` is its close). */
export type IndicatorPoint = { time: string; value: number; color?: string; label?: string; open?: number; high?: number; low?: number };
/** A table an Omnix Script draws over the chart (TVP-11.1), at one of nine positions; cells by row, then column. */
export type IndicatorTable = {
  position: string;
  rows: Array<Array<{ text: string; color?: string; background?: string } | null>>;
  background?: string;
  border?: string;
};
/**
 * How an output is drawn: a line or histogram series; `bar-colors` recolours the chart's own bars; `background`
 * shades the price pane behind the bars (full height, per bar); `viewport-average` is a price line at the average close
 * of the bars in view (Visible Average Price) and has no points of its own; `candles` draws each point as a candle in
 * its pane (Volume Delta, CVD); `fill` shades between two values per bar (each point's `high` and `low`, in its colour);
 * `table` has no points and is drawn over the chart from its `table` (Omnix Scripts, TVP-11.1).
 */
export type IndicatorOutputKind = 'line' | 'histogram' | 'bar-colors' | 'background' | 'viewport-average' | 'candles' | 'fill' | 'table';
export type IndicatorLineStyle = 'solid' | 'dotted' | 'dashed' | 'large-dashed' | 'sparse-dotted';
export type IndicatorPaneScale = {
  min: number;
  max: number;
  band?: { from: number; to: number; color: string };
  levels: readonly { value: number; lineStyle: IndicatorLineStyle }[];
};
export type VolumeProfileBin = {
  low: number;
  high: number;
  volume: number;
};
export type VolumeProfileData = {
  bins: readonly VolumeProfileBin[];
  poc: number;
  pocIndex: number;
  valueAreaHigh: number;
  valueAreaLow: number;
  maxVolume: number;
};
export type CoreIndicatorStyle = {
  plots?: Record<string, boolean>;
  colors?: Record<string, string>;
  lineStyles?: Record<string, IndicatorLineStyle>;
  lineWidth?: 1 | 2 | 3 | 4;
  backgroundVisible?: boolean;
  backgroundColor?: string;
  precision?: number | null;
  labelsOnPriceScale?: boolean;
  valuesInStatusLine?: boolean;
  inputsInStatusLine?: boolean;
};
export type IndicatorOutput = {
  key: string;
  title: string;
  pane: 0 | 1;
  kind: IndicatorOutputKind;
  points: IndicatorPoint[];
  visible?: boolean;
  color?: string;
  lineStyle?: IndicatorLineStyle;
  lineWidth?: 1 | 2 | 3 | 4;
  backgroundVisible?: boolean;
  backgroundColor?: string;
  precision?: number | null;
  labelsOnPriceScale?: boolean;
  valuesInStatusLine?: boolean;
  inputsInStatusLine?: boolean;
  volumeProfile?: VolumeProfileData;
  /** Line outputs: joined (default), horizontal levels with gaps on bars without a value, or point markers. */
  render?: 'line' | 'levels' | 'markers';
  marker?: 'arrowUp' | 'arrowDown' | 'circle';
  /** Markers: text beside each marker, and above or below the bar instead of at the point's price (candlestick patterns). */
  markerText?: string;
  markerPosition?: 'aboveBar' | 'belowBar';
  /** A pane-1 output drawn in another indicator's pane (an indicator on that indicator, TVP-6.5). */
  paneOf?: string;
  /** Candles: drawn as OHLC bars instead (an Omnix Script's plotbar). */
  barStyle?: 'candles' | 'bars';
  /** `table` outputs: the table to draw. */
  table?: IndicatorTable;
};
export type CoreIndicatorId =
  | 'sma' | 'ema' | 'rsi' | 'macd' | 'bollinger' | 'atr' | 'vwap'
  | 'bull-market-band' | 'death-cross' | 'ema-stack' | 'fair-value-gap' | 'golden-cross'
  | 'ideal-bb' | 'log-macd' | 'macd-dema' | 'rsi-divergence' | 'stochastic-rsi'
  | 'swing-liquidity' | 'volume-profile' | AutoChartPatternId;
export type CoreIndicatorInstance = {
  id: CoreIndicatorId;
  period: number;
  enabled: boolean;
  visible?: boolean;
  fastPeriod?: number;
  slowPeriod?: number;
  signalPeriod?: number;
  standardDeviations?: number;
  anchorTime?: string | null;
  /** Instrument id of the second series, for indicators that read one (Correlation Coefficient). */
  compareSymbol?: string | null;
  /** Built-in inputs beyond the period (`tradingViewBuiltInInputs`), by key. */
  params?: Record<string, number | string>;
  /** Another chart indicator's output read instead of the close (TVP-6.5, `indicatorSources.ts`); none reads the close. */
  source?: IndicatorSourceRef | null;
  style?: CoreIndicatorStyle;
};

export function indicatorDefaultBackgroundColor(id: CoreIndicatorId): string {
  if (id === 'bull-market-band') return '#40ad50';
  if (id === 'fair-value-gap') return '#ff922b';
  return '#74c0fc';
}

export function indicatorUsesSeparatePane(id: CoreIndicatorId): boolean {
  return id === 'rsi'
    || id === 'macd'
    || id === 'atr'
    || id === 'log-macd'
    || id === 'macd-dema'
    || id === 'rsi-divergence'
    || id === 'stochastic-rsi';
}

export function indicatorPaneScale(id: CoreIndicatorId): IndicatorPaneScale | null {
  if (id === 'rsi') {
    return {
      min: 0,
      max: 100,
      band: { from: 30, to: 70, color: '#74c0fc' },
      levels: [
        { value: 30, lineStyle: 'dashed' },
        { value: 50, lineStyle: 'dotted' },
        { value: 70, lineStyle: 'dashed' },
      ],
    };
  }
  if (id === 'stochastic-rsi') {
    return {
      min: 0,
      max: 100,
      band: { from: 20, to: 80, color: '#74c0fc' },
      levels: [
        { value: 20, lineStyle: 'dashed' },
        { value: 50, lineStyle: 'dotted' },
        { value: 80, lineStyle: 'dashed' },
      ],
    };
  }
  return null;
}

function closes(bars: readonly MarketBar[]): number[] {
  return bars.map((bar) => Number(bar.close));
}

function validatePeriod(period: number): void {
  if (!Number.isInteger(period) || period < 1) throw new Error('Indicator period must be a positive integer');
}

export function simpleMovingAverage(values: readonly number[], period: number): number[] {
  validatePeriod(period);
  if (values.length < period) return [];
  let sum = values.slice(0, period).reduce((total, value) => total + value, 0);
  const result = [sum / period];
  for (let index = period; index < values.length; index += 1) {
    sum += values[index] - values[index - period];
    result.push(sum / period);
  }
  return result;
}

export function exponentialMovingAverage(values: readonly number[], period: number): number[] {
  validatePeriod(period);
  if (values.length < period) return [];
  let average = values.slice(0, period).reduce((total, value) => total + value, 0) / period;
  const multiplier = 2 / (period + 1);
  const result = [average];
  for (const value of values.slice(period)) {
    average = (value - average) * multiplier + average;
    result.push(average);
  }
  return result;
}

export function relativeStrengthIndex(values: readonly number[], period: number): number[] {
  validatePeriod(period);
  if (values.length <= period) return [];
  const gains: number[] = [];
  const losses: number[] = [];
  for (let index = 1; index < values.length; index += 1) {
    const change = values[index] - values[index - 1];
    gains.push(Math.max(change, 0));
    losses.push(Math.max(-change, 0));
  }
  let averageGain = gains.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  let averageLoss = losses.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  const value = () => averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss);
  const result = [value()];
  for (let index = period; index < gains.length; index += 1) {
    averageGain = (averageGain * (period - 1) + gains[index]) / period;
    averageLoss = (averageLoss * (period - 1) + losses[index]) / period;
    result.push(value());
  }
  return result;
}

export function bollingerBands(
  values: readonly number[],
  period: number,
  standardDeviations = 2,
): Array<{ middle: number; upper: number; lower: number }> {
  validatePeriod(period);
  if (!Number.isFinite(standardDeviations) || standardDeviations <= 0) throw new Error('Bollinger deviation must be positive');
  if (values.length < period) return [];
  const result: Array<{ middle: number; upper: number; lower: number }> = [];
  for (let index = period - 1; index < values.length; index += 1) {
    const window = values.slice(index - period + 1, index + 1);
    const middle = window.reduce((sum, value) => sum + value, 0) / period;
    const variance = window.reduce((sum, value) => sum + (value - middle) ** 2, 0) / period;
    const deviation = Math.sqrt(variance) * standardDeviations;
    result.push({ middle, upper: middle + deviation, lower: middle - deviation });
  }
  return result;
}

export function averageTrueRange(
  highs: readonly number[],
  lows: readonly number[],
  closeValues: readonly number[],
  period: number,
): number[] {
  validatePeriod(period);
  if (highs.length !== lows.length || highs.length !== closeValues.length) throw new Error('ATR inputs must have equal length');
  const ranges = highs.map((high, index) => index === 0
    ? high - lows[index]
    : Math.max(high - lows[index], Math.abs(high - closeValues[index - 1]), Math.abs(lows[index] - closeValues[index - 1])));
  if (ranges.length < period) return [];
  let average = ranges.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  const result = [average];
  for (const value of ranges.slice(period)) {
    average = (average * (period - 1) + value) / period;
    result.push(average);
  }
  return result;
}

export function movingAverageConvergenceDivergence(
  values: readonly number[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): Array<{ macd: number; signal: number; histogram: number }> {
  validatePeriod(fastPeriod);
  validatePeriod(slowPeriod);
  validatePeriod(signalPeriod);
  if (fastPeriod >= slowPeriod) throw new Error('MACD fast period must be smaller than slow period');
  const fast = exponentialMovingAverage(values, fastPeriod);
  const slow = exponentialMovingAverage(values, slowPeriod);
  if (!slow.length) return [];
  const macd = values.slice(slowPeriod - 1).map((_, index) => (
    fast[index + slowPeriod - fastPeriod] - slow[index]
  ));
  const signal = exponentialMovingAverage(macd, signalPeriod);
  if (!signal.length) return [];
  return signal.map((signalValue, index) => {
    const macdValue = macd[index + signalPeriod - 1];
    return { macd: macdValue, signal: signalValue, histogram: macdValue - signalValue };
  });
}

export function anchoredVolumeWeightedAveragePrice(
  bars: readonly MarketBar[],
  anchorTime?: string | null,
): number[] {
  const anchorIndex = anchorTime
    ? bars.findIndex((bar) => Date.parse(bar.start_time) >= Date.parse(anchorTime))
    : 0;
  if (anchorIndex < 0) return [];
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;
  const result: number[] = [];
  for (const bar of bars.slice(anchorIndex)) {
    const volume = Number(bar.volume);
    const typical = (Number(bar.high) + Number(bar.low) + Number(bar.close)) / 3;
    cumulativePriceVolume += typical * volume;
    cumulativeVolume += volume;
    result.push(cumulativeVolume === 0 ? typical : cumulativePriceVolume / cumulativeVolume);
  }
  return result;
}

function weightedMovingAverage(values: readonly number[], period: number): number[] {
  validatePeriod(period);
  if (values.length < period) return [];
  const denominator = period * (period + 1) / 2;
  const result: number[] = [];
  for (let index = period - 1; index < values.length; index += 1) {
    let total = 0;
    for (let offset = 0; offset < period; offset += 1) total += values[index - offset] * (period - offset);
    result.push(total / denominator);
  }
  return result;
}

function doubleExponentialMovingAverage(values: readonly number[], period: number): number[] {
  const first = exponentialMovingAverage(values, period);
  const second = exponentialMovingAverage(first, period);
  if (!second.length) return [];
  return second.map((value, index) => 2 * first[index + period - 1] - value);
}

function stochasticOscillator(
  closesValues: readonly number[],
  highs: readonly number[],
  lows: readonly number[],
  period: number,
): number[] {
  validatePeriod(period);
  if (closesValues.length !== highs.length || closesValues.length !== lows.length) throw new Error('Stochastic inputs must have equal length');
  if (closesValues.length < period) return [];
  return closesValues.slice(period - 1).map((close, index) => {
    const end = index + period;
    const high = Math.max(...highs.slice(index, end));
    const low = Math.min(...lows.slice(index, end));
    return high === low ? 50 : (close - low) / (high - low) * 100;
  });
}

function weeklyCloseSeries(bars: readonly MarketBar[]): Array<{ time: string; value: number }> {
  const weeks = new Map<string, { time: string; value: number }>();
  for (const bar of bars) {
    const date = new Date(bar.start_time);
    if (!Number.isFinite(date.getTime())) continue;
    const day = date.getUTCDay();
    date.setUTCDate(date.getUTCDate() - (day === 0 ? 6 : day - 1));
    const key = date.toISOString().slice(0, 10);
    weeks.set(key, { time: bar.start_time, value: Number(bar.close) });
  }
  return [...weeks.values()];
}

function pivotLevels(
  bars: readonly MarketBar[],
  period: number,
): { support: IndicatorPoint[]; resistance: IndicatorPoint[] } {
  validatePeriod(period);
  const support: IndicatorPoint[] = [];
  const resistance: IndicatorPoint[] = [];
  let latestSupport: number | undefined;
  let latestResistance: number | undefined;
  for (let index = 0; index < bars.length; index += 1) {
    const pivotIndex = index - period;
    if (pivotIndex >= period && pivotIndex + period < bars.length) {
      const pivotHigh = Number(bars[pivotIndex].high);
      const pivotLow = Number(bars[pivotIndex].low);
      const highWindow = bars.slice(pivotIndex - period, pivotIndex + period + 1).map((bar) => Number(bar.high));
      const lowWindow = bars.slice(pivotIndex - period, pivotIndex + period + 1).map((bar) => Number(bar.low));
      if (pivotHigh === Math.max(...highWindow)) latestResistance = pivotHigh;
      if (pivotLow === Math.min(...lowWindow)) latestSupport = pivotLow;
    }
    if (latestSupport !== undefined) support.push({ time: bars[index].start_time, value: latestSupport });
    if (latestResistance !== undefined) resistance.push({ time: bars[index].start_time, value: latestResistance });
  }
  return { support, resistance };
}

function fairValueGapPoints(bars: readonly MarketBar[]): { upper: IndicatorPoint[]; lower: IndicatorPoint[] } {
  const upper: IndicatorPoint[] = [];
  const lower: IndicatorPoint[] = [];
  let activeUpper: number | undefined;
  let activeLower: number | undefined;
  for (let index = 2; index < bars.length; index += 1) {
    const highTwoBarsAgo = Number(bars[index - 2].high);
    const lowTwoBarsAgo = Number(bars[index - 2].low);
    const high = Number(bars[index].high);
    const low = Number(bars[index].low);
    const middleClose = Number(bars[index - 1].close);
    if (low > highTwoBarsAgo && middleClose > highTwoBarsAgo) {
      activeUpper = low;
      activeLower = highTwoBarsAgo;
    } else if (high < lowTwoBarsAgo && middleClose < lowTwoBarsAgo) {
      activeUpper = lowTwoBarsAgo;
      activeLower = high;
    }
    if (activeLower !== undefined && ((activeUpper ?? 0) >= activeLower && low <= activeLower && high >= activeLower)) {
      activeUpper = undefined;
      activeLower = undefined;
    }
    if (activeUpper !== undefined && activeLower !== undefined) {
      upper.push({ time: bars[index].start_time, value: activeUpper });
      lower.push({ time: bars[index].start_time, value: activeLower });
    }
  }
  return { upper, lower };
}

export function volumeProfileData(bars: readonly MarketBar[], period: number): VolumeProfileData | null {
  validatePeriod(period);
  const window = bars.slice(Math.max(0, bars.length - period));
  if (!window.length) return null;
  const low = Math.min(...window.map((bar) => Number(bar.low)));
  const high = Math.max(...window.map((bar) => Number(bar.high)));
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
  const binCount = Math.min(32, Math.max(12, Math.round(Math.sqrt(window.length) * 2)));
  const width = high === low ? 1 : (high - low) / binCount;
  const volumes = Array.from({ length: binCount }, () => 0);
  window.forEach((bar) => {
    const typical = (Number(bar.high) + Number(bar.low) + Number(bar.close)) / 3;
    const index = Math.min(binCount - 1, Math.max(0, Math.floor((typical - low) / width)));
    volumes[index] += Math.max(0, Number(bar.volume));
  });
  const pocIndex = volumes.indexOf(Math.max(...volumes));
  const maxVolume = volumes[pocIndex] ?? 0;
  const totalVolume = volumes.reduce((sum, value) => sum + value, 0);
  let included = volumes[pocIndex] ?? 0;
  let left = pocIndex;
  let right = pocIndex;
  while (totalVolume > 0 && included / totalVolume < 0.68 && (left > 0 || right < binCount - 1)) {
    const nextLeft = left > 0 ? volumes[left - 1] : -1;
    const nextRight = right < binCount - 1 ? volumes[right + 1] : -1;
    if (nextRight >= nextLeft && right < binCount - 1) {
      right += 1;
      included += volumes[right];
    } else if (left > 0) {
      left -= 1;
      included += volumes[left];
    } else break;
  }
  return {
    bins: volumes.map((volume, index) => ({
      low: low + index * width,
      high: low + (index + 1) * width,
      volume,
    })),
    poc: low + (pocIndex + 0.5) * width,
    pocIndex,
    valueAreaHigh: low + (right + 1) * width,
    valueAreaLow: low + left * width,
    maxVolume,
  };
}

function points(bars: readonly MarketBar[], startIndex: number, values: readonly number[]): IndicatorPoint[] {
  return values.map((value, index) => ({ time: bars[startIndex + index].start_time, value }));
}

function calculateIndicatorOutputs(
  bars: readonly MarketBar[],
  instance: CoreIndicatorInstance,
): IndicatorOutput[] {
  if (isAutoChartPatternId(instance.id)) {
    return autoChartPatternLines(bars, instance.id, instance.period || 3).map((line) => ({
      key: line.key,
      title: line.title,
      pane: 0,
      kind: 'line',
      points: line.points,
      color: line.color,
      lineStyle: line.lineStyle,
      lineWidth: line.lineWidth,
      labelsOnPriceScale: false,
      valuesInStatusLine: false,
      inputsInStatusLine: false,
    }));
  }
  const values = closes(bars);
  if (instance.id === 'sma' || instance.id === 'ema' || instance.id === 'rsi') {
    const outputs = instance.id === 'sma'
      ? simpleMovingAverage(values, instance.period)
      : instance.id === 'ema'
        ? exponentialMovingAverage(values, instance.period)
        : relativeStrengthIndex(values, instance.period);
    const startIndex = instance.id === 'rsi' ? instance.period : instance.period - 1;
    return [{
      key: `${instance.id}:${instance.period}`,
      title: `${instance.id.toUpperCase()} ${instance.period}`,
      pane: instance.id === 'rsi' ? 1 : 0,
      kind: 'line',
      points: points(bars, startIndex, outputs),
    }];
  }
  if (instance.id === 'bollinger') {
    const deviation = instance.standardDeviations ?? 2;
    const bandValues = bollingerBands(values, instance.period, deviation);
    const start = instance.period - 1;
    return [
      { key: `bollinger:${instance.period}:upper`, title: 'BB Upper', pane: 0, kind: 'line', points: points(bars, start, bandValues.map((item) => item.upper)) },
      { key: `bollinger:${instance.period}:middle`, title: 'BB Middle', pane: 0, kind: 'line', points: points(bars, start, bandValues.map((item) => item.middle)) },
      { key: `bollinger:${instance.period}:lower`, title: 'BB Lower', pane: 0, kind: 'line', points: points(bars, start, bandValues.map((item) => item.lower)) },
    ];
  }
  if (instance.id === 'atr') {
    const atrValues = averageTrueRange(
      bars.map((bar) => Number(bar.high)),
      bars.map((bar) => Number(bar.low)),
      values,
      instance.period,
    );
    return [{ key: `atr:${instance.period}`, title: `ATR ${instance.period}`, pane: 1, kind: 'line', points: points(bars, instance.period - 1, atrValues) }];
  }
  if (instance.id === 'macd') {
    const fast = instance.fastPeriod ?? 12;
    const slow = instance.slowPeriod ?? 26;
    const signal = instance.signalPeriod ?? 9;
    const macdValues = movingAverageConvergenceDivergence(values, fast, slow, signal);
    const start = slow + signal - 2;
    return [
      { key: `macd:${fast}:${slow}:line`, title: 'MACD', pane: 1, kind: 'line', points: points(bars, start, macdValues.map((item) => item.macd)) },
      { key: `macd:${fast}:${slow}:signal`, title: 'Signal', pane: 1, kind: 'line', points: points(bars, start, macdValues.map((item) => item.signal)) },
      { key: `macd:${fast}:${slow}:histogram`, title: 'Histogram', pane: 1, kind: 'histogram', points: points(bars, start, macdValues.map((item) => item.histogram)) },
    ];
  }
  if (instance.id === 'bull-market-band') {
    const weekly = weeklyCloseSeries(bars);
    const weeklyValues = weekly.map((item) => item.value);
    const smaPeriod = instance.fastPeriod ?? 20;
    const emaPeriod = instance.slowPeriod ?? 21;
    const sma = simpleMovingAverage(weeklyValues, smaPeriod);
    const ema = exponentialMovingAverage(weeklyValues, emaPeriod);
    return [
      { key: 'bull-market-band:sma', title: `${smaPeriod}W SMA`, pane: 0, kind: 'line', points: sma.map((value, index) => ({ time: weekly[index + smaPeriod - 1].time, value })) },
      { key: 'bull-market-band:ema', title: `${emaPeriod}W EMA`, pane: 0, kind: 'line', points: ema.map((value, index) => ({ time: weekly[index + emaPeriod - 1].time, value })) },
    ];
  }
  if (instance.id === 'ema-stack') {
    const periods = [9, 21, 50, 200];
    return periods.map((period) => ({
      key: `ema-stack:${period}`,
      title: `EMA ${period}`,
      pane: 0 as const,
      kind: 'line' as const,
      points: points(bars, period - 1, exponentialMovingAverage(values, period)),
    }));
  }
  if (instance.id === 'death-cross' || instance.id === 'golden-cross') {
    const fastPeriod = instance.fastPeriod ?? 50;
    const slowPeriod = instance.slowPeriod ?? 200;
    return [
      { key: `${instance.id}:fast`, title: '50D SMA', pane: 0, kind: 'line', points: points(bars, fastPeriod - 1, simpleMovingAverage(values, fastPeriod)) },
      { key: `${instance.id}:slow`, title: '200D SMA', pane: 0, kind: 'line', points: points(bars, slowPeriod - 1, simpleMovingAverage(values, slowPeriod)) },
    ];
  }
  if (instance.id === 'fair-value-gap') {
    const gap = fairValueGapPoints(bars);
    return [
      { key: 'fair-value-gap:upper', title: 'FVG Upper', pane: 0, kind: 'line', points: gap.upper },
      { key: 'fair-value-gap:lower', title: 'FVG Lower', pane: 0, kind: 'line', points: gap.lower },
    ];
  }
  if (instance.id === 'ideal-bb') {
    const bandValues = bollingerBands(values, 20, 2);
    const nmaPeriod = 120;
    const nmaSignal = 12;
    const first = exponentialMovingAverage(values, nmaPeriod);
    const second = exponentialMovingAverage(first, nmaSignal);
    const lambda = nmaPeriod / nmaSignal;
    const alpha = lambda * (nmaPeriod - 1) / (nmaPeriod - lambda);
    const nma = second.map((value, index) => ({ time: bars[index + nmaPeriod + nmaSignal - 2].start_time, value: (1 + alpha) * first[index + nmaSignal - 1] - alpha * value }));
    const hullSource = values;
    const hullFast = weightedMovingAverage(hullSource, 12);
    const hullSlow = weightedMovingAverage(hullSource, 24);
    const hull = hullSlow.map((value, index) => ({ time: bars[index + 23].start_time, value: 2 * hullFast[index + 12] - value }));
    const vwap = anchoredVolumeWeightedAveragePrice(bars);
    return [
      { key: 'ideal-bb:nma', title: 'NMA', pane: 0, kind: 'line', points: nma },
      { key: 'ideal-bb:vwap', title: 'VWAP Middle', pane: 0, kind: 'line', points: points(bars, 0, vwap) },
      { key: 'ideal-bb:upper', title: 'BB Top', pane: 0, kind: 'line', points: points(bars, 19, bandValues.map((item) => item.upper)) },
      { key: 'ideal-bb:middle', title: 'BB Middle', pane: 0, kind: 'line', points: points(bars, 19, bandValues.map((item) => item.middle)) },
      { key: 'ideal-bb:lower', title: 'BB Bottom', pane: 0, kind: 'line', points: points(bars, 19, bandValues.map((item) => item.lower)) },
      { key: 'ideal-bb:hull', title: 'Hull Trend', pane: 0, kind: 'line', points: hull },
    ];
  }
  if (instance.id === 'log-macd') {
    const fast = 12;
    const slow = 26;
    const signal = 9;
    const fastEma = exponentialMovingAverage(values, fast);
    const slowEma = exponentialMovingAverage(values, slow);
    const logLine = slowEma.map((value, index) => Math.log(Math.max(fastEma[index + slow - fast], Number.EPSILON)) - Math.log(Math.max(value, Number.EPSILON)));
    const signalLine = exponentialMovingAverage(logLine, signal);
    const start = slow + signal - 2;
    return [
      { key: 'log-macd:line', title: 'Log MACD', pane: 1, kind: 'line', points: points(bars, start, logLine.slice(signal - 1)) },
      { key: 'log-macd:signal', title: 'Signal', pane: 1, kind: 'line', points: points(bars, start, signalLine) },
      { key: 'log-macd:histogram', title: 'Histogram', pane: 1, kind: 'histogram', points: points(bars, start, signalLine.map((value, index) => logLine[index + signal - 1] - value)) },
    ];
  }
  if (instance.id === 'macd-dema') {
    const fast = 12;
    const slow = 26;
    const signal = 9;
    const fastDema = doubleExponentialMovingAverage(values, fast);
    const slowDema = doubleExponentialMovingAverage(values, slow);
    const lineStart = 2 * slow - 2;
    const line = values.slice(lineStart).map((_, index) => fastDema[index + lineStart - (2 * fast - 2)] - slowDema[index]);
    const signalLine = exponentialMovingAverage(line, signal);
    const start = lineStart + signal - 1;
    return [
      { key: 'macd-dema:line', title: 'MACD DEMA', pane: 1, kind: 'line', points: points(bars, start, line.slice(signal - 1)) },
      { key: 'macd-dema:signal', title: 'Signal', pane: 1, kind: 'line', points: points(bars, start, signalLine) },
      { key: 'macd-dema:histogram', title: 'Histogram', pane: 1, kind: 'histogram', points: points(bars, start, signalLine.map((value, index) => line[index + signal - 1] - value)) },
    ];
  }
  if (instance.id === 'rsi-divergence') {
    const fast = instance.fastPeriod ?? 5;
    const slow = instance.period || 14;
    const fastRsi = relativeStrengthIndex(values, fast);
    const slowRsi = relativeStrengthIndex(values, slow);
    const divergence = slowRsi.map((value, index) => fastRsi[index + slow - fast] - value);
    return [{ key: 'rsi-divergence:value', title: 'RSI Divergence', pane: 1, kind: 'histogram', points: points(bars, slow, divergence) }];
  }
  if (instance.id === 'stochastic-rsi') {
    const period = instance.period || 14;
    const smoothing = instance.fastPeriod || 3;
    const signal = instance.signalPeriod || 3;
    const rsi = relativeStrengthIndex(values, period);
    const raw = rsi.length < period ? [] : rsi.slice(period - 1).map((value, index) => {
      const window = rsi.slice(index, index + period);
      const minimum = Math.min(...window);
      const maximum = Math.max(...window);
      return maximum === minimum ? 50 : Math.max(0, Math.min(100, (value - minimum) / (maximum - minimum) * 100));
    });
    const k = simpleMovingAverage(raw, smoothing);
    const d = simpleMovingAverage(k, signal);
    const start = period + period - 1 + smoothing - 1 + signal - 1;
    const bounded = (value: number) => Math.max(0, Math.min(100, value));
    return [
      { key: 'stochastic-rsi:k', title: 'Stoch RSI %K', pane: 1, kind: 'line', points: points(bars, start, d.length ? k.slice(signal - 1).map(bounded) : []) },
      { key: 'stochastic-rsi:d', title: 'Stoch RSI %D', pane: 1, kind: 'line', points: points(bars, start, d.map(bounded)) },
    ];
  }
  if (instance.id === 'swing-liquidity') {
    const levels = pivotLevels(bars, instance.period || 5);
    return [
      { key: `${instance.id}:support`, title: 'Support', pane: 0, kind: 'line', points: levels.support },
      { key: `${instance.id}:resistance`, title: 'Resistance', pane: 0, kind: 'line', points: levels.resistance },
    ];
  }
  if (instance.id === 'volume-profile') {
    const profile = volumeProfileData(bars, instance.period || 100);
    if (!profile) return [];
    const times = bars.map((bar) => bar.start_time);
    return [
      { key: 'volume-profile:poc', title: 'POC', pane: 0, kind: 'line', points: times.map((time) => ({ time, value: profile.poc })), volumeProfile: profile },
      { key: 'volume-profile:value-area-high', title: 'VAH', pane: 0, kind: 'line', points: times.map((time) => ({ time, value: profile.valueAreaHigh })), volumeProfile: profile },
      { key: 'volume-profile:value-area-low', title: 'VAL', pane: 0, kind: 'line', points: times.map((time) => ({ time, value: profile.valueAreaLow })), volumeProfile: profile },
    ];
  }
  const anchorIndex = instance.anchorTime
    ? bars.findIndex((bar) => Date.parse(bar.start_time) >= Date.parse(instance.anchorTime as string))
    : 0;
  const vwapValues = anchoredVolumeWeightedAveragePrice(bars, instance.anchorTime);
  return [{ key: `vwap:${instance.anchorTime ?? 'dataset'}`, title: 'Anchored VWAP', pane: 0, kind: 'line', points: points(bars, Math.max(anchorIndex, 0), vwapValues) }];
}

export function indicatorOutputs(
  bars: readonly MarketBar[],
  instance: CoreIndicatorInstance,
): IndicatorOutput[] {
  return calculateIndicatorOutputs(bars, instance)
    .map((output) => ({
      ...output,
      visible: instance.style?.plots?.[output.key] !== false,
      color: instance.style?.colors?.[output.key] ?? output.color,
      lineStyle: instance.style?.lineStyles?.[output.key] ?? output.lineStyle,
      lineWidth: instance.style?.lineWidth ?? output.lineWidth,
      backgroundVisible: instance.style?.backgroundVisible !== false,
      backgroundColor: instance.style?.backgroundColor ?? output.backgroundColor ?? indicatorDefaultBackgroundColor(instance.id),
      precision: instance.style?.precision ?? output.precision,
      labelsOnPriceScale: instance.style?.labelsOnPriceScale ?? output.labelsOnPriceScale ?? false,
      valuesInStatusLine: instance.style?.valuesInStatusLine ?? output.valuesInStatusLine,
      inputsInStatusLine: instance.style?.inputsInStatusLine ?? output.inputsInStatusLine,
    }))
    .filter((output) => output.visible !== false);
}

export function indicatorPlotDefinitions(
  instance: CoreIndicatorInstance,
): Array<Pick<IndicatorOutput, 'key' | 'title'>> {
  const calculated = calculateIndicatorOutputs([], instance);
  if (calculated.length > 0) return calculated.map(({ key, title }) => ({ key, title }));
  if (instance.id === 'volume-profile') {
    return [
      { key: 'volume-profile:poc', title: 'POC' },
      { key: 'volume-profile:value-area-high', title: 'VAH' },
      { key: 'volume-profile:value-area-low', title: 'VAL' },
    ];
  }
  return [];
}

export function indicatorPoints(
  bars: readonly MarketBar[],
  instance: CoreIndicatorInstance,
): IndicatorPoint[] {
  return indicatorOutputs(bars, instance)[0]?.points ?? [];
}
