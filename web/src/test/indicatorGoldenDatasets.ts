import type { MarketBar } from '../features/trading/tradingTypes';
import { fixture } from './fixture';
import { wallClockMs, type TradingSessionSpec } from '../features/trading/indicators/tradingSessions';

export type GoldenBar = {
  start_time: string;
  end_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  /** Session label, as the server sets it on equity bars; absent elsewhere. */
  session?: string;
};

export type GoldenDataset = { name: string; bars: GoldenBar[] };

const HOUR_MS = 3_600_000;
const FIRST_BAR_MS = Date.parse('2026-01-05T14:00:00Z');

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(random: () => number): number {
  const u = Math.max(random(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * random());
}

function bar(index: number, open: number, high: number, low: number, close: number, volume: number): GoldenBar {
  const start = FIRST_BAR_MS + index * HOUR_MS;
  return {
    start_time: new Date(start).toISOString(),
    end_time: new Date(start + HOUR_MS).toISOString(),
    open: open.toFixed(2),
    high: high.toFixed(2),
    low: low.toFixed(2),
    close: close.toFixed(2),
    volume: Math.round(volume).toString(),
  };
}

function randomWalk(seed: number, length: number, gapEvery = 0): GoldenBar[] {
  const random = mulberry32(seed);
  const bars: GoldenBar[] = [];
  let previousClose = 100;
  for (let index = 0; index < length; index += 1) {
    const gap = gapEvery > 0 && index > 0 && index % gapEvery === 0 ? (random() < 0.5 ? -1 : 1) * (3 + random() * 3) : 0;
    const open = Math.max(5, previousClose + gap + gaussian(random) * 0.2);
    const close = Math.max(5, open + gaussian(random) * 0.8);
    const high = Math.max(open, close) + random() * 0.6;
    const low = Math.max(1, Math.min(open, close) - random() * 0.6);
    bars.push(bar(index, open, high, low, close, 1_000 + random() * 5_000));
    previousClose = close;
  }
  return bars;
}

function gapsFlatsAndZeroVolume(): GoldenBar[] {
  const bars = randomWalk(0x5eed2, 160, 25);
  for (let index = 40; index < 50; index += 1) {
    const price = bars[39].close;
    bars[index] = { ...bars[index], open: price, high: price, low: price, close: price, volume: '0' };
  }
  for (let index = 90; index < 100; index += 1) bars[index] = { ...bars[index], volume: '0' };
  bars[120] = { ...bars[120], high: (Number(bars[120].high) + 15).toFixed(2) };
  return bars;
}

function negativePrices(length: number): GoldenBar[] {
  const random = mulberry32(0x5eed5);
  let previousClose = 2;
  return Array.from({ length }, (_, index) => {
    const open = previousClose + gaussian(random) * 0.3;
    const close = index % 9 === 4 ? open : open + gaussian(random) * 1.5;
    const high = Math.max(open, close) + (index % 7 === 3 ? 0 : random() * 0.8);
    const low = Math.min(open, close) - (index % 7 === 3 ? 0 : random() * 0.8);
    const volume = index % 11 === 5 ? -500 : index % 13 === 6 ? 0 : 1_000 + random() * 3_000;
    previousClose = close;
    return bar(index, open, high, low, close, volume);
  });
}

function trendUp(length: number): GoldenBar[] {
  return Array.from({ length }, (_, index) => {
    const close = 50 + index * 0.5;
    const open = close - 0.3;
    return bar(index, open, close + 0.2, open - 0.2, close, 1_000 + index);
  });
}

function constant(length: number): GoldenBar[] {
  return Array.from({ length }, (_, index) => bar(index, 100, 100, 100, 100, 500));
}

/** Generates the datasets. Only the update run uses this; checks read the committed files, so a Node maths change can't move them. */
export function generateGoldenDatasets(): GoldenDataset[] {
  return [
    { name: 'random-walk-300', bars: randomWalk(0x5eed1, 300) },
    { name: 'gaps-flats-zero-volume-160', bars: gapsFlatsAndZeroVolume() },
    { name: 'short-12', bars: randomWalk(0x5eed3, 12) },
    { name: 'trend-up-80', bars: trendUp(80) },
    { name: 'constant-40', bars: constant(40) },
    { name: 'empty-0', bars: [] },
    { name: 'mixed-90', bars: randomWalk(0x5eed4, 90, 20) },
    { name: 'negative-prices-60', bars: negativePrices(60) },
  ];
}

/**
 * Second series for indicators that read a compare symbol (Correlation Coefficient). It starts 5 hours after the other
 * datasets and misses every 23rd hour, so alignment by start time and carrying the last close forward are both exercised.
 */
export function generateCompareDatasets(): GoldenDataset[] {
  return [{ name: 'compare-walk-291', bars: randomWalk(0x5eed6, 310).filter((_, index) => index >= 5 && index % 23 !== 7) }];
}

export const COMPARE_DATASET = 'compare-walk-291';

const HALF_HOUR_MS = 1_800_000;

/**
 * A US equity on 30-minute bars from 04:00 to 20:00 New York time, Monday 2026-02-16 to Friday 2026-03-20: five weeks across the
 * start of daylight saving time (2026-03-08) and a month end, with the server's pre-market, regular and after-hours labels.
 */
function equitySession(): GoldenBar[] {
  const random = mulberry32(0x5eed7);
  const bars: GoldenBar[] = [];
  let previousClose = 50;
  for (let start = Date.parse('2026-02-16T00:00:00Z'); start < Date.parse('2026-03-21T00:00:00Z'); start += HALF_HOUR_MS) {
    const local = new Date(wallClockMs(start, 'America/New_York'));
    const minute = local.getUTCHours() * 60 + local.getUTCMinutes();
    if (local.getUTCDay() === 0 || local.getUTCDay() === 6 || minute < 240 || minute >= 1200) continue;
    const open = Math.max(5, previousClose + gaussian(random) * 0.1);
    const close = Math.max(5, open + gaussian(random) * 0.4);
    const high = Math.max(open, close) + random() * 0.3;
    const low = Math.max(1, Math.min(open, close) - random() * 0.3);
    const regular = minute >= 570 && minute < 960;
    bars.push({
      start_time: new Date(start).toISOString(),
      end_time: new Date(start + HALF_HOUR_MS).toISOString(),
      open: open.toFixed(2), high: high.toFixed(2), low: low.toFixed(2), close: close.toFixed(2),
      volume: Math.round((regular ? 5_000 : 400) + random() * 2_000).toString(),
      session: minute < 570 ? 'extended_pre' : regular ? 'regular' : 'extended_post',
    });
    previousClose = close;
  }
  return bars;
}

/** Datasets for session-aware indicators only (TVP-6.1), so other golden files keep their bytes. */
export function generateSessionDatasets(): GoldenDataset[] {
  return [{ name: 'equity-dst-30m', bars: equitySession() }];
}

export const SESSION_DATASET = 'equity-dst-30m';

// Candles (open, high, low, close) that complete each candlestick pattern the generated walks rarely produce (TVP-6.3),
// each after spacer bars of body 1; read with trend detection off, so the shapes alone decide.
const CANDLE_SNIPPETS: ReadonlyArray<ReadonlyArray<[number, number, number, number]>> = [
  [[100, 103.1, 99.9, 103], [104.5, 105, 104, 104.5], [103.5, 103.6, 101.9, 102]], // abandoned baby, bearish
  [[103, 103.1, 99.9, 100], [98.5, 99, 98, 98.5], [99.5, 101.1, 99.4, 101]], // abandoned baby, bullish
  [[103, 103.1, 99.9, 100], [99, 99.1, 98.6, 98.7], [98.8, 99.6, 98.7, 99.5]], // downside tasuki gap
  [[100, 100, 98, 100]], // dragonfly doji
  [[100, 102, 100, 100]], // gravestone doji
  [[100, 103.1, 99.9, 103], [101.5, 101.8, 101.2, 101.5]], // harami cross, bearish
  [[103, 103.1, 99.9, 100], [101.5, 101.8, 101.2, 101.5]], // harami cross, bullish
  [[100, 101.5, 100, 100.3]], // inverted hammer and shooting star
  [[100, 103, 100, 103], [99, 99, 96, 96]], // kicking, bearish
  [[103, 103, 100, 100], [104, 107, 104, 107]], // kicking, bullish
  [[103, 103.1, 99.9, 100], [99, 99.3, 98.9, 99.2], [99.5, 102.6, 99.4, 102.5]], // morning star
  [[103, 103.1, 99.9, 100], [99, 99.2, 98.8, 99], [99.5, 102.6, 99.4, 102.5]], // morning doji star
  [[103, 103.1, 99.9, 100], [99.6, 100, 99.5, 99.9]], // on neck
  [[100, 103.1, 99.9, 103], [102.8, 102.85, 102.45, 102.5], [102.5, 102.55, 102.15, 102.2], [102.2, 102.25, 101.85, 101.9], [102, 105.1, 101.9, 105]], // rising three methods
  [[110, 110.1, 107, 107], [108, 108.1, 105, 105], [106, 106.1, 103, 103]], // three black crows
  [[100, 103, 99.9, 103], [102, 105, 101.9, 105], [104, 107, 103.9, 107]], // three white soldiers
  [[100, 100.5, 99.5, 100], [101.5, 102, 101, 101.5], [100.5, 101, 100, 100.5]], // tri-star, bearish
  [[100, 100.5, 99.5, 100], [98.5, 99, 98, 98.5], [99.5, 100, 99, 99.5]], // tri-star, bullish
];

function candlestickPatterns(): GoldenBar[] {
  const candles: Array<[number, number, number, number]> = [];
  const spacers = (count: number) => {
    for (let i = 0; i < count; i += 1) candles.push(i % 2 === 0 ? [100, 101.5, 99.5, 101] : [101, 101.5, 99.5, 100]);
  };
  spacers(20);
  for (const snippet of CANDLE_SNIPPETS) {
    candles.push(...snippet);
    spacers(3);
  }
  return candles.map(([open, high, low, close], index) => bar(index, open, high, low, close, 1_000 + index));
}

export function generateCandlestickDatasets(): GoldenDataset[] {
  return [{ name: 'candlestick-patterns', bars: candlestickPatterns() }];
}

export const CANDLESTICK_DATASET = 'candlestick-patterns';
/** The session calendar the server derives for a US equity, and a futures-style 18:00 ET roll. */
export const EQUITY_SESSION: TradingSessionSpec = { timezone: 'America/New_York', startMinute: 0, regularStartMinute: 570, regularOnly: true };
export const FUTURES_SESSION: TradingSessionSpec = { timezone: 'America/New_York', startMinute: 1080 };
export const COMPARE_SYMBOL = 'golden:compare';

export const ALTERNATIVE_PERIOD_DATASET = 'random-walk-300';
export const INPUT_VARIANT_DATASET = 'mixed-90';

export function asMarketBars(dataset: GoldenDataset): MarketBar[] {
  return fixture<MarketBar[]>(dataset.bars.map((item) => ({
    ...item,
    instrument_id: 'golden:test',
    interval: '1h',
    is_final: true,
  })));
}
