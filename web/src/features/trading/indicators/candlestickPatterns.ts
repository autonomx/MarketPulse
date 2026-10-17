/**
 * Candlestick pattern recognition (TVP-6.3), following the public definitions of TradingView's built-in candlestick
 * pattern indicators: a candle's body is small or long against EMA(14) of the bodies, shadows count above 5% of the
 * body, a doji body is at most 5% of the range, and the trend is the close against SMA(50) (optionally SMA(50) against
 * SMA(200)). A value missing for lack of history makes the pattern false, like Pine's `na`.
 *
 * Mirrored on the server by `server_indicators/candlestick_patterns.py`; the shared goldens prove they agree.
 */
import type { MarketBar } from '../tradingTypes';
import type { TradingViewBuiltInOutput } from './tradingViewBuiltIns';

type MaybeNumber = number | null;

// The built-ins' SMA and EMA arithmetic (tradingViewBuiltIns.ts), copied so this module doesn't import it back.
const finite = (value: MaybeNumber): value is number => value !== null && Number.isFinite(value);
const total = (values: readonly number[]) => values.reduce((sum, value) => sum + value, 0);

function sma(values: readonly number[], p: number): MaybeNumber[] {
  const result: MaybeNumber[] = values.map(() => null);
  if (values.length < p) return result;
  let running = total(values.slice(0, p));
  result[p - 1] = running / p;
  for (let i = p; i < values.length; i += 1) {
    running += values[i] - values[i - p];
    result[i] = running / p;
  }
  return result;
}

function ema(values: readonly number[], p: number): MaybeNumber[] {
  const result: MaybeNumber[] = values.map(() => null);
  if (values.length < p) return result;
  let current = total(values.slice(0, p)) / p;
  result[p - 1] = current;
  const alpha = 2 / (p + 1);
  for (let i = p; i < values.length; i += 1) {
    current += alpha * (values[i] - current);
    result[i] = current;
  }
  return result;
}

export type CandlestickDirection = 'bullish' | 'bearish' | 'neutral';
export type CandlestickTrend = 'sma50' | 'sma50-sma200' | 'none';
export type CandlestickPattern = { key: string; name: string; label: string; direction: CandlestickDirection };

type Candles = {
  open: readonly number[]; high: readonly number[]; low: readonly number[]; close: readonly number[];
  bodyHi: number[]; bodyLo: number[]; body: number[]; bodyAvg: MaybeNumber[]; bodyMiddle: number[];
  small: boolean[]; long: boolean[]; upShadow: number[]; dnShadow: number[]; hasUpShadow: boolean[]; hasDnShadow: boolean[];
  white: boolean[]; black: boolean[]; range: number[]; dojiBody: boolean[]; doji: boolean[]; marubozu: boolean[];
  up: boolean[]; down: boolean[];
};
/** A pattern at bar `i`, which has at least `lookback` bars before it. */
type Detector = (c: Candles, i: number) => boolean;

const SHADOW_PERCENT = 5;
const DOJI_BODY_PERCENT = 5;
const SHADOW_EQUALS_PERCENT = 100;
const FACTOR = 2;
const LONG_SHADOW_PERCENT = 75;
const SPINNING_TOP_PERCENT = 34;

function candles(open: readonly number[], high: readonly number[], low: readonly number[], close: readonly number[], trend: CandlestickTrend): Candles {
  const bodyHi = close.map((value, i) => Math.max(value, open[i]));
  const bodyLo = close.map((value, i) => Math.min(value, open[i]));
  const body = bodyHi.map((value, i) => value - bodyLo[i]);
  const bodyAvg = ema(body, 14);
  const upShadow = high.map((value, i) => value - bodyHi[i]);
  const dnShadow = low.map((value, i) => bodyLo[i] - value);
  const range = high.map((value, i) => value - low[i]);
  const dojiBody = body.map((value, i) => range[i] > 0 && value <= range[i] * DOJI_BODY_PERCENT / 100);
  const shadowEquals = upShadow.map((upper, i) => {
    const lower = dnShadow[i];
    if (upper === lower) return true;
    // Pine: x / 0 is na, and a comparison with na is false.
    return lower !== 0 && upper !== 0 && Math.abs(upper - lower) / lower * 100 < SHADOW_EQUALS_PERCENT && Math.abs(lower - upper) / upper * 100 < SHADOW_EQUALS_PERCENT;
  });
  const small = body.map((value, i) => finite(bodyAvg[i]) && value < bodyAvg[i]!);
  const long = body.map((value, i) => finite(bodyAvg[i]) && value > bodyAvg[i]!);
  const sma50 = sma(close, 50);
  const sma200 = trend === 'sma50-sma200' ? sma(close, 200) : [];
  const up = close.map((value, i) => trend === 'none' || (finite(sma50[i]) && value > sma50[i]! && (trend === 'sma50' || (finite(sma200[i]) && sma50[i]! > sma200[i]!))));
  const down = close.map((value, i) => trend === 'none' || (finite(sma50[i]) && value < sma50[i]! && (trend === 'sma50' || (finite(sma200[i]) && sma50[i]! < sma200[i]!))));
  return {
    open, high, low, close, bodyHi, bodyLo, body, bodyAvg, small, long, upShadow, dnShadow, range, dojiBody, up, down,
    bodyMiddle: body.map((value, i) => value / 2 + bodyLo[i]),
    hasUpShadow: upShadow.map((value, i) => value > SHADOW_PERCENT / 100 * body[i]),
    hasDnShadow: dnShadow.map((value, i) => value > SHADOW_PERCENT / 100 * body[i]),
    white: open.map((value, i) => value < close[i]),
    black: open.map((value, i) => value > close[i]),
    doji: dojiBody.map((value, i) => value && shadowEquals[i]),
    marubozu: long.map((value, i) => value && upShadow[i] <= SHADOW_PERCENT / 100 * body[i] && dnShadow[i] <= SHADOW_PERCENT / 100 * body[i]),
  };
}

const hammerShape = (c: Candles, i: number) => c.small[i] && c.body[i] > 0 && c.bodyLo[i] > (c.high[i] + c.low[i]) / 2 && c.dnShadow[i] >= FACTOR * c.body[i] && !c.hasUpShadow[i];
const starShape = (c: Candles, i: number) => c.small[i] && c.body[i] > 0 && c.bodyHi[i] < (c.high[i] + c.low[i]) / 2 && c.upShadow[i] >= FACTOR * c.body[i] && !c.hasDnShadow[i];
const harami = (c: Candles, i: number) => c.long[i - 1] && c.high[i] <= c.bodyHi[i - 1] && c.low[i] >= c.bodyLo[i - 1];
const spinningTop = (c: Candles, i: number) => c.dnShadow[i] >= c.range[i] / 100 * SPINNING_TOP_PERCENT && c.upShadow[i] >= c.range[i] / 100 * SPINNING_TOP_PERCENT && !c.dojiBody[i];
const noUpShadow = (c: Candles, i: number) => c.range[i] * SHADOW_PERCENT / 100 > c.upShadow[i];
const noDnShadow = (c: Candles, i: number) => c.range[i] * SHADOW_PERCENT / 100 > c.dnShadow[i];
const near = (a: number, b: number, average: MaybeNumber) => finite(average) && Math.abs(a - b) <= average * 0.05;
const threeInside = (c: Candles, i: number, white: boolean) => [3, 2, 1].every((k) => c.small[i - k] && (white ? c.white[i - k] : c.black[i - k])
  && (white ? c.open[i - k] > c.low[i - 4] && c.close[i - k] < c.high[i - 4] : c.open[i - k] < c.high[i - 4] && c.close[i - k] > c.low[i - 4]));

const DEFINITIONS: ReadonlyArray<CandlestickPattern & { lookback: number; detect: Detector }> = [
  { key: 'abandoned-baby-bearish', name: 'Abandoned Baby - Bearish', label: 'AB', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.up[i - 2] && c.white[i - 2] && c.dojiBody[i - 1] && c.high[i - 2] < c.low[i - 1] && c.black[i] && c.low[i - 1] > c.high[i] },
  { key: 'abandoned-baby-bullish', name: 'Abandoned Baby - Bullish', label: 'AB', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.down[i - 2] && c.black[i - 2] && c.dojiBody[i - 1] && c.low[i - 2] > c.high[i - 1] && c.white[i] && c.high[i - 1] < c.low[i] },
  { key: 'dark-cloud-cover', name: 'Dark Cloud Cover - Bearish', label: 'DCC', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.up[i - 1] && c.white[i - 1] && c.long[i - 1] && c.black[i] && c.open[i] >= c.high[i - 1] && c.close[i] < c.bodyMiddle[i - 1] && c.close[i] > c.open[i - 1] },
  { key: 'doji', name: 'Doji', label: 'D', direction: 'neutral', lookback: 0,
    detect: (c, i) => c.doji[i] && !(c.dojiBody[i] && c.upShadow[i] <= c.body[i]) && !(c.dojiBody[i] && c.dnShadow[i] <= c.body[i]) },
  { key: 'doji-star-bearish', name: 'Doji Star - Bearish', label: 'DS', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.up[i] && c.white[i - 1] && c.long[i - 1] && c.dojiBody[i] && c.bodyLo[i] > c.bodyHi[i - 1] },
  { key: 'doji-star-bullish', name: 'Doji Star - Bullish', label: 'DS', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.down[i] && c.black[i - 1] && c.long[i - 1] && c.dojiBody[i] && c.bodyHi[i] < c.bodyLo[i - 1] },
  { key: 'downside-tasuki-gap', name: 'Downside Tasuki Gap - Bearish', label: 'DTG', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.small[i - 1] && c.down[i] && c.black[i - 2] && c.bodyHi[i - 1] < c.bodyLo[i - 2] && c.black[i - 1] && c.white[i]
      && c.bodyHi[i] <= c.bodyLo[i - 2] && c.bodyHi[i] >= c.bodyHi[i - 1] },
  { key: 'dragonfly-doji', name: 'Dragonfly Doji - Bullish', label: 'DD', direction: 'bullish', lookback: 0,
    detect: (c, i) => c.dojiBody[i] && c.upShadow[i] <= c.body[i] },
  { key: 'engulfing-bearish', name: 'Engulfing - Bearish', label: 'BE', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.up[i] && c.black[i] && c.long[i] && c.white[i - 1] && c.small[i - 1] && c.close[i] <= c.open[i - 1] && c.open[i] >= c.close[i - 1]
      && (c.close[i] < c.open[i - 1] || c.open[i] > c.close[i - 1]) },
  { key: 'engulfing-bullish', name: 'Engulfing - Bullish', label: 'BE', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.down[i] && c.white[i] && c.long[i] && c.black[i - 1] && c.small[i - 1] && c.close[i] >= c.open[i - 1] && c.open[i] <= c.close[i - 1]
      && (c.close[i] > c.open[i - 1] || c.open[i] < c.close[i - 1]) },
  { key: 'evening-doji-star', name: 'Evening Doji Star - Bearish', label: 'EDS', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.dojiBody[i - 1] && c.long[i] && c.up[i] && c.white[i - 2] && c.bodyLo[i - 1] > c.bodyHi[i - 2] && c.black[i]
      && c.bodyLo[i] <= c.bodyMiddle[i - 2] && c.bodyLo[i] > c.bodyLo[i - 2] && c.bodyLo[i - 1] > c.bodyHi[i] },
  { key: 'evening-star', name: 'Evening Star - Bearish', label: 'ES', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.small[i - 1] && c.long[i] && c.up[i] && c.white[i - 2] && c.bodyLo[i - 1] > c.bodyHi[i - 2] && c.black[i]
      && c.bodyLo[i] <= c.bodyMiddle[i - 2] && c.bodyLo[i] > c.bodyLo[i - 2] && c.bodyLo[i - 1] > c.bodyHi[i] },
  { key: 'falling-three-methods', name: 'Falling Three Methods - Bearish', label: 'F3M', direction: 'bearish', lookback: 4,
    detect: (c, i) => c.down[i - 4] && c.long[i - 4] && c.black[i - 4] && threeInside(c, i, true) && c.long[i] && c.black[i] && c.close[i] < c.close[i - 4] },
  { key: 'falling-window', name: 'Falling Window - Bearish', label: 'FW', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.down[i - 1] && c.range[i] !== 0 && c.range[i - 1] !== 0 && c.high[i] < c.low[i - 1] },
  { key: 'gravestone-doji', name: 'Gravestone Doji - Bearish', label: 'GD', direction: 'bearish', lookback: 0,
    detect: (c, i) => c.dojiBody[i] && c.dnShadow[i] <= c.body[i] },
  { key: 'hammer', name: 'Hammer - Bullish', label: 'H', direction: 'bullish', lookback: 0, detect: (c, i) => hammerShape(c, i) && c.down[i] },
  { key: 'hanging-man', name: 'Hanging Man - Bearish', label: 'HM', direction: 'bearish', lookback: 0, detect: (c, i) => hammerShape(c, i) && c.up[i] },
  { key: 'harami-bearish', name: 'Harami - Bearish', label: 'BH', direction: 'bearish', lookback: 1,
    detect: (c, i) => harami(c, i) && c.white[i - 1] && c.up[i - 1] && c.black[i] && c.small[i] },
  { key: 'harami-bullish', name: 'Harami - Bullish', label: 'BH', direction: 'bullish', lookback: 1,
    detect: (c, i) => harami(c, i) && c.black[i - 1] && c.down[i - 1] && c.white[i] && c.small[i] },
  { key: 'harami-cross-bearish', name: 'Harami Cross - Bearish', label: 'HC', direction: 'bearish', lookback: 1,
    detect: (c, i) => harami(c, i) && c.white[i - 1] && c.up[i - 1] && c.dojiBody[i] },
  { key: 'harami-cross-bullish', name: 'Harami Cross - Bullish', label: 'HC', direction: 'bullish', lookback: 1,
    detect: (c, i) => harami(c, i) && c.black[i - 1] && c.down[i - 1] && c.dojiBody[i] },
  { key: 'inverted-hammer', name: 'Inverted Hammer - Bullish', label: 'IH', direction: 'bullish', lookback: 0, detect: (c, i) => starShape(c, i) && c.down[i] },
  { key: 'kicking-bearish', name: 'Kicking - Bearish', label: 'K', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.marubozu[i - 1] && c.white[i - 1] && c.marubozu[i] && c.black[i] && c.low[i - 1] > c.high[i] },
  { key: 'kicking-bullish', name: 'Kicking - Bullish', label: 'K', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.marubozu[i - 1] && c.black[i - 1] && c.marubozu[i] && c.white[i] && c.high[i - 1] < c.low[i] },
  { key: 'long-lower-shadow', name: 'Long Lower Shadow - Bullish', label: 'LS', direction: 'bullish', lookback: 0,
    detect: (c, i) => c.dnShadow[i] > c.range[i] / 100 * LONG_SHADOW_PERCENT },
  { key: 'long-upper-shadow', name: 'Long Upper Shadow - Bearish', label: 'LS', direction: 'bearish', lookback: 0,
    detect: (c, i) => c.upShadow[i] > c.range[i] / 100 * LONG_SHADOW_PERCENT },
  { key: 'marubozu-black', name: 'Marubozu Black - Bearish', label: 'MB', direction: 'bearish', lookback: 0, detect: (c, i) => c.marubozu[i] && c.black[i] },
  { key: 'marubozu-white', name: 'Marubozu White - Bullish', label: 'MW', direction: 'bullish', lookback: 0, detect: (c, i) => c.marubozu[i] && c.white[i] },
  { key: 'morning-doji-star', name: 'Morning Doji Star - Bullish', label: 'MDS', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.dojiBody[i - 1] && c.long[i] && c.down[i] && c.black[i - 2] && c.bodyHi[i - 1] < c.bodyLo[i - 2] && c.white[i]
      && c.bodyHi[i] >= c.bodyMiddle[i - 2] && c.bodyHi[i] < c.bodyHi[i - 2] && c.bodyHi[i - 1] < c.bodyLo[i] },
  { key: 'morning-star', name: 'Morning Star - Bullish', label: 'MS', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.small[i - 1] && c.long[i] && c.down[i] && c.black[i - 2] && c.bodyHi[i - 1] < c.bodyLo[i - 2] && c.white[i]
      && c.bodyHi[i] >= c.bodyMiddle[i - 2] && c.bodyHi[i] < c.bodyHi[i - 2] && c.bodyHi[i - 1] < c.bodyLo[i] },
  { key: 'on-neck', name: 'On Neck - Bearish', label: 'ON', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.down[i] && c.black[i - 1] && c.long[i - 1] && c.white[i] && c.open[i] < c.close[i - 1] && c.small[i] && c.range[i] !== 0
      && near(c.close[i], c.low[i - 1], c.bodyAvg[i]) },
  { key: 'piercing', name: 'Piercing - Bullish', label: 'P', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.down[i - 1] && c.black[i - 1] && c.long[i - 1] && c.white[i] && c.open[i] <= c.low[i - 1] && c.close[i] > c.bodyMiddle[i - 1] && c.close[i] < c.open[i - 1] },
  { key: 'rising-three-methods', name: 'Rising Three Methods - Bullish', label: 'R3M', direction: 'bullish', lookback: 4,
    detect: (c, i) => c.up[i - 4] && c.long[i - 4] && c.white[i - 4] && threeInside(c, i, false) && c.long[i] && c.white[i] && c.close[i] > c.close[i - 4] },
  { key: 'rising-window', name: 'Rising Window - Bullish', label: 'RW', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.up[i - 1] && c.range[i] !== 0 && c.range[i - 1] !== 0 && c.low[i] > c.high[i - 1] },
  { key: 'shooting-star', name: 'Shooting Star - Bearish', label: 'SS', direction: 'bearish', lookback: 0, detect: (c, i) => starShape(c, i) && c.up[i] },
  { key: 'spinning-top-black', name: 'Spinning Top Black', label: 'STB', direction: 'neutral', lookback: 0, detect: (c, i) => spinningTop(c, i) && c.black[i] },
  { key: 'spinning-top-white', name: 'Spinning Top White', label: 'STW', direction: 'neutral', lookback: 0, detect: (c, i) => spinningTop(c, i) && c.white[i] },
  { key: 'three-black-crows', name: 'Three Black Crows - Bearish', label: '3BC', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.long[i] && c.long[i - 1] && c.long[i - 2] && c.black[i] && c.black[i - 1] && c.black[i - 2] && c.close[i] < c.close[i - 1] && c.close[i - 1] < c.close[i - 2]
      && c.open[i] > c.close[i - 1] && c.open[i] < c.open[i - 1] && c.open[i - 1] > c.close[i - 2] && c.open[i - 1] < c.open[i - 2]
      && noDnShadow(c, i) && noDnShadow(c, i - 1) && noDnShadow(c, i - 2) },
  { key: 'three-white-soldiers', name: 'Three White Soldiers - Bullish', label: '3WS', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.long[i] && c.long[i - 1] && c.long[i - 2] && c.white[i] && c.white[i - 1] && c.white[i - 2] && c.close[i] > c.close[i - 1] && c.close[i - 1] > c.close[i - 2]
      && c.open[i] < c.close[i - 1] && c.open[i] > c.open[i - 1] && c.open[i - 1] < c.close[i - 2] && c.open[i - 1] > c.open[i - 2]
      && noUpShadow(c, i) && noUpShadow(c, i - 1) && noUpShadow(c, i - 2) },
  { key: 'tri-star-bearish', name: 'Tri-Star - Bearish', label: '3S', direction: 'bearish', lookback: 2,
    detect: (c, i) => c.doji[i - 2] && c.doji[i - 1] && c.doji[i] && c.up[i - 2] && c.bodyHi[i - 2] < c.bodyLo[i - 1] && c.bodyLo[i - 1] > c.bodyHi[i] },
  { key: 'tri-star-bullish', name: 'Tri-Star - Bullish', label: '3S', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.doji[i - 2] && c.doji[i - 1] && c.doji[i] && c.down[i - 2] && c.bodyLo[i - 2] > c.bodyHi[i - 1] && c.bodyHi[i - 1] < c.bodyLo[i] },
  { key: 'tweezer-bottom', name: 'Tweezer Bottom - Bullish', label: 'TB', direction: 'bullish', lookback: 1,
    detect: (c, i) => c.down[i - 1] && (!c.dojiBody[i] || (c.hasUpShadow[i] && c.hasDnShadow[i])) && near(c.low[i], c.low[i - 1], c.bodyAvg[i])
      && c.black[i - 1] && c.white[i] && c.long[i - 1] },
  { key: 'tweezer-top', name: 'Tweezer Top - Bearish', label: 'TT', direction: 'bearish', lookback: 1,
    detect: (c, i) => c.up[i - 1] && (!c.dojiBody[i] || (c.hasUpShadow[i] && c.hasDnShadow[i])) && near(c.high[i], c.high[i - 1], c.bodyAvg[i])
      && c.white[i - 1] && c.black[i] && c.long[i - 1] },
  { key: 'upside-tasuki-gap', name: 'Upside Tasuki Gap - Bullish', label: 'UTG', direction: 'bullish', lookback: 2,
    detect: (c, i) => c.long[i - 2] && c.small[i - 1] && c.up[i] && c.white[i - 2] && c.bodyLo[i - 1] > c.bodyHi[i - 2] && c.white[i - 1] && c.black[i]
      && c.bodyLo[i] >= c.bodyHi[i - 2] && c.bodyLo[i] <= c.bodyLo[i - 1] },
];

export const CANDLESTICK_PATTERNS: readonly CandlestickPattern[] = DEFINITIONS.map(({ key, name, label, direction }) => ({ key, name, label, direction }));

/** Which patterns a `patterns` input selects: all, one direction, or one pattern by key. */
export function selectedCandlestickPatterns(selection: string): CandlestickPattern[] {
  if (selection === 'bullish' || selection === 'bearish' || selection === 'neutral') return CANDLESTICK_PATTERNS.filter((pattern) => pattern.direction === selection);
  const one = CANDLESTICK_PATTERNS.find((pattern) => pattern.key === selection);
  return one ? [one] : [...CANDLESTICK_PATTERNS];
}

/** For each selected pattern, whether it completes at each bar. */
export function detectCandlestickPatterns(
  open: readonly number[], high: readonly number[], low: readonly number[], close: readonly number[], trend: CandlestickTrend, patterns: readonly CandlestickPattern[],
): Map<string, boolean[]> {
  const c = candles(open, high, low, close, trend);
  const wanted = new Set(patterns.map((pattern) => pattern.key));
  const result = new Map<string, boolean[]>();
  for (const definition of DEFINITIONS) {
    if (!wanted.has(definition.key)) continue;
    result.set(definition.key, close.map((_, i) => i >= definition.lookback && definition.detect(c, i)));
  }
  return result;
}

const DIRECTION_STYLE: Record<CandlestickDirection, { color: string; marker: 'arrowUp' | 'arrowDown' | 'circle'; position: 'aboveBar' | 'belowBar' }> = {
  bullish: { color: '#089981', marker: 'arrowUp', position: 'belowBar' },
  bearish: { color: '#f23645', marker: 'arrowDown', position: 'aboveBar' },
  neutral: { color: '#787b86', marker: 'circle', position: 'aboveBar' },
};

/**
 * One marker output per selected pattern. A point is the bar the pattern completes on, valued at the bar's low (bullish)
 * or high (bearish, neutral), so an alert on the output's value greater than 0 fires when the pattern appears.
 */
export function candlestickPatternOutputs(id: string, bars: readonly MarketBar[], selection: string, trend: string): TradingViewBuiltInOutput[] {
  const num = (key: 'open' | 'high' | 'low' | 'close') => bars.map((bar) => {
    const value = Number(bar[key]);
    return Number.isFinite(value) ? value : 0;
  });
  const open = num('open'); const high = num('high'); const low = num('low'); const close = num('close');
  const patterns = selectedCandlestickPatterns(selection);
  const hits = detectCandlestickPatterns(open, high, low, close, trend === 'sma50-sma200' || trend === 'none' ? trend : 'sma50', patterns);
  return patterns.map((pattern) => {
    const style = DIRECTION_STYLE[pattern.direction];
    const found = hits.get(pattern.key)!;
    return {
      key: `${id}:${pattern.key}`,
      title: pattern.name,
      pane: 0,
      kind: 'line',
      points: bars.flatMap((bar, i) => (found[i] ? [{ time: bar.start_time, value: pattern.direction === 'bullish' ? low[i] : high[i] }] : [])),
      color: style.color,
      render: 'markers',
      marker: style.marker,
      markerPosition: style.position,
      markerText: pattern.label,
      labelsOnPriceScale: false,
      valuesInStatusLine: false,
    };
  });
}
