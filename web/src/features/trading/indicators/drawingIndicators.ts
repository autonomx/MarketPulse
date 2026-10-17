/**
 * Indicators that draw (TVP-6.2), built from public descriptions of the TradingView indicators: they colour bars or
 * the background, mark events, or draw levels and lines found from the chart's own swings, rather than (only) a
 * numeric series. The server registry has the same indicators (`server_indicators/drawing.py`, checked by the shared
 * goldens), so their lines can be alerted on and screened, except Visible Average Price, which depends on the bars in view.
 */
import type { MarketBar } from '../tradingTypes';
import { candlestickPatternOutputs } from './candlestickPatterns';
import { sessionClock, sessionPeriods, type TradingSessionSpec } from './tradingSessions';
import {
  atr, bollinger, ema, finite, full, highest, lowest, nums, startTimes,
  type MaybeNumber, type TradingViewBuiltInOutput,
} from './tradingViewBuiltIns';

type Params = Record<string, number | string>;
type Output = TradingViewBuiltInOutput;
type Point = { time: string; value: number; color?: string; label?: string };

const FIB_COLORS = ['#787b86', '#f23645', '#ff9800', '#4caf50', '#089981', '#00bcd4', '#787b86', '#2962ff', '#9c27b0', '#e91e63', '#673ab7'];

function line(id: string, suffix: string, title: string, values: readonly MaybeNumber[], bars: readonly MarketBar[], color?: string, render?: 'levels' | 'markers', marker?: 'circle'): Output {
  return {
    key: `${id}:${suffix}`,
    title,
    pane: 0,
    kind: 'line',
    points: values.flatMap((value, index) => (finite(value) && bars[index] ? [{ time: bars[index].start_time, value }] : [])),
    color,
    labelsOnPriceScale: false,
    ...(render ? { render } : {}),
    ...(marker ? { marker } : {}),
  };
}

function pointsOutput(id: string, suffix: string, title: string, pane: 0 | 1, kind: Output['kind'], points: Point[], color?: string): Output {
  return { key: `${id}:${suffix}`, title, pane, kind, points, color, labelsOnPriceScale: false };
}

// Swings: a zigzag of alternating highs and lows, each at least `deviation` x ATR(10) from the last.

export type Swing = { index: number; price: number; high: boolean };

/** Alternating swing highs and lows: pivots over `depth` bars each side, a move of at least `deviation` x ATR(10). */
export function zigzag(high: readonly number[], low: readonly number[], close: readonly number[], depth: number, deviation: number): Swing[] {
  const side = Math.max(1, Math.floor(depth / 2));
  const range = atr(high, low, close, 10);
  const swings: Swing[] = [];
  for (let i = side; i < high.length - side; i += 1) {
    let isHigh = true;
    let isLow = true;
    for (let j = i - side; j <= i + side; j += 1) {
      if (j === i) continue;
      if (high[j] > high[i] || (j < i && high[j] === high[i])) isHigh = false;
      if (low[j] < low[i] || (j < i && low[j] === low[i])) isLow = false;
    }
    for (const [found, price, highSwing] of [[isHigh, high[i], true], [isLow, low[i], false]] as const) {
      if (!found) continue;
      const last = swings.at(-1);
      if (last && last.high === highSwing) {
        // The same side again: keep the more extreme one.
        if (highSwing ? price > last.price : price < last.price) swings[swings.length - 1] = { index: i, price, high: highSwing };
        continue;
      }
      const threshold = deviation * (range[i] ?? 0);
      if (last && Math.abs(price - last.price) < threshold) continue;
      swings.push({ index: i, price, high: highSwing });
    }
  }
  return swings;
}

/** A level from bar `from` to the last bar. */
function levelFrom(length: number, from: number, price: number): MaybeNumber[] {
  return Array.from({ length }, (_, index) => (index >= from ? price : null));
}

/** The line through (a, pa) and (b, pb) in bar index, from bar `from` to the last bar. */
function rayFrom(length: number, from: number, a: number, pa: number, b: number, pb: number): MaybeNumber[] {
  const slope = b === a ? 0 : (pb - pa) / (b - a);
  return Array.from({ length }, (_, index) => (index >= from ? pa + slope * (index - a) : null));
}

const RETRACEMENT_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const EXTENSION_LEVELS = [0, 0.382, 0.618, 1, 1.272, 1.618, 2.618];

function autoFib(id: string, bars: readonly MarketBar[], swings: readonly Swing[], extension: boolean): Output[] {
  const needed = extension ? 3 : 2;
  if (swings.length < needed) return [];
  const [a, b, c] = swings.slice(-needed);
  const levels = extension ? EXTENSION_LEVELS : RETRACEMENT_LEVELS;
  return levels.map((level, index) => {
    // Retracement: 0 at the last swing, 1 at the one before. Extension: the A-B move projected from C.
    const price = extension ? c.price + (b.price - a.price) * level : b.price + (a.price - b.price) * level;
    return line(id, `level-${level}`, `${level} (${price.toFixed(2)})`, levelFrom(bars.length, extension ? c.index : a.index, price), bars, FIB_COLORS[index], 'levels');
  });
}

function autoPitchfork(id: string, bars: readonly MarketBar[], swings: readonly Swing[]): Output[] {
  if (swings.length < 3) return [];
  const [a, b, c] = swings.slice(-3);
  // The median from A through the middle of B-C; tines through B and C, parallel to it.
  const middle = { index: (b.index + c.index) / 2, price: (b.price + c.price) / 2 };
  const slope = middle.index === a.index ? 0 : (middle.price - a.price) / (middle.index - a.index);
  const parallel = (anchor: Swing) => rayFrom(bars.length, anchor.index, anchor.index, anchor.price, anchor.index + 1, anchor.price + slope);
  return [
    line(id, 'median', 'Median', rayFrom(bars.length, a.index, a.index, a.price, middle.index, middle.price), bars, '#f23645'),
    line(id, 'upper', b.price >= c.price ? 'Upper' : 'Lower', parallel(b), bars, '#2962ff'),
    line(id, 'lower', b.price >= c.price ? 'Lower' : 'Upper', parallel(c), bars, '#2962ff'),
  ];
}

function autoTrendlines(id: string, bars: readonly MarketBar[], swings: readonly Swing[]): Output[] {
  // Resistance through the last two swing highs, support through the last two swing lows, to the last bar.
  const highs = swings.filter((swing) => swing.high).slice(-2);
  const lows = swings.filter((swing) => !swing.high).slice(-2);
  const outputs: Output[] = [];
  if (highs.length === 2) outputs.push(line(id, 'resistance', 'Resistance', rayFrom(bars.length, highs[0].index, highs[0].index, highs[0].price, highs[1].index, highs[1].price), bars, '#f23645'));
  if (lows.length === 2) outputs.push(line(id, 'support', 'Support', rayFrom(bars.length, lows[0].index, lows[0].index, lows[0].price, lows[1].index, lows[1].price), bars, '#089981'));
  return outputs;
}

/** Key levels: swing prices within the lookback, grouped within half an ATR, the most touched first. */
export function keyLevels(high: readonly number[], low: readonly number[], close: readonly number[], lookback: number, count: number): number[] {
  const from = Math.max(0, high.length - lookback);
  const swings = zigzag(high, low, close, 6, 0).filter((swing) => swing.index >= from);
  const range = atr(high, low, close, 14).at(-1) ?? 0;
  const groups: Array<{ total: number; touches: number }> = [];
  for (const swing of swings) {
    const group = groups.find((item) => Math.abs(item.total / item.touches - swing.price) <= range / 2);
    if (group) {
      group.total += swing.price;
      group.touches += 1;
    } else groups.push({ total: swing.price, touches: 1 });
  }
  return groups.sort((x, y) => y.touches - x.touches).slice(0, count).map((group) => group.total / group.touches);
}

/** VWAP from the bar of the lookback's highest high, lowest low or highest volume. */
export function autoAnchoredVwap(high: readonly number[], low: readonly number[], close: readonly number[], volume: readonly number[], length: number, mode: string): MaybeNumber[] {
  const from = Math.max(0, high.length - length);
  const series = mode === 'lowest-low' ? low : mode === 'highest-volume' ? volume : high;
  let anchor = from;
  for (let i = from; i < series.length; i += 1) {
    if (mode === 'lowest-low' ? series[i] < series[anchor] : series[i] > series[anchor]) anchor = i;
  }
  const values = full(high.length);
  let priceVolume = 0;
  let totalVolume = 0;
  for (let i = anchor; i < high.length; i += 1) {
    const typical = (high[i] + low[i] + close[i]) / 3;
    priceVolume += typical * volume[i];
    totalVolume += volume[i];
    values[i] = totalVolume === 0 ? typical : priceVolume / totalVolume;
  }
  return values;
}

// Chop Zone (TradingView's description): the angle of EMA(34)'s slope, scaled by the lookback's range, in 9 zones.

const CHOP_COLORS: ReadonlyArray<[number, string]> = [[5, '#26c6da'], [3.57, '#43a047'], [2.14, '#a5d6a7'], [0.71, '#009688']];
const CHOP_DOWN_COLORS: ReadonlyArray<[number, string]> = [[-5, '#d50000'], [-3.57, '#e91e63'], [-2.14, '#ff6d00'], [-0.71, '#ffb74d']];

export function chopZoneColors(high: readonly number[], low: readonly number[], close: readonly number[], period: number): Array<string | null> {
  const average = close.map((value, i) => (high[i] + low[i] + value) / 3);
  const top = highest(high as number[], period);
  const bottom = lowest(low as number[], period);
  const ema34 = ema(close as number[], 34);
  return close.map((_, i) => {
    const previous = ema34[i - 1];
    if (!finite(top[i]) || !finite(bottom[i]) || !finite(ema34[i]) || !finite(previous) || top[i] === bottom[i] || average[i] === 0) return null;
    const span = 25 / (top[i]! - bottom[i]!) * bottom[i]!;
    const rise = (previous! - ema34[i]!) / average[i] * span;
    const angle = Math.round(180 * Math.acos(1 / Math.sqrt(1 + rise * rise)) / Math.PI);
    const signed = rise > 0 ? -angle : angle;
    const up = CHOP_COLORS.find(([limit]) => signed >= limit);
    if (up) return up[1];
    const down = CHOP_DOWN_COLORS.find(([limit]) => signed <= limit);
    return down ? down[1] : '#fdd835';
  });
}

// Moon phases: Meeus, Astronomical Algorithms ch. 49 (the main periodic terms; within a few minutes of the true phase).

const SYNODIC_DAYS = 29.530588861;
const RADIANS = Math.PI / 180;
const DAY_MS = 86_400_000;
// [coefficient for the new moon, for the full moon, power of E, then the multipliers of M, M', F and Omega].
const PHASE_TERMS: ReadonlyArray<[number, number, number, number, number, number, number]> = [
  [-0.40720, -0.40614, 0, 0, 1, 0, 0], [0.17241, 0.17302, 1, 1, 0, 0, 0], [0.01608, 0.01614, 0, 0, 2, 0, 0], [0.01039, 0.01043, 0, 0, 0, 2, 0],
  [0.00739, 0.00734, 1, -1, 1, 0, 0], [-0.00514, -0.00515, 1, 1, 1, 0, 0], [0.00208, 0.00209, 2, 2, 0, 0, 0], [-0.00111, -0.00111, 0, 0, 1, -2, 0],
  [-0.00057, -0.00057, 0, 0, 1, 2, 0], [0.00056, 0.00056, 1, 1, 2, 0, 0], [-0.00042, -0.00042, 0, 0, 3, 0, 0], [0.00042, 0.00042, 1, 1, 0, 2, 0],
  [0.00038, 0.00038, 1, 1, 0, -2, 0], [-0.00024, -0.00024, 1, -1, 2, 0, 0], [-0.00017, -0.00017, 0, 0, 0, 0, 1], [-0.00007, -0.00007, 0, 2, 1, 0, 0],
  [0.00004, 0.00004, 0, 0, 2, -2, 0], [0.00004, 0.00004, 0, 3, 0, 0, 0], [0.00003, 0.00003, 0, 1, 1, -2, 0], [0.00003, 0.00003, 0, 0, 2, 2, 0],
  [-0.00003, -0.00003, 0, 1, 1, 2, 0], [0.00003, 0.00003, 0, -1, 1, 2, 0], [-0.00002, -0.00002, 0, -1, 1, -2, 0], [-0.00002, -0.00002, 0, 1, 3, 0, 0],
  [0.00002, 0.00002, 0, 0, 4, 0, 0],
];

/** The instant (ms) of lunation `k`: a whole k is a new moon, k + 0.5 the full moon after it; k = 0 is 2000-01-06. */
export function moonPhaseTime(k: number): number {
  const t = k / 1236.85;
  const jde = 2451550.09766 + SYNODIC_DAYS * k + 0.00015437 * t ** 2 - 0.00000015 * t ** 3 + 0.00000000073 * t ** 4;
  const e = 1 - 0.002516 * t - 0.0000074 * t ** 2;
  const m = (2.5534 + 29.1053567 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3) * RADIANS;
  const mp = (201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4) * RADIANS;
  const f = (160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4) * RADIANS;
  const omega = (124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3) * RADIANS;
  const full = !Number.isInteger(k);
  let correction = 0;
  for (const [newCoefficient, fullCoefficient, power, cm, cmp, cf, co] of PHASE_TERMS) {
    correction += (full ? fullCoefficient : newCoefficient) * e ** power * Math.sin(cm * m + cmp * mp + cf * f + co * omega);
  }
  // Julian ephemeris day to Unix ms (TT - UTC, about a minute, is below this accuracy).
  return (jde + correction - 2440587.5) * 86_400_000;
}

/** The new and full moons between two instants (ms). */
export function moonEvents(from: number, to: number): Array<{ time: number; full: boolean }> {
  const events: Array<{ time: number; full: boolean }> = [];
  const first = Math.floor((from - Date.UTC(2000, 0, 6)) / (SYNODIC_DAYS * 86_400_000)) - 1;
  for (let k = first; ; k += 1) {
    if (moonPhaseTime(k) > to) break;
    for (const [phase, isFull] of [[k, false], [k + 0.5, true]] as const) {
      const time = moonPhaseTime(phase);
      if (time >= from && time < to) events.push({ time, full: isFull });
    }
  }
  return events;
}

// Trading sessions: Tokyo, London and New York in their own time zones.

const SESSIONS: ReadonlyArray<{ name: string; zone: string; open: number; close: number; color: string }> = [
  { name: 'Tokyo', zone: 'Asia/Tokyo', open: 9 * 60, close: 15 * 60, color: 'rgba(41, 98, 255, 0.10)' },
  { name: 'London', zone: 'Europe/London', open: 8 * 60, close: 16 * 60 + 30, color: 'rgba(255, 152, 0, 0.10)' },
  { name: 'New York', zone: 'America/New_York', open: 9 * 60 + 30, close: 16 * 60, color: 'rgba(76, 175, 80, 0.12)' },
];

const formatters = new Map<string, Intl.DateTimeFormat>();
function minuteOfDay(time: number, zone: string): number {
  let format = formatters.get(zone);
  if (!format) {
    format = new Intl.DateTimeFormat('en-GB', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    formatters.set(zone, format);
  }
  const parts = format.formatToParts(new Date(time));
  const hour = Number(parts.find((part) => part.type === 'hour')?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === 'minute')?.value ?? 0);
  return hour * 60 + minute;
}

/** The smallest positive gap between consecutive times, or Infinity. */
function smallestStep(times: readonly number[]): number {
  let step = Number.POSITIVE_INFINITY;
  for (let i = 1; i < times.length; i += 1) if (times[i] > times[i - 1]) step = Math.min(step, times[i] - times[i - 1]);
  return step;
}

/**
 * The session each bar starts in (the last listed wins where they overlap), on intraday bars only. A session is labelled
 * where it starts: a change of session, or the first bar after a gap (the next day's session on regular-hours bars).
 */
export function tradingSessionShading(times: readonly number[]): Array<{ name: string; color: string; first: boolean } | null> {
  const step = smallestStep(times);
  if (!(step < 86_400_000)) return times.map(() => null);
  let previous: string | null = null;
  return times.map((time, index) => {
    let session: (typeof SESSIONS)[number] | null = null;
    for (const candidate of SESSIONS) {
      const minute = minuteOfDay(time, candidate.zone);
      if (minute >= candidate.open && minute < candidate.close) session = candidate;
    }
    const first = session !== null && (session.name !== previous || time - times[index - 1] > step);
    previous = session?.name ?? null;
    return session ? { name: session.name, color: session.color, first } : null;
  });
}

/**
 * Seasonality: each year's change since its first bar, at the current year's dates (daily bars only). Dates align by
 * month and day, so 1 March is 1 March in a leap year too; a year whose first bar is after 15 January (history starting
 * mid-year) is left out rather than measured from that bar. Earlier years go on past the last bar (`ahead`) to the end
 * of the year, one point a trading day (weekdays, or every day when the bars trade at weekends).
 */
export function seasonality(bars: readonly MarketBar[], years: number): Array<{ year: number; values: MaybeNumber[]; ahead: Point[] }> {
  const times = startTimes(bars);
  if (bars.length < 2) return [];
  const gaps = times.slice(1).map((time, i) => time - times[i]).filter((gap) => gap > 0).sort((x, y) => x - y);
  if (gaps[Math.floor(gaps.length / 2)] < 20 * 3_600_000) return [];
  const close = nums(bars, 'close');
  const yearOf = (time: number) => new Date(time).getUTCFullYear();
  // Month and day as MMDD.
  const dayOf = (time: number) => {
    const date = new Date(time);
    return (date.getUTCMonth() + 1) * 100 + date.getUTCDate();
  };
  const lastTime = times[times.length - 1];
  const current = yearOf(lastTime);
  const byYear = new Map<number, Array<{ day: number; close: number }>>();
  times.forEach((time, i) => {
    const year = yearOf(time);
    if (current - year > years) return;
    const list = byYear.get(year) ?? [];
    list.push({ day: dayOf(time), close: close[i] });
    byYear.set(year, list);
  });
  const weekends = times.slice(-30).some((time) => [0, 6].includes(new Date(time).getUTCDay()));
  const aheadTimes: number[] = [];
  for (let time = lastTime + DAY_MS; yearOf(time) === current; time += DAY_MS) {
    if (weekends || ![0, 6].includes(new Date(time).getUTCDay())) aheadTimes.push(time);
  }
  const result: Array<{ year: number; values: MaybeNumber[]; ahead: Point[] }> = [];
  for (let year = current; year >= current - years; year -= 1) {
    const list = byYear.get(year);
    if (!list || list.length === 0 || list[0].day > 115) continue;
    const base = list[0].close;
    // The year's change at its last bar on or before this day of the year.
    const changeAt = (time: number): number | null => {
      const day = dayOf(time);
      let found: number | null = null;
      for (const item of list) {
        if (item.day > day) break;
        found = item.close;
      }
      return found === null || base === 0 ? null : (found / base - 1) * 100;
    };
    const values = times.map((time) => (yearOf(time) === current ? changeAt(time) : null));
    const ahead = year === current ? [] : aheadTimes.flatMap((time) => {
      const value = changeAt(time);
      return value === null ? [] : [{ time: new Date(time).toISOString(), value }];
    });
    result.push({ year, values, ahead });
  }
  return result;
}

/** Rising bars (close above open) over falling bars in each window of `period` bars; the rising count when none fell. */
export function advanceDeclineBarsRatio(bars: readonly MarketBar[], period: number): MaybeNumber[] {
  const length = Math.max(1, Math.round(period));
  let up = 0;
  let down = 0;
  return bars.map((bar, index) => {
    const direction = Math.sign(Number(bar.close) - Number(bar.open));
    if (direction > 0) up += 1;
    if (direction < 0) down += 1;
    if (index >= length) {
      const old = Math.sign(Number(bars[index - length].close) - Number(bars[index - length].open));
      if (old > 0) up -= 1;
      if (old < 0) down -= 1;
    }
    if (index < length - 1) return null;
    return down === 0 ? up : up / down;
  });
}

const DRAWING_NAMES = new Set([
  'Auto Fib Retracement', 'Auto Fib Extension', 'Auto Pitchfork', 'Auto Trendlines', 'Auto key levels', 'VWAP Auto Anchored',
  'Visible Average Price', 'Bollinger Bars', 'Chop Zone', 'Moon Phases', 'Trading Sessions', 'Multi-Time Period Charts indicator', 'Seasonality',
  'All Candlestick Patterns', 'Advance/Decline Ratio (Bars)',
]);
const SEASON_COLORS = ['#2962ff', '#f23645', '#ff9800', '#4caf50', '#9c27b0', '#00bcd4'];

/** The outputs of a TVP-6.2 indicator, or null when `name` isn't one. */
export function drawingIndicatorOutputs(
  name: string,
  id: string,
  period: number,
  params: Params,
  bars: readonly MarketBar[],
  session: TradingSessionSpec,
): Output[] | null {
  if (bars.length === 0) return DRAWING_NAMES.has(name) ? [] : null;
  const high = nums(bars, 'high'); const low = nums(bars, 'low'); const close = nums(bars, 'close'); const volume = nums(bars, 'volume');
  const at = (index: number) => bars[index].start_time;
  switch (name) {
    case 'Auto Fib Retracement':
    case 'Auto Fib Extension':
      return autoFib(id, bars, zigzag(high, low, close, period, Number(params.deviation)), name === 'Auto Fib Extension');
    case 'Auto Pitchfork':
      return autoPitchfork(id, bars, zigzag(high, low, close, period, Number(params.deviation)));
    case 'Auto Trendlines':
      return autoTrendlines(id, bars, zigzag(high, low, close, period * 2, 0));
    case 'Auto key levels':
      return keyLevels(high, low, close, period, Number(params.count)).map((price, index) => line(id, `level-${index + 1}`, `Key level ${index + 1}`, levelFrom(bars.length, Math.max(0, bars.length - period), price), bars, '#9c27b0', 'levels'));
    case 'VWAP Auto Anchored':
      return [line(id, 'vwap', 'Auto Anchored VWAP', autoAnchoredVwap(high, low, close, volume, period, String(params.anchor)), bars, '#2962ff')];
    case 'Visible Average Price':
      return [pointsOutput(id, 'average', 'Visible Average Price', 0, 'viewport-average', [], '#ff9800')];
    case 'Bollinger Bars': {
      const bands = bollinger(close, period, Number(params.deviations));
      const colors: Point[] = close.flatMap((value, i) => (finite(bands.upper[i]) && value > bands.upper[i]! ? [{ time: at(i), value: 1, color: '#26a69a' }]
        : finite(bands.lower[i]) && value < bands.lower[i]! ? [{ time: at(i), value: 1, color: '#ef5350' }] : []));
      return [
        pointsOutput(id, 'bars', 'Bollinger Bars', 0, 'bar-colors', colors),
        { ...line(id, 'upper', 'Upper', bands.upper, bars, '#787b86'), lineStyle: 'dashed' },
        { ...line(id, 'lower', 'Lower', bands.lower, bars, '#787b86'), lineStyle: 'dashed' },
      ];
    }
    case 'Advance/Decline Ratio (Bars)':
      // TVP-6.6: the chart's rising bars over its falling bars in the last `period` bars (TradingView's definition).
      return [line(id, 'ratio', 'A/D Ratio (Bars)', advanceDeclineBarsRatio(bars, period), bars, '#2962ff')];
    case 'Chop Zone': {
      const colors = chopZoneColors(high, low, close, period);
      return [pointsOutput(id, 'zone', 'Chop Zone', 1, 'histogram', colors.flatMap((color, i) => (color ? [{ time: at(i), value: 1, color }] : [])))];
    }
    case 'Moon Phases': {
      const times = startTimes(bars);
      const step = times.length > 1 ? times[times.length - 1] - times[times.length - 2] : 86_400_000;
      const events = moonEvents(times[0], times[times.length - 1] + step);
      const full = new Array<MaybeNumber>(bars.length).fill(null);
      const fresh = new Array<MaybeNumber>(bars.length).fill(null);
      let i = 0;
      for (const event of events) {
        while (i + 1 < times.length && times[i + 1] <= event.time) i += 1;
        if (event.full) full[i] = high[i]; else fresh[i] = low[i];
      }
      return [line(id, 'full', 'Full moon', full, bars, '#fdd835', 'markers', 'circle'), line(id, 'new', 'New moon', fresh, bars, '#787b86', 'markers', 'circle')];
    }
    case 'Trading Sessions': {
      const shading = tradingSessionShading(startTimes(bars));
      return [pointsOutput(id, 'sessions', 'Trading Sessions', 0, 'background', shading.flatMap((item, i) => (item ? [{ time: at(i), value: 1, color: item.color, ...(item.first ? { label: item.name } : {}) }] : [])))];
    }
    case 'Multi-Time Period Charts indicator': {
      const clock = sessionClock(bars, startTimes(bars), session);
      const { key } = sessionPeriods(clock, String(params.period) as 'D' | 'W' | 'M', session);
      const periodHigh = full(bars.length); const periodLow = full(bars.length); const periodOpen = full(bars.length);
      const shading: Point[] = [];
      let start = 0;
      for (let i = 0; i <= bars.length; i += 1) {
        if (i < bars.length && key[i] === key[start]) continue;
        // One higher-timeframe candle: its range as levels, its direction as a tint behind it.
        const top = Math.max(...high.slice(start, i)); const bottom = Math.min(...low.slice(start, i));
        const tint = close[i - 1] >= Number(bars[start].open) ? 'rgba(38, 166, 154, 0.08)' : 'rgba(239, 83, 80, 0.08)';
        for (let j = start; j < i; j += 1) {
          periodHigh[j] = top; periodLow[j] = bottom; periodOpen[j] = Number(bars[start].open);
          shading.push({ time: at(j), value: 1, color: tint });
        }
        start = i;
      }
      return [
        pointsOutput(id, 'candles', 'Period candles', 0, 'background', shading),
        line(id, 'high', 'Period high', periodHigh, bars, '#26a69a', 'levels'),
        line(id, 'low', 'Period low', periodLow, bars, '#ef5350', 'levels'),
        { ...line(id, 'open', 'Period open', periodOpen, bars, '#787b86', 'levels'), lineStyle: 'dotted' },
      ];
    }
    case 'All Candlestick Patterns':
      return candlestickPatternOutputs(id, bars, String(params.patterns), String(params.trend));
    case 'Seasonality':
      return seasonality(bars, period).map(({ year, values, ahead }, index) => {
        const output = line(id, String(year), String(year), values, bars, SEASON_COLORS[index % SEASON_COLORS.length]);
        return { ...output, points: [...output.points, ...ahead], pane: 1 as const };
      });
    default:
      return null;
  }
}
