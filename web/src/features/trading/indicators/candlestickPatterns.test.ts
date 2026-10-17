import { describe, expect, it } from 'vitest';
import { asMarketBars, generateCandlestickDatasets } from '../../../test/indicatorGoldenDatasets';
import { CANDLESTICK_PATTERNS, candlestickPatternOutputs, detectCandlestickPatterns, selectedCandlestickPatterns } from './candlestickPatterns';
import { calculateTradingViewBuiltInOutputs, tradingViewBuiltInDefinition, tradingViewBuiltInInputs, tradingViewBuiltInPlotDefinitions } from './tradingViewBuiltIns';

const bars = asMarketBars(generateCandlestickDatasets()[0]);
const series = (key: 'open' | 'high' | 'low' | 'close') => bars.map((bar) => Number(bar[key]));
const [open, high, low, close] = (['open', 'high', 'low', 'close'] as const).map(series);

// The golden dataset's snippets, in order: their length and the patterns their last candle completes.
const SNIPPETS: Array<[number, string[]]> = [
  [3, ['abandoned-baby-bearish']], [3, ['abandoned-baby-bullish']], [3, ['downside-tasuki-gap']], [1, ['dragonfly-doji']], [1, ['gravestone-doji']],
  [2, ['harami-cross-bearish']], [2, ['harami-cross-bullish']], [1, ['inverted-hammer', 'shooting-star']], [2, ['kicking-bearish']], [2, ['kicking-bullish']],
  [3, ['morning-star']], [3, ['morning-doji-star']], [2, ['on-neck']], [5, ['rising-three-methods']], [3, ['three-black-crows']],
  [3, ['three-white-soldiers']], [3, ['tri-star-bearish']], [3, ['tri-star-bullish']],
];

function candles(rows: Array<[number, number, number, number]>) {
  return { open: rows.map((row) => row[0]), high: rows.map((row) => row[1]), low: rows.map((row) => row[2]), close: rows.map((row) => row[3]) };
}

/** Bars of body 1 for the body average, then `rows`. */
function afterWarmup(rows: Array<[number, number, number, number]>) {
  const warmup = Array.from({ length: 20 }, (_, i): [number, number, number, number] => (i % 2 === 0 ? [100, 101.5, 99.5, 101] : [101, 101.5, 99.5, 100]));
  return candles([...warmup, ...rows]);
}

describe('candlestick patterns (TVP-6.3)', () => {
  it('lists 44 patterns with unique keys, in the order the server uses', () => {
    expect(CANDLESTICK_PATTERNS).toHaveLength(44);
    expect(new Set(CANDLESTICK_PATTERNS.map((pattern) => pattern.key)).size).toBe(44);
    expect(CANDLESTICK_PATTERNS.map((pattern) => pattern.key)).toEqual([...CANDLESTICK_PATTERNS.map((pattern) => pattern.key)].sort());
  });

  it('finds each rarer pattern on the last candle of its snippet, with trend detection off', () => {
    const hits = detectCandlestickPatterns(open, high, low, close, 'none', CANDLESTICK_PATTERNS);
    let index = 20;
    for (const [length, keys] of SNIPPETS) {
      const last = index + length - 1;
      for (const key of keys) expect(hits.get(key)![last], `${key} at bar ${last}`).toBe(true);
      index += length + 3;
    }
  });

  it('finds hammers and engulfings only in their trend', () => {
    const hammer: [number, number, number, number] = [100.7, 101, 98.5, 101];
    const down = afterWarmup([...Array.from({ length: 40 }, (_, i): [number, number, number, number] => [150 - i, 150.5 - i, 148.5 - i, 149 - i]), hammer]);
    const found = (rows: ReturnType<typeof afterWarmup>, trend: 'sma50' | 'none', key: string) => detectCandlestickPatterns(rows.open, rows.high, rows.low, rows.close, trend, selectedCandlestickPatterns(key)).get(key)!.at(-1);
    expect(found(down, 'none', 'hammer')).toBe(true);
    expect(found(down, 'sma50', 'hammer')).toBe(true);
    expect(found(down, 'sma50', 'hanging-man')).toBe(false);
    expect(found(down, 'none', 'hanging-man')).toBe(true);
    const engulfing = afterWarmup([[101, 101.1, 100.6, 100.7], [100.5, 103.6, 100.4, 103.5]]);
    expect(found(engulfing, 'none', 'engulfing-bullish')).toBe(true);
    // Without 50 bars there is no SMA(50), so no trend: trend-bound patterns don't fire.
    expect(found(engulfing, 'sma50', 'engulfing-bullish')).toBe(false);
    expect(found(candles([[100, 100.5, 99.5, 100], [100, 100.5, 99.5, 100]]), 'none', 'doji')).toBe(true);
  });

  it('selects all, one direction, or one pattern', () => {
    expect(selectedCandlestickPatterns('all')).toHaveLength(44);
    expect(selectedCandlestickPatterns('bogus')).toHaveLength(44);
    expect(selectedCandlestickPatterns('neutral').map((pattern) => pattern.key)).toEqual(['doji', 'spinning-top-black', 'spinning-top-white']);
    expect(selectedCandlestickPatterns('bullish').every((pattern) => pattern.direction === 'bullish')).toBe(true);
    expect(selectedCandlestickPatterns('hammer').map((pattern) => pattern.name)).toEqual(['Hammer - Bullish']);
  });

  it('draws each pattern as labelled markers below (bullish) or above (bearish) the bar, valued at its low or high', () => {
    const outputs = candlestickPatternOutputs('tv-all-candlestick-patterns', bars, 'all', 'none');
    expect(outputs).toHaveLength(44);
    const kicking = outputs.find((output) => output.key === 'tv-all-candlestick-patterns:kicking-bullish')!;
    expect(kicking).toMatchObject({ title: 'Kicking - Bullish', render: 'markers', marker: 'arrowUp', markerPosition: 'belowBar', markerText: 'K', color: '#089981' });
    const at = bars.findIndex((bar) => bar.start_time === kicking.points[0].time);
    expect(kicking.points[0].value).toBe(Number(bars[at].low));
    const crows = outputs.find((output) => output.key === 'tv-all-candlestick-patterns:three-black-crows')!;
    expect(crows).toMatchObject({ marker: 'arrowDown', markerPosition: 'aboveBar', markerText: '3BC' });
    expect(crows.points[0].value).toBe(Number(bars.find((bar) => bar.start_time === crows.points[0].time)!.high));
    expect(candlestickPatternOutputs('tv-all-candlestick-patterns', bars, 'bearish', 'none').every((output) => output.marker === 'arrowDown')).toBe(true);
  });

  it('is the "All Candlestick Patterns" built-in, with pattern and trend inputs', () => {
    expect(tradingViewBuiltInDefinition('tv-all-candlestick-patterns')).toMatchObject({ name: 'All Candlestick Patterns', available: true, pane: 0 });
    expect(tradingViewBuiltInInputs('tv-all-candlestick-patterns')?.params.map((param) => param.key)).toEqual(['patterns', 'trend']);
    const one = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-all-candlestick-patterns', period: 1, params: { patterns: 'tri-star-bullish', trend: 'none' } });
    expect(one.map((output) => output.key)).toEqual(['tv-all-candlestick-patterns:tri-star-bullish']);
    expect(one[0].points).toHaveLength(1);
    expect(calculateTradingViewBuiltInOutputs([], { id: 'tv-all-candlestick-patterns', period: 1 })).toEqual([]);
    // The Style tab lists the selected patterns' plots.
    expect(tradingViewBuiltInPlotDefinitions({ id: 'tv-all-candlestick-patterns', period: 1 })).toHaveLength(44);
    expect(tradingViewBuiltInPlotDefinitions({ id: 'tv-all-candlestick-patterns', period: 1, params: { patterns: 'neutral' } }).map((plot) => plot.title))
      .toEqual(['Doji', 'Spinning Top Black', 'Spinning Top White']);
  });
});
