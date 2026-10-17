/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import type { MarketBar } from '../tradingTypes';
import { findPatternPivots } from './autoPatterns';
import { CANDLESTICK_PATTERNS, selectedCandlestickPatterns } from './candlestickPatterns';
import { drawingIndicatorOutputs } from './drawingIndicators';
import { UTC_SESSION, sessionClock, sessionPeriods, type SessionPeriod, type TradingSessionSpec } from './tradingSessions';

export type TradingViewBuiltInId = `tv-${string}`;
export type TradingViewBuiltInDefinition = {
  id: string;
  name: string;
  defaultPeriod: number;
  pane: 0 | 1;
  available: boolean;
  requirement?: string;
};
export type TradingViewBuiltInOutput = {
  key: string;
  title: string;
  pane: 0 | 1;
  kind: 'line' | 'histogram' | 'bar-colors' | 'background' | 'viewport-average';
  points: Array<{ time: string; value: number; color?: string; label?: string }>;
  color?: string;
  lineStyle?: 'solid' | 'dotted' | 'dashed' | 'large-dashed' | 'sparse-dotted';
  lineWidth?: 1 | 2 | 3 | 4;
  labelsOnPriceScale?: boolean;
  valuesInStatusLine?: boolean;
  inputsInStatusLine?: boolean;
  /** How the chart draws the points: a joined line (default), horizontal levels with gaps where there is no value, or markers. */
  render?: IndicatorRender;
  marker?: IndicatorMarkerShape;
  /** Markers: text beside each marker, and above or below the bar instead of at the point's price. */
  markerText?: string;
  markerPosition?: 'aboveBar' | 'belowBar';
};
export type IndicatorRender = 'line' | 'levels' | 'markers';
export type IndicatorMarkerShape = 'arrowUp' | 'arrowDown' | 'circle';
type ParamValue = number | string;
export type TradingViewBuiltInInstance = {
  id: string;
  period: number;
  fastPeriod?: number;
  slowPeriod?: number;
  signalPeriod?: number;
  standardDeviations?: number;
  anchorTime?: string | null;
  /** Instrument id of the second series (Correlation Coefficient). */
  compareSymbol?: string | null;
  /** Indicator-specific inputs declared in `tradingViewBuiltInInputs`; missing or invalid values use the defaults. */
  params?: Record<string, ParamValue>;
  /** The chart instrument's session calendar (not stored with the indicator); UTC when absent. */
  session?: TradingSessionSpec | null;
};
export type TradingViewBuiltInParamSpec =
  | { key: string; label: string; kind: 'number'; default: number; min: number; integer?: boolean; step?: number }
  | { key: string; label: string; kind: 'select'; default: string; options: ReadonlyArray<{ value: string; label: string }> };
/** Inputs of a built-in beyond `period`: the period's label (null hides it) and its params. */
export type TradingViewBuiltInInputs = { periodLabel: string | null; params: readonly TradingViewBuiltInParamSpec[] };
/** Series an indicator reads besides the chart's own bars. */
export type TradingViewBuiltInSeries = {
  /** Bars of `compareSymbol`, in any order; aligned to the chart by start time. Ignored when the instance has no `compareSymbol`. */
  compareBars?: readonly MarketBar[];
};

export type MaybeNumber = number | null;
type SupportedConfig = { defaultPeriod: number; pane: 0 | 1 };

const BUILTIN_NAMES = `
1 year active supply %
24-hour Volume
Accumulation Distribution (ADL)
Active addresses with contracts
Addresses with balance ≥ X (% of supply)
Addresses with balance ≥ X (USD)
Advance/Decline Line
Advance/Decline Ratio
Advance/Decline Ratio (Bars)
All Candlestick Patterns
Analyst price forecast
Arnaud Legoux Moving Average
Aroon Indicator
Aroon Oscillator
Auto Fib Extension
Auto Fib Retracement
Auto key levels
Auto Pitchfork
Auto Trendlines
Average Daily Range (ADR) indicator
Average Directional Index (ADX)
Average transaction volume
Average True Range (ATR)
Awesome Oscillator (AO)
Balance of Power (BOP)
Basis
BBTrend
Block height
Blocks mined
Bollinger Bands (BB)
Bollinger Bands %b (%b)
Bollinger BandWidth (BBW)
Bollinger Bars
Bull Bear Power
Chaikin Money Flow (CMF)
Chaikin Oscillator
Chande Kroll Stop
Chande Momentum Oscillator (CMO)
Chandelier Exit
Chop Zone
Choppiness Index (CHOP)
Commodity Channel Index (CCI)
Connors RSI (CRSI)
Coppock Curve
Correlation Coefficient (CC)
Created UTXOs
Cumulative Volume Delta
Cumulative Volume Index (CVI)
Detrended Price Oscillator (DPO)
Difficulty
Directional Movement (DMI)
Dividend Yield
Donchian Channels (DC)
Double Exponential Moving Average (EMA)
Ease of Movement (EOM)
El Salvador Government balance
Elder's Force Index (EFI)
Envelope (ENV)
ETF balances
ETF flows
Ethereum new deposits
Ethereum new unique depositors
Ethereum new value staked
Ethereum total number of deposits
Ethereum total unique depositors
Ethereum total value staked
Exponential Moving Average
Fisher Transform
Funding rate: a guide to market sentiment
Hash Rate
Held tokens in addresses ≥ X (% of supply)
Held tokens in addresses ≥ X (tokens)
Held tokens in addresses ≥ X (USD)
Historical Volatility
Hull Moving Average
Ichimoku Cloud
Index price
Kaufman's Adaptive Moving Average (KAMA)
Keltner Channels (KC)
Klinger Oscillator
Know Sure Thing (KST)
Large transaction volume
Learn using seasonals
Least Squares Moving Average
Linear Regression
Liquidation data: what to watch and why it matters
Long / Short Ratio Accounts
Long Short Accounts %
MA Cross
Mark price
Mass Index
McGinley Dynamic
Mean block interval
Mean block size in bytes
Mean gas used
Mean transaction fees
Mean transaction gas limit
Mean transaction gas price
Mean transaction size in bytes
Mean transfer volume
Mean UTXO value created
Mean UTXO value spent
Median
Median block interval
Median gas used
Median transaction fees
Median transaction gas limit
Median transaction gas price
Median transfer volume
Median UTXO value created
Median UTXO value spent
Momentum
Money Flow (MFI)
Moon Phases
Moving average convergence divergence (MACD) indicator
Moving Average Ribbon
Moving Averages
MovingAvg Cross
MovingAvg2Line Cross
Multi-Time Period Charts indicator
Negative Volume Index (NVI)
Net Volume
On Balance Volume (OBV)
Open Interest
Parabolic SAR (SAR)
Percentage Price Oscillator (PPO)
Percentage Volume Oscillator (PVO)
Performance
Pivot Points High Low
Pivot Points Standard
Positive Volume Index (PVI)
Power-Law Model
Premium
Price Momentum Oscillator (PMO)
Price target - indicator
Price Volume Trend (PVT)
Pring's Special K
Rank Correlation Index (RCI)
Rate of Change (ROC)
RCI Ribbon
Realized market cap
Receiving addresses
Relative Strength Index (RSI)
Relative Vigor Index
Relative Volatility Index
Relative Volume at Time
Rob Booker - ADX Breakout
Rob Booker - Knoxville Divergence
Rob Booker Intraday Pivot Points
Rob Booker Missed Pivot Points
Rob Booker Reversal
Rob Booker Ziv Ghost Pivots
RSI divergence indicator
RVT ratio, 90 days
Seasonality
Sending addresses
Simple Moving Average
SMI Ergodic Indicator
SMI Ergodic Oscillator
Smoothed Moving Average
Spent Output Profit Ratio (SOPR)
Spent UTXOs
Stochastic (STOCH)
Stochastic Momentum Index (SMI)
Stochastic RSI (STOCH RSI)
Stock-to-Flow Ratio in USD
Supertrend
Supply Equality Ratio
Technical Ratings
Time Weighted Average Price
Top trader long/short accounts
Top trader long/short accounts ratio
Top trader long/short positions
Top trader long/short positions ratio
Total block size in bytes
Total gas used
Total transactions size in bytes
Total UTXO value created
Total UTXO value spent
Total UTXOs
Trading Sessions
Transaction fees
Transaction rate
Transfer count
Transfer rate
Trend Strength Index
Triple EMA
TRIX
True Strength Index
Ulcer Index
Ultimate Oscillator (UO)
Understanding crypto open interest
Up/Down Volume
US spot crypto ETF balances
US spot crypto ETF flows
Visible Average Price
Volatility Stop
Volume
Volume Delta
Volume Weighted Average Price (VWAP)
Volume-Weighted Moving Average (VWMA)
Vortex Indicator
VWAP Auto Anchored
Weighted Moving Average
Williams %R (%R)
Williams Alligator
Williams Fractal
Woodies CCI
Zig Zag
`.trim().split('\n');

const NATIVE_ALIASES: Record<string, { id: string; defaultPeriod: number; pane: 0 | 1 }> = {
  'Average True Range (ATR)': { id: 'atr', defaultPeriod: 14, pane: 1 },
  'Bollinger Bands (BB)': { id: 'bollinger', defaultPeriod: 20, pane: 0 },
  'Exponential Moving Average': { id: 'ema', defaultPeriod: 20, pane: 0 },
  'Moving average convergence divergence (MACD) indicator': { id: 'macd', defaultPeriod: 9, pane: 1 },
  'Relative Strength Index (RSI)': { id: 'rsi', defaultPeriod: 14, pane: 1 },
  'RSI divergence indicator': { id: 'rsi-divergence', defaultPeriod: 14, pane: 1 },
  'Simple Moving Average': { id: 'sma', defaultPeriod: 20, pane: 0 },
  'Stochastic RSI (STOCH RSI)': { id: 'stochastic-rsi', defaultPeriod: 14, pane: 1 },
  'Volume Weighted Average Price (VWAP)': { id: 'vwap', defaultPeriod: 1, pane: 0 },
};

const SUPPORTED: Record<string, SupportedConfig> = {
  '24-hour Volume': { defaultPeriod: 1, pane: 1 },
  'Accumulation Distribution (ADL)': { defaultPeriod: 1, pane: 1 },
  'Arnaud Legoux Moving Average': { defaultPeriod: 9, pane: 0 },
  'Aroon Indicator': { defaultPeriod: 14, pane: 1 },
  'Aroon Oscillator': { defaultPeriod: 14, pane: 1 },
  'Average Daily Range (ADR) indicator': { defaultPeriod: 14, pane: 1 },
  'Average Directional Index (ADX)': { defaultPeriod: 14, pane: 1 },
  'Awesome Oscillator (AO)': { defaultPeriod: 34, pane: 1 },
  'Balance of Power (BOP)': { defaultPeriod: 14, pane: 1 },
  'BBTrend': { defaultPeriod: 20, pane: 1 },
  'Bollinger Bands %b (%b)': { defaultPeriod: 20, pane: 1 },
  'Bollinger BandWidth (BBW)': { defaultPeriod: 20, pane: 1 },
  'Bull Bear Power': { defaultPeriod: 13, pane: 1 },
  'Chaikin Money Flow (CMF)': { defaultPeriod: 20, pane: 1 },
  'Chaikin Oscillator': { defaultPeriod: 10, pane: 1 },
  'Chande Kroll Stop': { defaultPeriod: 10, pane: 0 },
  'Chande Momentum Oscillator (CMO)': { defaultPeriod: 14, pane: 1 },
  'Chandelier Exit': { defaultPeriod: 22, pane: 0 },
  'Choppiness Index (CHOP)': { defaultPeriod: 14, pane: 1 },
  'Commodity Channel Index (CCI)': { defaultPeriod: 20, pane: 1 },
  'Connors RSI (CRSI)': { defaultPeriod: 100, pane: 1 },
  'Coppock Curve': { defaultPeriod: 14, pane: 1 },
  'Correlation Coefficient (CC)': { defaultPeriod: 20, pane: 1 },
  'Detrended Price Oscillator (DPO)': { defaultPeriod: 20, pane: 1 },
  'Directional Movement (DMI)': { defaultPeriod: 14, pane: 1 },
  'Donchian Channels (DC)': { defaultPeriod: 20, pane: 0 },
  'Double Exponential Moving Average (EMA)': { defaultPeriod: 9, pane: 0 },
  'Ease of Movement (EOM)': { defaultPeriod: 14, pane: 1 },
  "Elder's Force Index (EFI)": { defaultPeriod: 13, pane: 1 },
  'Envelope (ENV)': { defaultPeriod: 20, pane: 0 },
  'Fisher Transform': { defaultPeriod: 9, pane: 1 },
  'Historical Volatility': { defaultPeriod: 20, pane: 1 },
  'Hull Moving Average': { defaultPeriod: 9, pane: 0 },
  'Ichimoku Cloud': { defaultPeriod: 26, pane: 0 },
  "Kaufman's Adaptive Moving Average (KAMA)": { defaultPeriod: 10, pane: 0 },
  'Keltner Channels (KC)': { defaultPeriod: 20, pane: 0 },
  'Klinger Oscillator': { defaultPeriod: 55, pane: 1 },
  'Know Sure Thing (KST)': { defaultPeriod: 30, pane: 1 },
  'Least Squares Moving Average': { defaultPeriod: 25, pane: 0 },
  'Linear Regression': { defaultPeriod: 20, pane: 0 },
  'MA Cross': { defaultPeriod: 9, pane: 0 },
  'Mass Index': { defaultPeriod: 25, pane: 1 },
  'McGinley Dynamic': { defaultPeriod: 14, pane: 0 },
  'Median': { defaultPeriod: 9, pane: 0 },
  'Momentum': { defaultPeriod: 10, pane: 1 },
  'Money Flow (MFI)': { defaultPeriod: 14, pane: 1 },
  'Moving Average Ribbon': { defaultPeriod: 20, pane: 0 },
  'Moving Averages': { defaultPeriod: 20, pane: 0 },
  'MovingAvg Cross': { defaultPeriod: 9, pane: 0 },
  'MovingAvg2Line Cross': { defaultPeriod: 9, pane: 0 },
  'Negative Volume Index (NVI)': { defaultPeriod: 1, pane: 1 },
  'Net Volume': { defaultPeriod: 1, pane: 1 },
  'On Balance Volume (OBV)': { defaultPeriod: 1, pane: 1 },
  'Parabolic SAR (SAR)': { defaultPeriod: 2, pane: 0 },
  'Percentage Price Oscillator (PPO)': { defaultPeriod: 9, pane: 1 },
  'Percentage Volume Oscillator (PVO)': { defaultPeriod: 9, pane: 1 },
  'Performance': { defaultPeriod: 1, pane: 1 },
  'Pivot Points High Low': { defaultPeriod: 10, pane: 0 },
  'Pivot Points Standard': { defaultPeriod: 20, pane: 0 },
  'Positive Volume Index (PVI)': { defaultPeriod: 1, pane: 1 },
  'Price Momentum Oscillator (PMO)': { defaultPeriod: 35, pane: 1 },
  'Price Volume Trend (PVT)': { defaultPeriod: 1, pane: 1 },
  "Pring's Special K": { defaultPeriod: 30, pane: 1 },
  'Rank Correlation Index (RCI)': { defaultPeriod: 9, pane: 1 },
  'Rate of Change (ROC)': { defaultPeriod: 9, pane: 1 },
  'RCI Ribbon': { defaultPeriod: 9, pane: 1 },
  'Relative Vigor Index': { defaultPeriod: 10, pane: 1 },
  'Relative Volatility Index': { defaultPeriod: 14, pane: 1 },
  'Relative Volume at Time': { defaultPeriod: 5, pane: 1 },
  'Rob Booker - ADX Breakout': { defaultPeriod: 20, pane: 0 },
  'Rob Booker - Knoxville Divergence': { defaultPeriod: 150, pane: 0 },
  'Rob Booker Intraday Pivot Points': { defaultPeriod: 1, pane: 0 },
  'Rob Booker Missed Pivot Points': { defaultPeriod: 10, pane: 0 },
  'Rob Booker Reversal': { defaultPeriod: 14, pane: 0 },
  'Rob Booker Ziv Ghost Pivots': { defaultPeriod: 1, pane: 0 },
  'SMI Ergodic Indicator': { defaultPeriod: 20, pane: 1 },
  'SMI Ergodic Oscillator': { defaultPeriod: 20, pane: 1 },
  'Smoothed Moving Average': { defaultPeriod: 20, pane: 0 },
  'Stochastic (STOCH)': { defaultPeriod: 14, pane: 1 },
  'Stochastic Momentum Index (SMI)': { defaultPeriod: 14, pane: 1 },
  'Supertrend': { defaultPeriod: 10, pane: 0 },
  'Technical Ratings': { defaultPeriod: 14, pane: 1 },
  'Time Weighted Average Price': { defaultPeriod: 1, pane: 0 },
  'Trend Strength Index': { defaultPeriod: 20, pane: 1 },
  'Triple EMA': { defaultPeriod: 9, pane: 0 },
  'TRIX': { defaultPeriod: 18, pane: 1 },
  'True Strength Index': { defaultPeriod: 25, pane: 1 },
  'Ulcer Index': { defaultPeriod: 14, pane: 1 },
  'Ultimate Oscillator (UO)': { defaultPeriod: 28, pane: 1 },
  'Up/Down Volume': { defaultPeriod: 1, pane: 1 },
  'Volatility Stop': { defaultPeriod: 20, pane: 0 },
  'Volume': { defaultPeriod: 1, pane: 1 },
  'Volume-Weighted Moving Average (VWMA)': { defaultPeriod: 20, pane: 0 },
  'Vortex Indicator': { defaultPeriod: 14, pane: 1 },
  'Weighted Moving Average': { defaultPeriod: 9, pane: 0 },
  'Williams %R (%R)': { defaultPeriod: 14, pane: 1 },
  'Williams Alligator': { defaultPeriod: 13, pane: 0 },
  'Williams Fractal': { defaultPeriod: 2, pane: 0 },
  'Woodies CCI': { defaultPeriod: 14, pane: 1 },
  'Zig Zag': { defaultPeriod: 5, pane: 0 },
  // TVP-6.2: indicators that draw (drawingIndicators.ts).
  'Auto Fib Extension': { defaultPeriod: 10, pane: 0 },
  'Auto Fib Retracement': { defaultPeriod: 10, pane: 0 },
  'Auto key levels': { defaultPeriod: 200, pane: 0 },
  'Auto Pitchfork': { defaultPeriod: 10, pane: 0 },
  'Auto Trendlines': { defaultPeriod: 5, pane: 0 },
  'Bollinger Bars': { defaultPeriod: 20, pane: 0 },
  'Chop Zone': { defaultPeriod: 30, pane: 1 },
  'Advance/Decline Ratio (Bars)': { defaultPeriod: 9, pane: 1 },
  'Moon Phases': { defaultPeriod: 1, pane: 0 },
  'Multi-Time Period Charts indicator': { defaultPeriod: 1, pane: 0 },
  'Seasonality': { defaultPeriod: 3, pane: 1 },
  'Trading Sessions': { defaultPeriod: 1, pane: 0 },
  'Visible Average Price': { defaultPeriod: 1, pane: 0 },
  'VWAP Auto Anchored': { defaultPeriod: 200, pane: 0 },
  // TVP-6.3: candlestick patterns (candlestickPatterns.ts).
  'All Candlestick Patterns': { defaultPeriod: 1, pane: 0 },
};

const SPECIALIZED_REQUIREMENTS: Record<string, string> = {
};

const PERIOD_OPTIONS = { D: 'Day', W: 'Week', M: 'Month' } as const;
const integer = (key: string, label: string, value: number): TradingViewBuiltInParamSpec => ({ key, label, kind: 'number', default: value, min: 1, integer: true });
const level = (key: string, label: string, value: number): TradingViewBuiltInParamSpec => ({ key, label, kind: 'number', default: value, min: 0, step: 1 });
const select = (key: string, label: string, value: string, options: Record<string, string>): TradingViewBuiltInParamSpec => ({
  key, label, kind: 'select', default: value, options: Object.entries(options).map(([optionValue, optionLabel]) => ({ value: optionValue, label: optionLabel })),
});

/** Declared inputs of the built-ins that have more than a period (TVP-6.1). The server registry uses the same keys and defaults. */
const BUILTIN_INPUTS: Record<string, TradingViewBuiltInInputs> = {
  '24-hour Volume': { periodLabel: null, params: [select('source', 'Price source', 'close', { close: 'Close', open: 'Open', high: 'High', low: 'Low', hl2: '(H + L) / 2', hlc3: '(H + L + C) / 3', ohlc4: '(O + H + L + C) / 4' })] },
  'Correlation Coefficient (CC)': { periodLabel: 'Length', params: [] },
  'Relative Volume at Time': { periodLabel: 'Length', params: [select('anchor', 'Anchor period', 'D', PERIOD_OPTIONS), select('mode', 'Calculation mode', 'cumulative', { cumulative: 'Cumulative', regular: 'Regular' })] },
  'Rob Booker - ADX Breakout': { periodLabel: 'Box lookback', params: [integer('adxLength', 'ADX length', 14), level('level', 'ADX level', 18)] },
  'Rob Booker - Knoxville Divergence': { periodLabel: 'Bars back', params: [integer('momentumLength', 'Momentum length', 20), integer('rsiLength', 'RSI length', 21)] },
  'Rob Booker Intraday Pivot Points': { periodLabel: 'Pivot period (hours)', params: [] },
  'Rob Booker Missed Pivot Points': { periodLabel: 'Periods back', params: [select('pivotPeriod', 'Pivot period', 'D', PERIOD_OPTIONS)] },
  'Rob Booker Reversal': {
    periodLabel: 'K period',
    params: [integer('fastLength', 'Fast MA period', 12), integer('slowLength', 'Slow MA period', 26), integer('slowing', 'Slowing', 3), level('upper', 'Stochastic upper', 70), level('lower', 'Stochastic lower', 30)],
  },
  'Rob Booker Ziv Ghost Pivots': { periodLabel: null, params: [select('pivotPeriod', 'Pivot period', 'W', { 240: '4 hours', 480: '8 hours (next day)', W: 'Next week', M: 'Next month' })] },
};
/** Inputs of the TVP-6.2 indicators that draw (drawingIndicators.ts). */
const DRAWING_INPUTS: Record<string, TradingViewBuiltInInputs> = {
  'Auto Fib Extension': { periodLabel: 'Depth', params: [{ key: 'deviation', label: 'Deviation (x ATR)', kind: 'number', default: 3, min: 0, step: 0.5 }] },
  'Auto Fib Retracement': { periodLabel: 'Depth', params: [{ key: 'deviation', label: 'Deviation (x ATR)', kind: 'number', default: 3, min: 0, step: 0.5 }] },
  'Auto key levels': { periodLabel: 'Lookback', params: [integer('count', 'Levels', 5)] },
  'Auto Pitchfork': { periodLabel: 'Depth', params: [{ key: 'deviation', label: 'Deviation (x ATR)', kind: 'number', default: 3, min: 0, step: 0.5 }] },
  'Auto Trendlines': { periodLabel: 'Pivot length', params: [] },
  'Bollinger Bars': { periodLabel: 'Length', params: [{ key: 'deviations', label: 'StdDev', kind: 'number', default: 2, min: 0.1, step: 0.1 }] },
  'Chop Zone': { periodLabel: 'Length', params: [] },
  'Advance/Decline Ratio (Bars)': { periodLabel: 'Length', params: [] },
  'Moon Phases': { periodLabel: null, params: [] },
  'Multi-Time Period Charts indicator': { periodLabel: null, params: [select('period', 'Period', 'D', PERIOD_OPTIONS)] },
  'Seasonality': { periodLabel: 'Years', params: [] },
  'Trading Sessions': { periodLabel: null, params: [] },
  'Visible Average Price': { periodLabel: null, params: [] },
  'VWAP Auto Anchored': { periodLabel: 'Length', params: [select('anchor', 'Anchor', 'highest-high', { 'highest-high': 'Highest high', 'lowest-low': 'Lowest low', 'highest-volume': 'Highest volume' })] },
  'All Candlestick Patterns': {
    periodLabel: null,
    params: [
      select('patterns', 'Patterns', 'all', { all: 'All', bullish: 'Bullish', bearish: 'Bearish', neutral: 'Neutral', ...Object.fromEntries(CANDLESTICK_PATTERNS.map((pattern) => [pattern.key, pattern.name])) }),
      select('trend', 'Detect trend based on', 'sma50', { sma50: 'SMA50', 'sma50-sma200': 'SMA50, SMA200', none: 'No detection' }),
    ],
  },
};
const DRAWING_INDICATORS = new Set(Object.keys(DRAWING_INPUTS));
// TVP-6.4: computed from intrabar data off the worker (intrabarIndicators.ts); the options repeat INTRABAR_LOWER_INTERVALS.
const LOWER_INTERVALS = { auto: 'Auto', '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D' };
const INTRABAR_INPUTS: Record<string, TradingViewBuiltInInputs> = {
  'Volume Delta': { periodLabel: null, params: [select('lowerInterval', 'Intrabar timeframe', 'auto', LOWER_INTERVALS)] },
  'Cumulative Volume Delta': {
    periodLabel: null,
    params: [select('anchor', 'Anchor period', 'D', PERIOD_OPTIONS), select('lowerInterval', 'Intrabar timeframe', 'auto', LOWER_INTERVALS)],
  },
};
Object.assign(BUILTIN_INPUTS, DRAWING_INPUTS, INTRABAR_INPUTS);
const QUICK_WINS = new Set(Object.keys(BUILTIN_INPUTS).filter((name) => !DRAWING_INDICATORS.has(name) && !(name in INTRABAR_INPUTS)));
const SESSION_INDICATORS = new Set(['Time Weighted Average Price', 'Relative Volume at Time', 'Rob Booker Intraday Pivot Points', 'Rob Booker Missed Pivot Points', 'Rob Booker Ziv Ghost Pivots']);

function slugify(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
}

const uniqueNames = [...new Set(BUILTIN_NAMES)];

export const TRADINGVIEW_BUILTIN_DEFINITIONS: readonly TradingViewBuiltInDefinition[] = uniqueNames.map((name) => {
  const alias = NATIVE_ALIASES[name];
  if (alias) return { id: alias.id, name, defaultPeriod: alias.defaultPeriod, pane: alias.pane, available: true };
  const supported = SUPPORTED[name];
  if (supported) return { id: `tv-${slugify(name)}`, name, defaultPeriod: supported.defaultPeriod, pane: supported.pane, available: true };
  return {
    id: `tv-${slugify(name)}`,
    name,
    defaultPeriod: 20,
    pane: 1,
    available: false,
    requirement: SPECIALIZED_REQUIREMENTS[name]
      ?? 'Requires a market breadth, fundamental, derivatives, analyst, or on-chain data series that is not present in the current OHLCV MarketBar feed.',
  };
});

const definitionById = new Map(TRADINGVIEW_BUILTIN_DEFINITIONS.map((definition) => [definition.id, definition]));

export function tradingViewBuiltInDefinition(id: string): TradingViewBuiltInDefinition | undefined {
  return definitionById.get(id);
}

export function isTradingViewBuiltInId(id: string): id is TradingViewBuiltInId {
  return id.startsWith('tv-');
}

export function tradingViewBuiltInDefaultPeriod(id: string): number | null {
  return definitionById.get(id)?.defaultPeriod ?? null;
}

export function tradingViewBuiltInUsesSeparatePane(id: string): boolean {
  return definitionById.get(id)?.pane === 1;
}

export function tradingViewBuiltInPaneScale(id: string): { min: number; max: number; band?: { from: number; to: number; color: string }; levels: Array<{ value: number; lineStyle: 'dashed' | 'dotted' }> } | null {
  const name = definitionById.get(id)?.name;
  if (!name) return null;
  if (['Aroon Indicator', 'Stochastic (STOCH)', 'Stochastic Momentum Index (SMI)', 'Money Flow (MFI)', 'Connors RSI (CRSI)', 'Relative Volatility Index'].includes(name)) {
    return { min: 0, max: 100, band: { from: 20, to: 80, color: '#74c0fc' }, levels: [{ value: 20, lineStyle: 'dashed' }, { value: 50, lineStyle: 'dotted' }, { value: 80, lineStyle: 'dashed' }] };
  }
  if (['Williams %R (%R)'].includes(name)) {
    return { min: -100, max: 0, band: { from: -80, to: -20, color: '#74c0fc' }, levels: [{ value: -80, lineStyle: 'dashed' }, { value: -50, lineStyle: 'dotted' }, { value: -20, lineStyle: 'dashed' }] };
  }
  if (['Trend Strength Index', 'Correlation Coefficient (CC)'].includes(name)) {
    return { min: -1, max: 1, levels: [{ value: 0, lineStyle: 'dotted' }] };
  }
  return null;
}

const COMPARE_SERIES_INDICATORS = new Set(['Correlation Coefficient (CC)']);

/** Declared inputs of a built-in beyond its period, or null when it has only a period. */
export function tradingViewBuiltInInputs(id: string): TradingViewBuiltInInputs | null {
  const name = definitionById.get(id)?.name;
  return name === undefined ? null : BUILTIN_INPUTS[name] ?? null;
}

/** Whether the indicator groups bars into trading sessions, so it reads the chart instrument's session calendar. */
export function tradingViewBuiltInUsesSessions(id: string): boolean {
  const name = definitionById.get(id)?.name;
  return name !== undefined && SESSION_INDICATORS.has(name);
}

/** Whether the indicator reads a second symbol (`compareSymbol`), loaded like a compare symbol. */
export function tradingViewBuiltInUsesCompareSeries(id: string): boolean {
  const name = definitionById.get(id)?.name;
  return name !== undefined && COMPARE_SERIES_INDICATORS.has(name);
}

export function nums(bars: readonly MarketBar[], key: 'open' | 'high' | 'low' | 'close' | 'volume'): number[] {
  return bars.map((bar) => {
    const value = Number(bar[key]);
    return Number.isFinite(value) ? value : 0;
  });
}

export function full(length: number): MaybeNumber[] { return Array.from({ length }, () => null); }
function safePeriod(period: number, fallback = 14): number { return Number.isInteger(period) && period > 0 ? period : fallback; }
function sum(values: readonly number[]): number { return values.reduce((total, value) => total + value, 0); }
function mean(values: readonly number[]): number { return values.length ? sum(values) / values.length : 0; }
export function finite(value: MaybeNumber): value is number { return value !== null && Number.isFinite(value); }

export function sma(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  if (values.length < p) return result;
  let running = sum(values.slice(0, p));
  result[p - 1] = running / p;
  for (let i = p; i < values.length; i += 1) {
    running += values[i] - values[i - p];
    result[i] = running / p;
  }
  return result;
}

export function ema(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  if (values.length < p) return result;
  let current = mean(values.slice(0, p));
  result[p - 1] = current;
  const alpha = 2 / (p + 1);
  for (let i = p; i < values.length; i += 1) {
    current += alpha * (values[i] - current);
    result[i] = current;
  }
  return result;
}

export function rma(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  if (values.length < p) return result;
  let current = mean(values.slice(0, p));
  result[p - 1] = current;
  for (let i = p; i < values.length; i += 1) {
    current = (current * (p - 1) + values[i]) / p;
    result[i] = current;
  }
  return result;
}

function wma(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  const denominator = p * (p + 1) / 2;
  for (let i = p - 1; i < values.length; i += 1) {
    let weighted = 0;
    for (let j = 0; j < p; j += 1) weighted += values[i - p + 1 + j] * (j + 1);
    result[i] = weighted / denominator;
  }
  return result;
}

function rolling(values: readonly number[], period: number, reducer: (window: readonly number[]) => number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  for (let i = p - 1; i < values.length; i += 1) result[i] = reducer(values.slice(i - p + 1, i + 1));
  return result;
}

export function highest(values: readonly number[], period: number): MaybeNumber[] { return rolling(values, period, (window) => Math.max(...window)); }
export function lowest(values: readonly number[], period: number): MaybeNumber[] { return rolling(values, period, (window) => Math.min(...window)); }
function median(values: readonly number[], period: number): MaybeNumber[] {
  return rolling(values, period, (window) => {
    const sorted = [...window].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  });
}
function stdev(values: readonly number[], period: number): MaybeNumber[] {
  return rolling(values, period, (window) => {
    const m = mean(window);
    return Math.sqrt(mean(window.map((value) => (value - m) ** 2)));
  });
}
function rollingSum(values: readonly number[], period: number): MaybeNumber[] { return rolling(values, period, (window) => sum(window)); }

function trueRange(high: readonly number[], low: readonly number[], close: readonly number[]): number[] {
  return high.map((value, i) => i === 0 ? value - low[i] : Math.max(value - low[i], Math.abs(value - close[i - 1]), Math.abs(low[i] - close[i - 1])));
}

export function atr(high: readonly number[], low: readonly number[], close: readonly number[], period: number): MaybeNumber[] {
  return rma(trueRange(high, low, close), period);
}

function rsi(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const gains = values.map((value, i) => i === 0 ? 0 : Math.max(0, value - values[i - 1]));
  const losses = values.map((value, i) => i === 0 ? 0 : Math.max(0, values[i - 1] - value));
  const ag = rma(gains, p);
  const al = rma(losses, p);
  return values.map((_, i) => {
    if (!finite(ag[i]) || !finite(al[i])) return null;
    if (al[i] === 0) return 100;
    const rs = ag[i] / al[i];
    return 100 - 100 / (1 + rs);
  });
}

function roc(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  return values.map((value, i) => i < p || values[i - p] === 0 ? null : (value / values[i - p] - 1) * 100);
}

export function bollinger(values: readonly number[], period: number, deviations = 2): { middle: MaybeNumber[]; upper: MaybeNumber[]; lower: MaybeNumber[] } {
  const middle = sma(values, period);
  const dev = stdev(values, period);
  return {
    middle,
    upper: values.map((_, i) => finite(middle[i]) && finite(dev[i]) ? middle[i]! + dev[i]! * deviations : null),
    lower: values.map((_, i) => finite(middle[i]) && finite(dev[i]) ? middle[i]! - dev[i]! * deviations : null),
  };
}

function dmi(high: readonly number[], low: readonly number[], close: readonly number[], period: number): { plus: MaybeNumber[]; minus: MaybeNumber[]; adx: MaybeNumber[] } {
  const p = safePeriod(period);
  const tr = trueRange(high, low, close);
  const plusDM = high.map((value, i) => i === 0 ? 0 : Math.max(value - high[i - 1] > low[i - 1] - low[i] ? value - high[i - 1] : 0, 0));
  const minusDM = low.map((value, i) => i === 0 ? 0 : Math.max(low[i - 1] - value > high[i] - high[i - 1] ? low[i - 1] - value : 0, 0));
  const trSm = rma(tr, p);
  const plusSm = rma(plusDM, p);
  const minusSm = rma(minusDM, p);
  const plus = high.map((_, i) => finite(trSm[i]) && trSm[i]! !== 0 && finite(plusSm[i]) ? 100 * plusSm[i]! / trSm[i]! : null);
  const minus = high.map((_, i) => finite(trSm[i]) && trSm[i]! !== 0 && finite(minusSm[i]) ? 100 * minusSm[i]! / trSm[i]! : null);
  const dx = high.map((_, i) => finite(plus[i]) && finite(minus[i]) && plus[i]! + minus[i]! !== 0 ? 100 * Math.abs(plus[i]! - minus[i]!) / (plus[i]! + minus[i]!) : 0);
  const adx = rma(dx, p);
  return { plus, minus, adx };
}

function linearRegression(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  const xMean = (p - 1) / 2;
  const xVariance = Array.from({ length: p }, (_, i) => (i - xMean) ** 2).reduce((a, b) => a + b, 0);
  for (let i = p - 1; i < values.length; i += 1) {
    const window = values.slice(i - p + 1, i + 1);
    const yMean = mean(window);
    let covariance = 0;
    for (let j = 0; j < p; j += 1) covariance += (j - xMean) * (window[j] - yMean);
    const slope = xVariance === 0 ? 0 : covariance / xVariance;
    const intercept = yMean - slope * xMean;
    result[i] = intercept + slope * (p - 1);
  }
  return result;
}

function correlationWithIndex(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  const x = Array.from({ length: p }, (_, i) => i);
  const mx = mean(x);
  const vx = sum(x.map((value) => (value - mx) ** 2));
  for (let i = p - 1; i < values.length; i += 1) {
    const y = values.slice(i - p + 1, i + 1);
    const my = mean(y);
    const vy = sum(y.map((value) => (value - my) ** 2));
    if (vy <= Number.EPSILON || vx <= Number.EPSILON) result[i] = 0;
    else result[i] = sum(y.map((value, j) => (x[j] - mx) * (value - my))) / Math.sqrt(vx * vy);
  }
  return result;
}

function spearman(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  for (let i = p - 1; i < values.length; i += 1) {
    const window = values.slice(i - p + 1, i + 1);
    const sorted = window.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value);
    const rank = Array(p).fill(0) as number[];
    sorted.forEach((item, index) => { rank[item.index] = index + 1; });
    const d2 = sum(rank.map((value, index) => (value - (index + 1)) ** 2));
    result[i] = p <= 1 ? 0 : (1 - 6 * d2 / (p * (p * p - 1))) * 100;
  }
  return result;
}

function output(
  id: string,
  suffix: string,
  title: string,
  pane: 0 | 1,
  kind: 'line' | 'histogram',
  values: readonly MaybeNumber[],
  bars: readonly MarketBar[],
  color?: string,
): TradingViewBuiltInOutput {
  return {
    key: `${id}:${suffix}`,
    title,
    pane,
    kind,
    points: values.flatMap((value, index) => finite(value) && bars[index]?.start_time ? [{ time: bars[index].start_time, value }] : []),
    color,
    labelsOnPriceScale: false,
  };
}

function fixedLine(id: string, suffix: string, title: string, values: readonly MaybeNumber[], bars: readonly MarketBar[], color?: string): TradingViewBuiltInOutput {
  return output(id, suffix, title, 0, 'line', values, bars, color);
}

function alma(values: readonly number[], period: number, offset = 0.85, sigma = 6): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  const m = offset * (p - 1);
  const s = p / sigma;
  const weights = Array.from({ length: p }, (_, i) => Math.exp(-((i - m) ** 2) / (2 * s * s)));
  const denominator = sum(weights);
  for (let i = p - 1; i < values.length; i += 1) {
    let total = 0;
    for (let j = 0; j < p; j += 1) total += values[i - p + 1 + j] * weights[j];
    result[i] = total / denominator;
  }
  return result;
}

function hull(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const half = wma(values, Math.max(1, Math.round(p / 2)));
  const whole = wma(values, p);
  const diff = values.map((_, i) => finite(half[i]) && finite(whole[i]) ? 2 * half[i]! - whole[i]! : 0);
  return wma(diff, Math.max(1, Math.round(Math.sqrt(p))));
}

function kama(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const result = full(values.length);
  if (values.length <= p) return result;
  let current = mean(values.slice(0, p));
  result[p - 1] = current;
  const fast = 2 / 3;
  const slow = 2 / 31;
  for (let i = p; i < values.length; i += 1) {
    const change = Math.abs(values[i] - values[i - p]);
    let volatility = 0;
    for (let j = i - p + 1; j <= i; j += 1) volatility += Math.abs(values[j] - values[j - 1]);
    const er = volatility === 0 ? 0 : change / volatility;
    const sc = (er * (fast - slow) + slow) ** 2;
    current += sc * (values[i] - current);
    result[i] = current;
  }
  return result;
}

function parabolicSar(high: readonly number[], low: readonly number[]): MaybeNumber[] {
  const result = full(high.length);
  if (high.length < 2) return result;
  let up = true;
  let af = 0.02;
  let ep = high[0];
  let sar = low[0];
  result[0] = sar;
  for (let i = 1; i < high.length; i += 1) {
    sar += af * (ep - sar);
    if (up) {
      sar = Math.min(sar, low[i - 1], i > 1 ? low[i - 2] : low[i - 1]);
      if (low[i] < sar) {
        up = false; sar = ep; ep = low[i]; af = 0.02;
      } else if (high[i] > ep) { ep = high[i]; af = Math.min(0.2, af + 0.02); }
    } else {
      sar = Math.max(sar, high[i - 1], i > 1 ? high[i - 2] : high[i - 1]);
      if (high[i] > sar) {
        up = true; sar = ep; ep = high[i]; af = 0.02;
      } else if (low[i] < ep) { ep = low[i]; af = Math.min(0.2, af + 0.02); }
    }
    result[i] = sar;
  }
  return result;
}

function supertrend(high: readonly number[], low: readonly number[], close: readonly number[], period: number, factor = 3): { line: MaybeNumber[]; direction: MaybeNumber[] } {
  const a = atr(high, low, close, period);
  const line = full(close.length);
  const direction = full(close.length);
  let upper = 0;
  let lower = 0;
  let trend = 1;
  for (let i = 0; i < close.length; i += 1) {
    if (!finite(a[i])) continue;
    const mid = (high[i] + low[i]) / 2;
    const basicUpper = mid + factor * a[i]!;
    const basicLower = mid - factor * a[i]!;
    upper = i === 0 || close[i - 1] > upper ? basicUpper : Math.min(basicUpper, upper);
    lower = i === 0 || close[i - 1] < lower ? basicLower : Math.max(basicLower, lower);
    if (trend > 0 && close[i] < lower) trend = -1;
    else if (trend < 0 && close[i] > upper) trend = 1;
    direction[i] = trend;
    line[i] = trend > 0 ? lower : upper;
  }
  return { line, direction };
}

function tripleEma(values: readonly number[], period: number): MaybeNumber[] {
  const e1 = ema(values, period);
  const e1Numeric = e1.map((value) => finite(value) ? value : values[0] ?? 0);
  const e2 = ema(e1Numeric, period);
  const e2Numeric = e2.map((value, i) => finite(value) ? value : e1Numeric[i]);
  const e3 = ema(e2Numeric, period);
  return values.map((_, i) => finite(e1[i]) && finite(e2[i]) && finite(e3[i]) ? 3 * e1[i]! - 3 * e2[i]! + e3[i]! : null);
}

function stochastic(high: readonly number[], low: readonly number[], close: readonly number[], period: number): MaybeNumber[] {
  const hh = highest(high, period);
  const ll = lowest(low, period);
  return close.map((value, i) => finite(hh[i]) && finite(ll[i]) && hh[i] !== ll[i] ? 100 * (value - ll[i]!) / (hh[i]! - ll[i]!) : null);
}

function moneyFlowIndex(high: readonly number[], low: readonly number[], close: readonly number[], volume: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const typical = close.map((value, i) => (high[i] + low[i] + value) / 3);
  const pos = typical.map((value, i) => i > 0 && value > typical[i - 1] ? value * volume[i] : 0);
  const neg = typical.map((value, i) => i > 0 && value < typical[i - 1] ? value * volume[i] : 0);
  const ps = rollingSum(pos, p);
  const ns = rollingSum(neg, p);
  return close.map((_, i) => {
    if (!finite(ps[i]) || !finite(ns[i])) return null;
    if (ns[i] === 0) return 100;
    return 100 - 100 / (1 + ps[i]! / ns[i]!);
  });
}

function stochasticMomentum(high: readonly number[], low: readonly number[], close: readonly number[], period: number): MaybeNumber[] {
  const hh = highest(high, period);
  const ll = lowest(low, period);
  const midpointDelta = close.map((value, i) => finite(hh[i]) && finite(ll[i]) ? value - (hh[i]! + ll[i]!) / 2 : 0);
  const range = close.map((_, i) => finite(hh[i]) && finite(ll[i]) ? hh[i]! - ll[i]! : 0);
  const num1 = ema(midpointDelta, 3).map((v) => finite(v) ? v : 0);
  const num2 = ema(num1, 3);
  const den1 = ema(range, 3).map((v) => finite(v) ? v : 0);
  const den2 = ema(den1, 3);
  return close.map((_, i) => finite(num2[i]) && finite(den2[i]) && den2[i] !== 0 ? 200 * num2[i]! / den2[i]! : null);
}

function cci(high: readonly number[], low: readonly number[], close: readonly number[], period: number): MaybeNumber[] {
  const typical = close.map((value, i) => (high[i] + low[i] + value) / 3);
  const basis = sma(typical, period);
  return close.map((_, i) => {
    if (!finite(basis[i])) return null;
    const start = i - safePeriod(period) + 1;
    const window = typical.slice(start, i + 1);
    const md = mean(window.map((value) => Math.abs(value - basis[i]!)));
    return md === 0 ? 0 : (typical[i] - basis[i]!) / (0.015 * md);
  });
}

function percentileRank(values: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  return values.map((value, i) => {
    if (i < p - 1) return null;
    const window = values.slice(i - p + 1, i + 1);
    return 100 * window.filter((item) => item < value).length / Math.max(1, p - 1);
  });
}

function connorsRsi(close: readonly number[]): MaybeNumber[] {
  const priceRsi = rsi(close, 3);
  const streak: number[] = [0];
  for (let i = 1; i < close.length; i += 1) {
    const direction = Math.sign(close[i] - close[i - 1]);
    const previousDirection = Math.sign(streak[i - 1]);
    streak.push(direction === 0 ? 0 : direction === previousDirection ? streak[i - 1] + direction : direction);
  }
  const streakRsi = rsi(streak, 2);
  const oneBarRoc = close.map((value, i) => i === 0 || close[i - 1] === 0 ? 0 : (value / close[i - 1] - 1) * 100);
  const rank = percentileRank(oneBarRoc, 100);
  return close.map((_, i) => finite(priceRsi[i]) && finite(streakRsi[i]) && finite(rank[i]) ? (priceRsi[i]! + streakRsi[i]! + rank[i]!) / 3 : null);
}

function forceIndex(close: readonly number[], volume: readonly number[], period: number): MaybeNumber[] {
  const raw = close.map((value, i) => i === 0 ? 0 : (value - close[i - 1]) * volume[i]);
  return ema(raw, period);
}

function relativeVigor(open: readonly number[], high: readonly number[], low: readonly number[], close: readonly number[], period: number): { rvi: MaybeNumber[]; signal: MaybeNumber[] } {
  const numerator = close.map((value, i) => value - open[i]);
  const denominator = high.map((value, i) => Math.max(Number.EPSILON, value - low[i]));
  const ns = sma(numerator, period);
  const ds = sma(denominator, period);
  const rvi = close.map((_, i) => finite(ns[i]) && finite(ds[i]) && ds[i] !== 0 ? ns[i]! / ds[i]! * 100 : null);
  const rvNumeric = rvi.map((value) => finite(value) ? value : 0);
  return { rvi, signal: sma(rvNumeric, 4) };
}

function relativeVolatility(close: readonly number[], period: number): MaybeNumber[] {
  const p = safePeriod(period);
  const sd = stdev(close, p).map((value) => finite(value) ? value : 0);
  const up = sd.map((value, i) => i > 0 && close[i] > close[i - 1] ? value : 0);
  const down = sd.map((value, i) => i > 0 && close[i] < close[i - 1] ? value : 0);
  const u = rma(up, p);
  const d = rma(down, p);
  return close.map((_, i) => finite(u[i]) && finite(d[i]) && u[i]! + d[i]! !== 0 ? 100 * u[i]! / (u[i]! + d[i]!) : null);
}

function trueStrength(close: readonly number[], longPeriod = 25, shortPeriod = 13): MaybeNumber[] {
  const momentum = close.map((value, i) => i === 0 ? 0 : value - close[i - 1]);
  const absMomentum = momentum.map(Math.abs);
  const first = ema(momentum, longPeriod).map((v) => finite(v) ? v : 0);
  const second = ema(first, shortPeriod);
  const absFirst = ema(absMomentum, longPeriod).map((v) => finite(v) ? v : 0);
  const absSecond = ema(absFirst, shortPeriod);
  return close.map((_, i) => finite(second[i]) && finite(absSecond[i]) && absSecond[i] !== 0 ? 100 * second[i]! / absSecond[i]! : null);
}

function ultimateOscillator(high: readonly number[], low: readonly number[], close: readonly number[]): MaybeNumber[] {
  const bp = close.map((value, i) => i === 0 ? value - low[i] : value - Math.min(low[i], close[i - 1]));
  const tr = close.map((value, i) => i === 0 ? high[i] - low[i] : Math.max(high[i], close[i - 1]) - Math.min(low[i], close[i - 1]));
  const avg = (period: number) => {
    const bs = rollingSum(bp, period);
    const ts = rollingSum(tr, period);
    return close.map((_, i) => finite(bs[i]) && finite(ts[i]) && ts[i] !== 0 ? bs[i]! / ts[i]! : null);
  };
  const a7 = avg(7); const a14 = avg(14); const a28 = avg(28);
  return close.map((_, i) => finite(a7[i]) && finite(a14[i]) && finite(a28[i]) ? 100 * (4 * a7[i]! + 2 * a14[i]! + a28[i]!) / 7 : null);
}

function vortex(high: readonly number[], low: readonly number[], close: readonly number[], period: number): { plus: MaybeNumber[]; minus: MaybeNumber[] } {
  const vmPlus = high.map((value, i) => i === 0 ? 0 : Math.abs(value - low[i - 1]));
  const vmMinus = low.map((value, i) => i === 0 ? 0 : Math.abs(value - high[i - 1]));
  const tr = trueRange(high, low, close);
  const plusSum = rollingSum(vmPlus, period); const minusSum = rollingSum(vmMinus, period); const trSum = rollingSum(tr, period);
  return {
    plus: high.map((_, i) => finite(plusSum[i]) && finite(trSum[i]) && trSum[i] !== 0 ? plusSum[i]! / trSum[i]! : null),
    minus: high.map((_, i) => finite(minusSum[i]) && finite(trSum[i]) && trSum[i] !== 0 ? minusSum[i]! / trSum[i]! : null),
  };
}

function volumeWeightedMa(close: readonly number[], volume: readonly number[], period: number): MaybeNumber[] {
  const pv = close.map((value, i) => value * volume[i]);
  const pvs = rollingSum(pv, period); const vs = rollingSum(volume, period);
  return close.map((_, i) => finite(pvs[i]) && finite(vs[i]) && vs[i] !== 0 ? pvs[i]! / vs[i]! : null);
}

function sessionTwap(bars: readonly MarketBar[], typical: readonly number[], session: TradingSessionSpec): MaybeNumber[] {
  const result = full(bars.length);
  const { day } = sessionClock(bars, bars.map((bar) => Date.parse(bar.start_time)), session);
  let running = 0;
  let count = 0;
  bars.forEach((_, i) => {
    if (i === 0 || day[i] !== day[i - 1]) { running = 0; count = 0; }
    running += typical[i]; count += 1; result[i] = running / count;
  });
  return result;
}

function technicalRating(high: readonly number[], low: readonly number[], close: readonly number[], volume: readonly number[]): MaybeNumber[] {
  const r = rsi(close, 14);
  const st = stochastic(high, low, close, 14);
  const c = cci(high, low, close, 20);
  const mom = roc(close, 10);
  const ma20 = sma(close, 20); const ma50 = sma(close, 50);
  const d = dmi(high, low, close, 14);
  return close.map((value, i) => {
    const signals: number[] = [];
    if (finite(r[i])) signals.push(r[i]! > 55 ? 1 : r[i]! < 45 ? -1 : 0);
    if (finite(st[i])) signals.push(st[i]! > 60 ? 1 : st[i]! < 40 ? -1 : 0);
    if (finite(c[i])) signals.push(c[i]! > 50 ? 1 : c[i]! < -50 ? -1 : 0);
    if (finite(mom[i])) signals.push(Math.sign(mom[i]!));
    if (finite(ma20[i])) signals.push(value > ma20[i]! ? 1 : -1);
    if (finite(ma50[i])) signals.push(value > ma50[i]! ? 1 : -1);
    if (finite(d.adx[i]) && finite(d.plus[i]) && finite(d.minus[i]) && d.adx[i]! > 20) signals.push(d.plus[i]! > d.minus[i]! ? 1 : -1);
    return signals.length ? mean(signals) : null;
  });
}

// TVP-6.1 helpers. Times are epoch milliseconds of bar starts; sessions come from `tradingSessions.ts`.
const DAY_MS = 86_400_000;

export function startTimes(bars: readonly MarketBar[]): number[] { return bars.map((bar) => Date.parse(bar.start_time)); }

/** A built-in's `params` value: a finite number at least `min` (whole when `integer`), else the default. */
function numberParam(instance: TradingViewBuiltInInstance, key: string, fallback: number, min: number, integer: boolean): number {
  const value = instance.params?.[key];
  return typeof value === 'number' && Number.isFinite(value) && value >= min && (!integer || Number.isInteger(value)) ? value : fallback;
}

function selectParam<T extends string>(instance: TradingViewBuiltInInstance, key: string, fallback: T, options: readonly T[]): T {
  const value = instance.params?.[key];
  return typeof value === 'string' && (options as readonly string[]).includes(value) ? value as T : fallback;
}

/** Reads every param of a built-in from its declared inputs, falling back to the declared defaults. */
export function builtInParams(name: string, instance: TradingViewBuiltInInstance): Record<string, ParamValue> {
  const values: Record<string, ParamValue> = {};
  for (const spec of BUILTIN_INPUTS[name]?.params ?? []) {
    values[spec.key] = spec.kind === 'number'
      ? numberParam(instance, spec.key, spec.default, spec.min, spec.integer === true)
      : selectParam(instance, spec.key, spec.default, spec.options.map((option) => option.value));
  }
  return values;
}

type PivotLevels = { pp: MaybeNumber[]; r1: MaybeNumber[]; r2: MaybeNumber[]; r3: MaybeNumber[]; s1: MaybeNumber[]; s2: MaybeNumber[]; s3: MaybeNumber[] };
type Hlc = [number, number, number];

/** Classic floor pivots of a period's high H, low L and close C. */
function setPivots(levels: PivotLevels, i: number, [h, l, c]: Hlc): void {
  const pp = (h + l + c) / 3;
  levels.pp[i] = pp; levels.r1[i] = 2 * pp - l; levels.s1[i] = 2 * pp - h;
  levels.r2[i] = pp + (h - l); levels.s2[i] = pp - (h - l);
  levels.r3[i] = h + 2 * (pp - l); levels.s3[i] = l - 2 * (h - pp);
}

/**
 * Period pivots: PP = (H + L + C) / 3, R1 = 2PP - L, S1 = 2PP - H, R2 = PP + (H - L), S2 = PP - (H - L), R3 = H + 2(PP - L), S3 = L - 2(H - PP)
 * of each period's bars (regular-hours bars only where the session asks for that). `developing` false: a bar gets the pivots of the
 * last completed period (Intraday Pivot Points). `developing` true: the pivots of its own period so far, the levels the next period
 * will use (Ziv Ghost Pivots).
 */
function periodPivots(keys: readonly number[], counts: readonly boolean[], high: readonly number[], low: readonly number[], close: readonly number[], developing: boolean): PivotLevels {
  const levels: PivotLevels = { pp: full(close.length), r1: full(close.length), r2: full(close.length), r3: full(close.length), s1: full(close.length), s2: full(close.length), s3: full(close.length) };
  let key = 0; let current: Hlc | null = null; let previous: Hlc | null = null;
  for (let i = 0; i < close.length; i += 1) {
    if (i === 0 || keys[i] !== key) {
      if (current) previous = current;
      key = keys[i]; current = null;
    }
    if (counts[i]) current = current ? [Math.max(current[0], high[i]), Math.min(current[1], low[i]), close[i]] : [high[i], low[i], close[i]];
    const source = developing ? current : previous;
    if (source) setPivots(levels, i, source);
  }
  return levels;
}

/**
 * Missed pivots: each period's pivot PP = (H + L + C) / 3 of the period before it. A pivot that no bar's high-low range has touched
 * since its period began, once that period is over, is "missed" and stays a target until price touches it. Only the pivots of the
 * last `periodsBack` periods count. Returns the nearest missed pivot above and below each close.
 */
function missedPivots(keys: readonly number[], counts: readonly boolean[], high: readonly number[], low: readonly number[], close: readonly number[], periodsBack: number): { above: MaybeNumber[]; below: MaybeNumber[] } {
  const above = full(close.length); const below = full(close.length);
  let open: Array<{ value: number; period: number }> = [];
  let key = 0; let period = -1; let current: Hlc | null = null;
  for (let i = 0; i < close.length; i += 1) {
    if (period < 0 || keys[i] !== key) {
      if (current) open.push({ value: (current[0] + current[1] + current[2]) / 3, period: period + 1 });
      period += 1; key = keys[i]; current = null;
    }
    if (counts[i]) current = current ? [Math.max(current[0], high[i]), Math.min(current[1], low[i]), close[i]] : [high[i], low[i], close[i]];
    open = open.filter((level) => level.period >= period - periodsBack && !(low[i] <= level.value && level.value <= high[i]));
    let up: MaybeNumber = null; let down: MaybeNumber = null;
    for (const level of open) {
      if (level.period >= period) continue;
      if (level.value > close[i] && (up === null || level.value < up)) up = level.value;
      if (level.value < close[i] && (down === null || level.value > down)) down = level.value;
    }
    above[i] = up; below[i] = down;
  }
  return { above, below };
}

/**
 * Relative Volume at Time: the volume since the start of the anchor period (cumulative mode) or the bar's own volume (regular mode),
 * divided by the average of the same value at the same time since the start of the period over the last `length` earlier periods
 * that have a bar at that time. No value until that many periods exist, or when their average is zero.
 */
function relativeVolumeAtTime(keys: readonly number[], since: readonly number[], volume: readonly number[], length: number, cumulative: boolean): MaybeNumber[] {
  const result = full(volume.length);
  const history = new Map<number, number[]>();
  let key = 0; let running = 0;
  for (let i = 0; i < volume.length; i += 1) {
    if (i === 0 || keys[i] !== key) { key = keys[i]; running = 0; }
    running += volume[i];
    const value = cumulative ? running : volume[i];
    const past = history.get(since[i]) ?? [];
    if (past.length >= length) {
      let total = 0;
      for (let k = past.length - length; k < past.length; k += 1) total += past[k];
      const average = total / length;
      if (average !== 0) result[i] = value / average;
    }
    past.push(value);
    history.set(since[i], past);
  }
  return result;
}

const PRICE_SOURCES = ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4'] as const;
type PriceSourceName = typeof PRICE_SOURCES[number];

function priceSource(source: PriceSourceName, open: readonly number[], high: readonly number[], low: readonly number[], close: readonly number[]): number[] {
  return close.map((value, i) => {
    if (source === 'open') return open[i];
    if (source === 'high') return high[i];
    if (source === 'low') return low[i];
    if (source === 'hl2') return (high[i] + low[i]) / 2;
    if (source === 'hlc3') return (high[i] + low[i] + value) / 3;
    if (source === 'ohlc4') return (open[i] + high[i] + low[i] + value) / 4;
    return value;
  });
}

/**
 * 24-hour volume in quote currency: the sum of volume × price (the price source, close by default) over the bars that started in the
 * 24 hours up to and including this bar's start (start > this start - 24h). Shown once the loaded bars cover a whole 24 hours: the first
 * bar started at least 24h minus one bar interval (the smallest gap between bar starts) earlier. Bars longer than a day show nothing.
 */
function rollingDayVolume(times: readonly number[], volume: readonly number[], price: readonly number[]): MaybeNumber[] {
  const result = full(volume.length);
  let step = Infinity;
  for (let i = 1; i < times.length; i += 1) {
    const gap = times[i] - times[i - 1];
    if (gap > 0 && gap < step) step = gap;
  }
  if (step > DAY_MS && step !== Infinity) return result;
  const covered = Math.min(step, DAY_MS);
  const traded = volume.map((value, i) => value * price[i]);
  let left = 0; let running = 0;
  for (let i = 0; i < volume.length; i += 1) {
    running += traded[i];
    while (left < i && times[left] <= times[i] - DAY_MS) { running -= traded[left]; left += 1; }
    if (times[0] <= times[i] - DAY_MS + covered) result[i] = running;
  }
  return result;
}

/** The compare series' close at each bar: the close of its latest bar starting at or before the bar's start (gaps carry the last close forward). */
function alignedCompareCloses(times: readonly number[], compareBars: readonly MarketBar[]): MaybeNumber[] {
  const series = compareBars
    .map((bar) => { const value = Number(bar.close); return { time: Date.parse(bar.start_time), close: Number.isFinite(value) ? value : 0 }; })
    .sort((a, b) => a.time - b.time);
  let j = -1;
  return times.map((time) => {
    while (j + 1 < series.length && series[j + 1].time <= time) j += 1;
    return j >= 0 ? series[j].close : null;
  });
}

/** Pearson correlation of x and y over the last `period` bars: sum(dx·dy) / sqrt(sum(dx²)·sum(dy²)) around the window means; none if either is flat. */
function pearson(x: readonly number[], y: readonly MaybeNumber[], period: number): MaybeNumber[] {
  const result = full(x.length);
  for (let i = period - 1; i < x.length; i += 1) {
    let complete = true;
    let sx = 0; let sy = 0;
    for (let j = i - period + 1; j <= i; j += 1) {
      const yj = y[j];
      if (yj === null) { complete = false; break; }
      sx += x[j]; sy += yj;
    }
    if (!complete) continue;
    const mx = sx / period; const my = sy / period;
    let sxx = 0; let syy = 0; let sxy = 0;
    for (let j = i - period + 1; j <= i; j += 1) {
      const dx = x[j] - mx; const dy = (y[j] as number) - my;
      sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    result[i] = sxx === 0 || syy === 0 ? null : sxy / Math.sqrt(sxx * syy);
  }
  return result;
}

/**
 * Knoxville Divergence: a bearish signal at a bar whose high exceeds the highest high of the previous `lookback` bars while
 * Momentum(m) = close - close[m] is lower than at that earlier high, with RSI(r) above 70 at some bar from the earlier high to now;
 * bullish mirrors it with lows, higher momentum and RSI below 30. The signal plots at the bar's high (bearish) or low (bullish).
 */
function knoxvilleDivergence(high: readonly number[], low: readonly number[], close: readonly number[], lookback: number, momentumLength: number, rsiLength: number): { bearish: MaybeNumber[]; bullish: MaybeNumber[] } {
  const bearish = full(close.length); const bullish = full(close.length);
  const momentum = close.map((value, i) => i >= momentumLength ? value - close[i - momentumLength] : null);
  const strength = rsi(close, rsiLength);
  const overbought: number[] = []; const oversold: number[] = [];
  strength.forEach((value, i) => {
    overbought.push((i > 0 ? overbought[i - 1] : 0) + (finite(value) && value > 70 ? 1 : 0));
    oversold.push((i > 0 ? oversold[i - 1] : 0) + (finite(value) && value < 30 ? 1 : 0));
  });
  const flagged = (counts: readonly number[], from: number, to: number) => counts[to] - (from > 0 ? counts[from - 1] : 0) > 0;
  // Monotonic queues of the previous `lookback` bars: the front is the latest index of the highest high (lowest low).
  const highs: number[] = []; const lows: number[] = [];
  for (let i = 1; i < close.length; i += 1) {
    while (highs.length && high[highs[highs.length - 1]] <= high[i - 1]) highs.pop();
    highs.push(i - 1);
    while (lows.length && low[lows[lows.length - 1]] >= low[i - 1]) lows.pop();
    lows.push(i - 1);
    while (highs[0] < i - lookback) highs.shift();
    while (lows[0] < i - lookback) lows.shift();
    const now = momentum[i];
    if (now === null) continue;
    const h = highs[0]; const l = lows[0];
    const atHigh = momentum[h]; const atLow = momentum[l];
    if (high[i] > high[h] && atHigh !== null && now < atHigh && flagged(overbought, h, i)) bearish[i] = high[i];
    if (low[i] < low[l] && atLow !== null && now > atLow && flagged(oversold, l, i)) bullish[i] = low[i];
  }
  return { bearish, bullish };
}

/** Mean of the last `length` values, only where all of them are finite (the Stochastic's slowing). */
function completeSma(values: readonly MaybeNumber[], length: number): MaybeNumber[] {
  return values.map((_, i) => {
    if (i < length - 1) return null;
    let total = 0;
    for (let j = i - length + 1; j <= i; j += 1) {
      const value = values[j];
      if (!finite(value)) return null;
      total += value;
    }
    return total / length;
  });
}

/**
 * Reversal: MACD line = EMA(close, fast) - EMA(close, slow); slow %K = SMA(raw Stochastic %K(K period), slowing). Bearish when the MACD
 * line crosses zero from above (previous > 0, now <= 0) while slow %K is above the upper level; bullish when it crosses from below
 * (previous < 0, now >= 0) while slow %K is below the lower level.
 */
function bookerReversal(high: readonly number[], low: readonly number[], close: readonly number[], kPeriod: number, p: Record<string, ParamValue>): { bullish: MaybeNumber[]; bearish: MaybeNumber[] } {
  const fast = ema(close, p.fastLength as number); const slow = ema(close, p.slowLength as number);
  const k = completeSma(stochastic(high, low, close, kPeriod), p.slowing as number);
  const macd = close.map((_, i) => finite(fast[i]) && finite(slow[i]) ? fast[i]! - slow[i]! : null);
  const bullish = full(close.length); const bearish = full(close.length);
  for (let i = 1; i < close.length; i += 1) {
    const now = macd[i]; const before = macd[i - 1]; const stoch = k[i];
    if (now === null || before === null || !finite(stoch)) continue;
    if (before > 0 && now <= 0 && stoch > (p.upper as number)) bearish[i] = high[i];
    if (before < 0 && now >= 0 && stoch < (p.lower as number)) bullish[i] = low[i];
  }
  return { bullish, bearish };
}

/**
 * ADX Breakout: while ADX(adx length) is below the level the market is consolidating, and the box is the highest high and lowest low of
 * the previous `lookback` bars (not counting this one). A close above (below) the box while it is drawn is a breakout up (down).
 */
function adxBreakout(high: readonly number[], low: readonly number[], close: readonly number[], lookback: number, adxLength: number, level: number): { upper: MaybeNumber[]; lower: MaybeNumber[]; up: MaybeNumber[]; down: MaybeNumber[] } {
  const { adx } = dmi(high, low, close, adxLength);
  const upper = full(close.length); const lower = full(close.length); const up = full(close.length); const down = full(close.length);
  for (let i = lookback; i < close.length; i += 1) {
    const strength = adx[i];
    if (!finite(strength) || !(strength < level)) continue;
    let top = high[i - lookback]; let bottom = low[i - lookback];
    for (let j = i - lookback + 1; j < i; j += 1) { top = Math.max(top, high[j]); bottom = Math.min(bottom, low[j]); }
    upper[i] = top; lower[i] = bottom;
    if (close[i] > top) up[i] = close[i];
    if (close[i] < bottom) down[i] = close[i];
  }
  return { upper, lower, up, down };
}

const PIVOT_LINES: ReadonlyArray<[keyof PivotLevels, string, string | undefined]> = [
  ['pp', 'Pivot', undefined], ['r1', 'R1', '#f23645'], ['r2', 'R2', '#f23645'], ['r3', 'R3', '#f23645'], ['s1', 'S1', '#20c997'], ['s2', 'S2', '#20c997'], ['s3', 'S3', '#20c997'],
];
const GHOST_LINES: ReadonlyArray<[keyof PivotLevels, string, string | undefined]> = [['pp', 'Ghost Pivot', undefined], ['r1', 'Ghost R1', '#f23645'], ['s1', 'Ghost S1', '#20c997']];

function levelLine(id: string, suffix: string, title: string, values: readonly MaybeNumber[], bars: readonly MarketBar[], color?: string): TradingViewBuiltInOutput {
  return { ...fixedLine(id, suffix, title, values, bars, color), render: 'levels' };
}

function markerLine(id: string, suffix: string, title: string, values: readonly MaybeNumber[], bars: readonly MarketBar[], color: string, marker: 'arrowUp' | 'arrowDown' | 'circle'): TradingViewBuiltInOutput {
  return { ...fixedLine(id, suffix, title, values, bars, color), render: 'markers', marker };
}

/** TVP-6.1 indicators, built from public descriptions of the TradingView indicators; the helpers above state each definition. */
function quickWinOutputs(
  name: string,
  id: string,
  period: number,
  instance: TradingViewBuiltInInstance,
  series: TradingViewBuiltInSeries,
  bars: readonly MarketBar[],
): TradingViewBuiltInOutput[] | null {
  if (!BUILTIN_INPUTS[name] || !QUICK_WINS.has(name)) return null;
  const open = nums(bars, 'open'); const high = nums(bars, 'high'); const low = nums(bars, 'low'); const close = nums(bars, 'close'); const volume = nums(bars, 'volume');
  const p = builtInParams(name, instance);
  const session = instance.session ?? UTC_SESSION;
  const clock = () => sessionClock(bars, startTimes(bars), session);
  if (name === '24-hour Volume') {
    const price = priceSource(p.source as PriceSourceName, open, high, low, close);
    return [output(id, 'volume-24h', '24h Volume', 1, 'histogram', rollingDayVolume(startTimes(bars), volume, price), bars)];
  }
  if (name === 'Correlation Coefficient (CC)') {
    // The second series counts only while the instance names its symbol, like the server registry.
    const compareBars = instance.compareSymbol ? series.compareBars : undefined;
    const compare = compareBars?.length ? alignedCompareCloses(startTimes(bars), compareBars) : full(close.length);
    return [output(id, 'cc', `CC ${period}`, 1, 'line', pearson(close, compare, period), bars)];
  }
  if (name === 'Relative Volume at Time') {
    const periods = sessionPeriods(clock(), p.anchor as 'D' | 'W' | 'M', session);
    return [output(id, 'rvol-at-time', `RVol at Time ${period}`, 1, 'histogram', relativeVolumeAtTime(periods.key, periods.since, volume, period, p.mode === 'cumulative'), bars)];
  }
  if (name === 'Rob Booker - ADX Breakout') {
    const box = adxBreakout(high, low, close, period, p.adxLength as number, p.level as number);
    return [
      levelLine(id, 'upper', 'Box Upper', box.upper, bars, '#20c997'), levelLine(id, 'lower', 'Box Lower', box.lower, bars, '#f23645'),
      markerLine(id, 'breakout-up', 'Breakout Up', box.up, bars, '#20c997', 'arrowUp'), markerLine(id, 'breakout-down', 'Breakout Down', box.down, bars, '#f23645', 'arrowDown'),
    ];
  }
  if (name === 'Rob Booker - Knoxville Divergence') {
    const values = knoxvilleDivergence(high, low, close, period, p.momentumLength as number, p.rsiLength as number);
    return [markerLine(id, 'bearish', 'Bearish Divergence', values.bearish, bars, '#f23645', 'arrowDown'), markerLine(id, 'bullish', 'Bullish Divergence', values.bullish, bars, '#20c997', 'arrowUp')];
  }
  if (name === 'Rob Booker Intraday Pivot Points' || name === 'Rob Booker Ziv Ghost Pivots') {
    const ghost = name === 'Rob Booker Ziv Ghost Pivots';
    const pivotPeriod: SessionPeriod = ghost ? ghostPeriod(p.pivotPeriod as string) : period;
    const sessionClockValues = clock();
    const { key } = sessionPeriods(sessionClockValues, pivotPeriod, session);
    const levels = periodPivots(key, sessionClockValues.counts, high, low, close, ghost);
    return (ghost ? GHOST_LINES : PIVOT_LINES).map(([level, title, color]) => levelLine(id, level, title, levels[level], bars, color));
  }
  if (name === 'Rob Booker Missed Pivot Points') {
    const sessionClockValues = clock();
    const { key } = sessionPeriods(sessionClockValues, p.pivotPeriod as 'D' | 'W' | 'M', session);
    const levels = missedPivots(key, sessionClockValues.counts, high, low, close, period);
    return [levelLine(id, 'missed-above', 'Missed Pivot Above', levels.above, bars, '#f23645'), levelLine(id, 'missed-below', 'Missed Pivot Below', levels.below, bars, '#20c997')];
  }
  if (name === 'Rob Booker Reversal') {
    const values = bookerReversal(high, low, close, period, p);
    return [markerLine(id, 'bullish', 'Bullish Reversal', values.bullish, bars, '#20c997', 'arrowUp'), markerLine(id, 'bearish', 'Bearish Reversal', values.bearish, bars, '#f23645', 'arrowDown')];
  }
  return null;
}

/** Ziv Ghost Pivots periods: 4 and 8 hours, a week, a month. */
function ghostPeriod(value: string): SessionPeriod {
  return value === '240' ? 4 : value === '480' ? 8 : value === 'M' ? 'M' : 'W';
}

export function calculateTradingViewBuiltInOutputs(
  bars: readonly MarketBar[],
  instance: TradingViewBuiltInInstance, series: TradingViewBuiltInSeries = {}, // series: the compare symbol's bars, if any
): TradingViewBuiltInOutput[] {
  const definition = definitionById.get(instance.id);
  if (!definition?.available || !isTradingViewBuiltInId(instance.id) || bars.length === 0) return [];
  const name = definition.name; const period = safePeriod(instance.period, definition.defaultPeriod);
  if (DRAWING_INDICATORS.has(name)) return drawingIndicatorOutputs(name, instance.id, period, builtInParams(name, instance), bars, instance.session ?? UTC_SESSION) ?? [];
  const open = nums(bars, 'open'); const high = nums(bars, 'high'); const low = nums(bars, 'low'); const close = nums(bars, 'close'); const volume = nums(bars, 'volume');
  const hl2 = close.map((_, i) => (high[i] + low[i]) / 2);
  const typical = close.map((value, i) => (high[i] + low[i] + value) / 3);
  const id = instance.id;

  if (name === 'Accumulation Distribution (ADL)') {
    let cumulative = 0;
    const values = close.map((_, i) => {
      const range = high[i] - low[i];
      const multiplier = range === 0 ? 0 : ((close[i] - low[i]) - (high[i] - close[i])) / range;
      cumulative += multiplier * volume[i];
      return cumulative;
    });
    return [output(id, 'adl', 'ADL', 1, 'line', values, bars)];
  }
  if (name === 'Arnaud Legoux Moving Average') return [fixedLine(id, 'alma', `ALMA ${period}`, alma(close, period), bars)];
  if (name === 'Aroon Indicator' || name === 'Aroon Oscillator') {
    const up = full(close.length); const down = full(close.length);
    for (let i = period - 1; i < close.length; i += 1) {
      const h = high.slice(i - period + 1, i + 1); const l = low.slice(i - period + 1, i + 1);
      const hi = h.lastIndexOf(Math.max(...h)); const li = l.lastIndexOf(Math.min(...l));
      up[i] = 100 * (hi + 1) / period; down[i] = 100 * (li + 1) / period;
    }
    if (name === 'Aroon Oscillator') return [output(id, 'oscillator', 'Aroon Oscillator', 1, 'line', up.map((value, i) => finite(value) && finite(down[i]) ? value - down[i]! : null), bars)];
    return [output(id, 'up', 'Aroon Up', 1, 'line', up, bars, '#20c997'), output(id, 'down', 'Aroon Down', 1, 'line', down, bars, '#f23645')];
  }
  if (name === 'Average Daily Range (ADR) indicator') return [output(id, 'adr', `ADR ${period}`, 1, 'line', sma(high.map((value, i) => value - low[i]), period), bars)];
  if (name === 'Average Directional Index (ADX)') return [output(id, 'adx', `ADX ${period}`, 1, 'line', dmi(high, low, close, period).adx, bars)];
  if (name === 'Awesome Oscillator (AO)') {
    const fast = sma(hl2, 5); const slow = sma(hl2, 34);
    return [output(id, 'ao', 'AO', 1, 'histogram', close.map((_, i) => finite(fast[i]) && finite(slow[i]) ? fast[i]! - slow[i]! : null), bars)];
  }
  if (name === 'Balance of Power (BOP)') return [output(id, 'bop', 'BOP', 1, 'line', close.map((value, i) => high[i] === low[i] ? 0 : (value - open[i]) / (high[i] - low[i])), bars)];
  if (name === 'BBTrend' || name === 'Bollinger Bands %b (%b)' || name === 'Bollinger BandWidth (BBW)') {
    const short = bollinger(close, name === 'BBTrend' ? 20 : period, instance.standardDeviations ?? 2);
    if (name === 'BBTrend') {
      const long = bollinger(close, 50, instance.standardDeviations ?? 2);
      const trend = close.map((_, i) => finite(short.lower[i]) && finite(long.lower[i]) && finite(short.upper[i]) && finite(long.upper[i]) && finite(short.middle[i]) && short.middle[i] !== 0
        ? (Math.abs(short.lower[i]! - long.lower[i]!) - Math.abs(short.upper[i]! - long.upper[i]!)) / short.middle[i]! * 100 : null);
      return [output(id, 'bbtrend', 'BBTrend', 1, 'histogram', trend, bars)];
    }
    if (name === 'Bollinger Bands %b (%b)') return [output(id, 'percent-b', '%B', 1, 'line', close.map((value, i) => finite(short.upper[i]) && finite(short.lower[i]) && short.upper[i] !== short.lower[i] ? (value - short.lower[i]!) / (short.upper[i]! - short.lower[i]!) : null), bars)];
    return [output(id, 'bandwidth', 'BBW', 1, 'line', close.map((_, i) => finite(short.upper[i]) && finite(short.lower[i]) && finite(short.middle[i]) && short.middle[i] !== 0 ? (short.upper[i]! - short.lower[i]!) / short.middle[i]! * 100 : null), bars)];
  }
  if (name === 'Bull Bear Power') {
    const basis = ema(close, period);
    return [output(id, 'bull', 'Bull Power', 1, 'histogram', high.map((value, i) => finite(basis[i]) ? value - basis[i]! : null), bars, '#20c997'), output(id, 'bear', 'Bear Power', 1, 'histogram', low.map((value, i) => finite(basis[i]) ? value - basis[i]! : null), bars, '#f23645')];
  }
  if (name === 'Chaikin Money Flow (CMF)') {
    const mfv = close.map((value, i) => high[i] === low[i] ? 0 : (((value - low[i]) - (high[i] - value)) / (high[i] - low[i])) * volume[i]);
    const mfvSum = rollingSum(mfv, period); const volSum = rollingSum(volume, period);
    return [output(id, 'cmf', `CMF ${period}`, 1, 'line', close.map((_, i) => finite(mfvSum[i]) && finite(volSum[i]) && volSum[i] !== 0 ? mfvSum[i]! / volSum[i]! : null), bars)];
  }
  if (name === 'Chaikin Oscillator') {
    let cumulative = 0;
    const adl = close.map((value, i) => { const range = high[i] - low[i]; cumulative += (range === 0 ? 0 : ((value - low[i]) - (high[i] - value)) / range) * volume[i]; return cumulative; });
    const fast = ema(adl, 3); const slow = ema(adl, 10);
    return [output(id, 'chaikin', 'Chaikin Oscillator', 1, 'line', close.map((_, i) => finite(fast[i]) && finite(slow[i]) ? fast[i]! - slow[i]! : null), bars)];
  }
  if (name === 'Chande Kroll Stop' || name === 'Chandelier Exit') {
    const a = atr(high, low, close, name === 'Chandelier Exit' ? period : 10);
    const lookback = name === 'Chandelier Exit' ? period : 20;
    const hh = highest(high, lookback); const ll = lowest(low, lookback);
    const mult = name === 'Chandelier Exit' ? 3 : 1;
    return [fixedLine(id, 'long-stop', 'Long Stop', high.map((_, i) => finite(hh[i]) && finite(a[i]) ? hh[i]! - mult * a[i]! : null), bars, '#20c997'), fixedLine(id, 'short-stop', 'Short Stop', low.map((_, i) => finite(ll[i]) && finite(a[i]) ? ll[i]! + mult * a[i]! : null), bars, '#f23645')];
  }
  if (name === 'Chande Momentum Oscillator (CMO)') {
    const gains = close.map((value, i) => i === 0 ? 0 : Math.max(0, value - close[i - 1])); const losses = close.map((value, i) => i === 0 ? 0 : Math.max(0, close[i - 1] - value));
    const gs = rollingSum(gains, period); const ls = rollingSum(losses, period);
    return [output(id, 'cmo', `CMO ${period}`, 1, 'line', close.map((_, i) => finite(gs[i]) && finite(ls[i]) && gs[i]! + ls[i]! !== 0 ? 100 * (gs[i]! - ls[i]!) / (gs[i]! + ls[i]!) : null), bars)];
  }
  if (name === 'Choppiness Index (CHOP)') {
    const trs = rollingSum(trueRange(high, low, close), period); const hh = highest(high, period); const ll = lowest(low, period);
    return [output(id, 'chop', `CHOP ${period}`, 1, 'line', close.map((_, i) => finite(trs[i]) && finite(hh[i]) && finite(ll[i]) && hh[i] !== ll[i] ? 100 * Math.log10(trs[i]! / (hh[i]! - ll[i]!)) / Math.log10(period) : null), bars)];
  }
  if (name === 'Commodity Channel Index (CCI)') return [output(id, 'cci', `CCI ${period}`, 1, 'line', cci(high, low, close, period), bars)];
  if (name === 'Connors RSI (CRSI)') return [output(id, 'crsi', 'Connors RSI', 1, 'line', connorsRsi(close), bars)];
  if (name === 'Coppock Curve') {
    const r14 = roc(close, 14); const r11 = roc(close, 11); const raw = close.map((_, i) => (finite(r14[i]) ? r14[i]! : 0) + (finite(r11[i]) ? r11[i]! : 0));
    return [output(id, 'coppock', 'Coppock Curve', 1, 'line', wma(raw, 10), bars)];
  }
  if (name === 'Detrended Price Oscillator (DPO)') {
    const basis = sma(close, period); const shift = Math.floor(period / 2) + 1;
    return [output(id, 'dpo', `DPO ${period}`, 1, 'line', close.map((_, i) => i >= shift && finite(basis[i - shift]) ? close[i - shift] - basis[i - shift]! : null), bars)];
  }
  if (name === 'Directional Movement (DMI)') {
    const values = dmi(high, low, close, period);
    return [output(id, 'plus-di', '+DI', 1, 'line', values.plus, bars, '#20c997'), output(id, 'minus-di', '-DI', 1, 'line', values.minus, bars, '#f23645'), output(id, 'adx', 'ADX', 1, 'line', values.adx, bars, '#74c0fc')];
  }
  if (name === 'Donchian Channels (DC)') {
    const upper = highest(high, period); const lower = lowest(low, period); const middle = close.map((_, i) => finite(upper[i]) && finite(lower[i]) ? (upper[i]! + lower[i]!) / 2 : null);
    return [fixedLine(id, 'upper', 'DC Upper', upper, bars), fixedLine(id, 'middle', 'DC Middle', middle, bars), fixedLine(id, 'lower', 'DC Lower', lower, bars)];
  }
  if (name === 'Double Exponential Moving Average (EMA)') {
    const first = ema(close, period); const firstNumeric = first.map((value, i) => finite(value) ? value : close[i]); const second = ema(firstNumeric, period);
    return [fixedLine(id, 'dema', `DEMA ${period}`, close.map((_, i) => finite(first[i]) && finite(second[i]) ? 2 * first[i]! - second[i]! : null), bars)];
  }
  if (name === 'Ease of Movement (EOM)') {
    const raw = close.map((_, i) => i === 0 || volume[i] === 0 ? 0 : (((high[i] + low[i]) / 2 - (high[i - 1] + low[i - 1]) / 2) * (high[i] - low[i]) * 100000000) / volume[i]);
    return [output(id, 'eom', `EOM ${period}`, 1, 'line', sma(raw, period), bars)];
  }
  if (name === "Elder's Force Index (EFI)") return [output(id, 'efi', `EFI ${period}`, 1, 'line', forceIndex(close, volume, period), bars)];
  if (name === 'Envelope (ENV)') {
    const basis = sma(close, period); const pct = 0.1;
    return [fixedLine(id, 'upper', 'Envelope Upper', basis.map((v) => finite(v) ? v * (1 + pct) : null), bars), fixedLine(id, 'basis', 'Envelope Basis', basis, bars), fixedLine(id, 'lower', 'Envelope Lower', basis.map((v) => finite(v) ? v * (1 - pct) : null), bars)];
  }
  if (name === 'Fisher Transform') {
    const hh = highest(hl2, period); const ll = lowest(hl2, period); const values = full(close.length); let previous = 0; let fisher = 0;
    for (let i = 0; i < close.length; i += 1) {
      if (!finite(hh[i]) || !finite(ll[i]) || hh[i] === ll[i]) continue;
      previous = Math.max(-0.999, Math.min(0.999, 0.66 * ((hl2[i] - ll[i]!) / (hh[i]! - ll[i]!) - 0.5) + 0.67 * previous));
      fisher = 0.5 * Math.log((1 + previous) / (1 - previous)) + 0.5 * fisher; values[i] = fisher;
    }
    return [output(id, 'fisher', 'Fisher', 1, 'line', values, bars)];
  }
  if (name === 'Historical Volatility') {
    const logReturns = close.map((value, i) => i === 0 || close[i - 1] <= 0 || value <= 0 ? 0 : Math.log(value / close[i - 1])); const sd = stdev(logReturns, period);
    return [output(id, 'hv', `HV ${period}`, 1, 'line', sd.map((v) => finite(v) ? v * Math.sqrt(252) * 100 : null), bars)];
  }
  if (name === 'Hull Moving Average') return [fixedLine(id, 'hma', `HMA ${period}`, hull(close, period), bars)];
  if (name === 'Ichimoku Cloud') {
    const conversionHigh = highest(high, 9); const conversionLow = lowest(low, 9); const baseHigh = highest(high, 26); const baseLow = lowest(low, 26); const spanBHigh = highest(high, 52); const spanBLow = lowest(low, 52);
    const conversion = close.map((_, i) => finite(conversionHigh[i]) && finite(conversionLow[i]) ? (conversionHigh[i]! + conversionLow[i]!) / 2 : null);
    const base = close.map((_, i) => finite(baseHigh[i]) && finite(baseLow[i]) ? (baseHigh[i]! + baseLow[i]!) / 2 : null);
    const spanA = close.map((_, i) => i >= 26 && finite(conversion[i - 26]) && finite(base[i - 26]) ? (conversion[i - 26]! + base[i - 26]!) / 2 : null);
    const spanB = close.map((_, i) => i >= 26 && finite(spanBHigh[i - 26]) && finite(spanBLow[i - 26]) ? (spanBHigh[i - 26]! + spanBLow[i - 26]!) / 2 : null);
    const lagging = close.map((_, i) => i + 26 < close.length ? close[i + 26] : null);
    return [fixedLine(id, 'conversion', 'Conversion', conversion, bars), fixedLine(id, 'base', 'Base', base, bars), fixedLine(id, 'span-a', 'Leading Span A', spanA, bars, '#20c997'), fixedLine(id, 'span-b', 'Leading Span B', spanB, bars, '#f23645'), fixedLine(id, 'lagging', 'Lagging Span', lagging, bars)];
  }
  if (name === "Kaufman's Adaptive Moving Average (KAMA)") return [fixedLine(id, 'kama', `KAMA ${period}`, kama(close, period), bars)];
  if (name === 'Keltner Channels (KC)') {
    const basis = ema(close, period); const a = atr(high, low, close, period);
    return [fixedLine(id, 'upper', 'KC Upper', close.map((_, i) => finite(basis[i]) && finite(a[i]) ? basis[i]! + 2 * a[i]! : null), bars), fixedLine(id, 'basis', 'KC Basis', basis, bars), fixedLine(id, 'lower', 'KC Lower', close.map((_, i) => finite(basis[i]) && finite(a[i]) ? basis[i]! - 2 * a[i]! : null), bars)];
  }
  if (name === 'Klinger Oscillator') {
    const trend = close.map((_, i) => i === 0 ? 1 : (high[i] + low[i] + close[i] > high[i - 1] + low[i - 1] + close[i - 1] ? 1 : -1));
    const vf = close.map((_, i) => trend[i] * volume[i] * Math.abs(2 * ((high[i] - low[i]) / Math.max(Number.EPSILON, high[i] + low[i])) - 1) * 100);
    const fast = ema(vf, 34); const slow = ema(vf, 55); const ko = close.map((_, i) => finite(fast[i]) && finite(slow[i]) ? fast[i]! - slow[i]! : null); const signal = ema(ko.map((v) => finite(v) ? v : 0), 13);
    return [output(id, 'klinger', 'Klinger', 1, 'line', ko, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)];
  }
  if (name === 'Know Sure Thing (KST)') {
    const r1 = roc(close, 10).map((v) => finite(v) ? v : 0); const r2 = roc(close, 15).map((v) => finite(v) ? v : 0); const r3 = roc(close, 20).map((v) => finite(v) ? v : 0); const r4 = roc(close, 30).map((v) => finite(v) ? v : 0);
    const s1 = sma(r1, 10); const s2 = sma(r2, 10); const s3 = sma(r3, 10); const s4 = sma(r4, 15);
    const kst = close.map((_, i) => [s1[i], s2[i], s3[i], s4[i]].every(finite) ? s1[i]! + 2 * s2[i]! + 3 * s3[i]! + 4 * s4[i]! : null); const signal = sma(kst.map((v) => finite(v) ? v : 0), 9);
    return [output(id, 'kst', 'KST', 1, 'line', kst, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)];
  }
  if (name === 'Least Squares Moving Average' || name === 'Linear Regression') return [fixedLine(id, 'linreg', `${name === 'Least Squares Moving Average' ? 'LSMA' : 'Linear Regression'} ${period}`, linearRegression(close, period), bars)];
  if (name === 'MA Cross' || name === 'MovingAvg Cross' || name === 'MovingAvg2Line Cross') {
    const fastPeriod = instance.fastPeriod ?? period; const slowPeriod = instance.slowPeriod ?? (name === 'MovingAvg2Line Cross' ? 26 : 21);
    return [fixedLine(id, 'fast', `Fast MA ${fastPeriod}`, sma(close, fastPeriod), bars), fixedLine(id, 'slow', `Slow MA ${slowPeriod}`, sma(close, slowPeriod), bars)];
  }
  if (name === 'Mass Index') {
    const range = high.map((value, i) => value - low[i]); const e1 = ema(range, 9); const e2 = ema(e1.map((v) => finite(v) ? v : 0), 9); const ratio = close.map((_, i) => finite(e1[i]) && finite(e2[i]) && e2[i] !== 0 ? e1[i]! / e2[i]! : 0);
    return [output(id, 'mass', 'Mass Index', 1, 'line', rollingSum(ratio, period), bars)];
  }
  if (name === 'McGinley Dynamic') {
    const values = full(close.length); if (close.length) { let md = close[0]; values[0] = md; for (let i = 1; i < close.length; i += 1) { const ratio = md === 0 ? 1 : close[i] / md; md += (close[i] - md) / Math.max(1, 0.6 * period * ratio ** 4); values[i] = md; } }
    return [fixedLine(id, 'mcginley', `McGinley ${period}`, values, bars)];
  }
  if (name === 'Median') return [fixedLine(id, 'median', `Median ${period}`, median(close, period), bars)];
  if (name === 'Momentum') return [output(id, 'momentum', `Momentum ${period}`, 1, 'line', close.map((value, i) => i >= period ? value - close[i - period] : null), bars)];
  if (name === 'Money Flow (MFI)') return [output(id, 'mfi', `MFI ${period}`, 1, 'line', moneyFlowIndex(high, low, close, volume, period), bars)];
  if (name === 'Moving Average Ribbon') {
    return [20, 50, 100, 200].map((p) => fixedLine(id, `sma-${p}`, `SMA ${p}`, sma(close, p), bars));
  }
  if (name === 'Moving Averages') return [fixedLine(id, 'sma', `SMA ${period}`, sma(close, period), bars), fixedLine(id, 'ema', `EMA ${period}`, ema(close, period), bars)];
  if (name === 'Negative Volume Index (NVI)' || name === 'Positive Volume Index (PVI)') {
    const positive = name.startsWith('Positive'); let current = 1000; const values = close.map((value, i) => { if (i > 0 && (positive ? volume[i] > volume[i - 1] : volume[i] < volume[i - 1]) && close[i - 1] !== 0) current *= 1 + (value - close[i - 1]) / close[i - 1]; return current; });
    return [output(id, positive ? 'pvi' : 'nvi', positive ? 'PVI' : 'NVI', 1, 'line', values, bars)];
  }
  if (name === 'Net Volume' || name === 'Up/Down Volume') {
    const values = volume.map((value, i) => i === 0 ? 0 : close[i] > close[i - 1] ? value : close[i] < close[i - 1] ? -value : 0);
    return [output(id, 'net-volume', name, 1, 'histogram', values, bars)];
  }
  if (name === 'On Balance Volume (OBV)') { let current = 0; const values = volume.map((value, i) => { if (i > 0) current += close[i] > close[i - 1] ? value : close[i] < close[i - 1] ? -value : 0; return current; }); return [output(id, 'obv', 'OBV', 1, 'line', values, bars)]; }
  if (name === 'Parabolic SAR (SAR)') return [fixedLine(id, 'sar', 'Parabolic SAR', parabolicSar(high, low), bars)];
  if (name === 'Percentage Price Oscillator (PPO)' || name === 'Percentage Volume Oscillator (PVO)') {
    const source = name === 'Percentage Volume Oscillator (PVO)' ? volume : close; const fastPeriod = instance.fastPeriod ?? 12; const slowPeriod = instance.slowPeriod ?? 26; const signalPeriod = instance.signalPeriod ?? 9;
    const fast = ema(source, fastPeriod); const slow = ema(source, slowPeriod); const line = source.map((_, i) => finite(fast[i]) && finite(slow[i]) && slow[i] !== 0 ? 100 * (fast[i]! - slow[i]!) / slow[i]! : null); const signal = ema(line.map((v) => finite(v) ? v : 0), signalPeriod);
    return [output(id, 'line', name.endsWith('(PVO)') ? 'PVO' : 'PPO', 1, 'line', line, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)];
  }
  if (name === 'Performance') { const anchor = close[0] || 1; return [output(id, 'performance', 'Performance %', 1, 'line', close.map((value) => (value / anchor - 1) * 100), bars)]; }
  if (name === 'Pivot Points High Low') {
    const pivots = findPatternPivots(bars, Math.max(2, Math.min(8, Math.round(period / 3)))); const highs = full(close.length); const lows = full(close.length); let h: number | null = null; let l: number | null = null; const byIndex = new Map(pivots.map((pivot) => [pivot.index, pivot]));
    close.forEach((_, i) => { const pivot = byIndex.get(i); if (pivot?.type === 'high') h = pivot.price; if (pivot?.type === 'low') l = pivot.price; highs[i] = h; lows[i] = l; });
    return [levelLine(id, 'pivot-high', 'Pivot High', highs, bars, '#f23645'), levelLine(id, 'pivot-low', 'Pivot Low', lows, bars, '#20c997')];
  }
  if (name === 'Pivot Points Standard') {
    const pp = full(close.length); const r1 = full(close.length); const s1 = full(close.length);
    for (let i = 1; i < close.length; i += 1) { const pivot = (high[i - 1] + low[i - 1] + close[i - 1]) / 3; pp[i] = pivot; r1[i] = 2 * pivot - low[i - 1]; s1[i] = 2 * pivot - high[i - 1]; }
    return [levelLine(id, 'pp', 'Pivot', pp, bars), levelLine(id, 'r1', 'R1', r1, bars, '#f23645'), levelLine(id, 's1', 'S1', s1, bars, '#20c997')];
  }
  if (name === 'Price Momentum Oscillator (PMO)') {
    const oneRoc = roc(close, 1).map((v) => finite(v) ? v * 10 : 0); const smooth1 = ema(oneRoc, 35).map((v) => finite(v) ? v : 0); const pmo = ema(smooth1, 20); const signal = ema(pmo.map((v) => finite(v) ? v : 0), 10);
    return [output(id, 'pmo', 'PMO', 1, 'line', pmo, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)];
  }
  if (name === 'Price Volume Trend (PVT)') { let current = 0; const values = close.map((value, i) => { if (i > 0 && close[i - 1] !== 0) current += volume[i] * (value - close[i - 1]) / close[i - 1]; return current; }); return [output(id, 'pvt', 'PVT', 1, 'line', values, bars)]; }
  if (name === "Pring's Special K") {
    const component = (r: number, s: number, weight: number) => sma(roc(close, r).map((v) => finite(v) ? v : 0), s).map((v) => finite(v) ? v * weight : 0);
    const components = [[10,10,1],[15,10,2],[20,10,3],[30,15,4],[40,20,1],[65,30,2],[75,30,3],[100,40,4],[195,65,1],[265,65,2],[390,100,3],[530,130,4]].map(([r,s,w]) => component(r,s,w));
    const values = close.map((_, i) => components.reduce((total, series) => total + (series[i] ?? 0), 0));
    return [output(id, 'special-k', 'Special K', 1, 'line', values, bars)];
  }
  if (name === 'Rank Correlation Index (RCI)' || name === 'RCI Ribbon') {
    if (name === 'RCI Ribbon') return [9, 26, 52].map((p) => output(id, `rci-${p}`, `RCI ${p}`, 1, 'line', spearman(close, p), bars));
    return [output(id, 'rci', `RCI ${period}`, 1, 'line', spearman(close, period), bars)];
  }
  if (name === 'Rate of Change (ROC)') return [output(id, 'roc', `ROC ${period}`, 1, 'line', roc(close, period), bars)];
  if (name === 'Relative Vigor Index') { const values = relativeVigor(open, high, low, close, period); return [output(id, 'rvi', 'RVI', 1, 'line', values.rvi, bars), output(id, 'signal', 'Signal', 1, 'line', values.signal, bars)]; }
  if (name === 'Relative Volatility Index') return [output(id, 'rvol', `RVI ${period}`, 1, 'line', relativeVolatility(close, period), bars)];
  if (name === 'SMI Ergodic Indicator' || name === 'SMI Ergodic Oscillator') {
    const tsi = trueStrength(close, 20, 5); const signal = ema(tsi.map((v) => finite(v) ? v : 0), 5); if (name === 'SMI Ergodic Oscillator') return [output(id, 'oscillator', 'SMI Ergodic Osc', 1, 'histogram', close.map((_, i) => finite(tsi[i]) && finite(signal[i]) ? tsi[i]! - signal[i]! : null), bars)];
    return [output(id, 'smi', 'SMI Ergodic', 1, 'line', tsi, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)];
  }
  if (name === 'Smoothed Moving Average') return [fixedLine(id, 'smma', `SMMA ${period}`, rma(close, period), bars)];
  if (name === 'Stochastic (STOCH)') { const k = stochastic(high, low, close, period); const d = sma(k.map((v) => finite(v) ? v : 0), 3); return [output(id, 'k', '%K', 1, 'line', k, bars), output(id, 'd', '%D', 1, 'line', d, bars)]; }
  if (name === 'Stochastic Momentum Index (SMI)') { const smi = stochasticMomentum(high, low, close, period); const signal = ema(smi.map((v) => finite(v) ? v : 0), 3); return [output(id, 'smi', 'SMI', 1, 'line', smi, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)]; }
  if (name === 'Supertrend') { const values = supertrend(high, low, close, period); return [fixedLine(id, 'supertrend', 'Supertrend', values.line, bars)]; }
  if (name === 'Technical Ratings') return [output(id, 'rating', 'Technical Rating', 1, 'histogram', technicalRating(high, low, close, volume), bars)];
  if (name === 'Time Weighted Average Price') return [fixedLine(id, 'twap', 'TWAP', sessionTwap(bars, typical, instance.session ?? UTC_SESSION), bars)];
  if (name === 'Trend Strength Index') return [output(id, 'trend-strength', `Trend Strength ${period}`, 1, 'line', correlationWithIndex(close, period), bars)];
  if (name === 'Triple EMA') return [fixedLine(id, 'tema', `TEMA ${period}`, tripleEma(close, period), bars)];
  if (name === 'TRIX') { const e1 = ema(close, period).map((v, i) => finite(v) ? v : close[i]); const e2 = ema(e1, period).map((v, i) => finite(v) ? v : e1[i]); const e3 = ema(e2, period).map((v, i) => finite(v) ? v : e2[i]); return [output(id, 'trix', `TRIX ${period}`, 1, 'line', e3.map((value, i) => i === 0 || e3[i - 1] === 0 ? null : (value / e3[i - 1] - 1) * 100), bars)]; }
  if (name === 'True Strength Index') { const tsi = trueStrength(close, instance.slowPeriod ?? 25, instance.fastPeriod ?? 13); const signal = ema(tsi.map((v) => finite(v) ? v : 0), instance.signalPeriod ?? 13); return [output(id, 'tsi', 'TSI', 1, 'line', tsi, bars), output(id, 'signal', 'Signal', 1, 'line', signal, bars)]; }
  if (name === 'Ulcer Index') {
    const hh = highest(close, period); const squared = close.map((value, i) => finite(hh[i]) && hh[i] !== 0 ? ((value - hh[i]!) / hh[i]! * 100) ** 2 : 0); const average = sma(squared, period);
    return [output(id, 'ulcer', `Ulcer ${period}`, 1, 'line', average.map((v) => finite(v) ? Math.sqrt(v) : null), bars)];
  }
  if (name === 'Ultimate Oscillator (UO)') return [output(id, 'uo', 'Ultimate Oscillator', 1, 'line', ultimateOscillator(high, low, close), bars)];
  if (name === 'Volatility Stop') { const a = atr(high, low, close, period); const hh = highest(close, period); const ll = lowest(close, period); const trend = close.map((value, i) => finite(hh[i]) && finite(ll[i]) ? value >= (hh[i]! + ll[i]!) / 2 : true); return [fixedLine(id, 'vstop', 'Volatility Stop', close.map((_, i) => finite(a[i]) && finite(hh[i]) && finite(ll[i]) ? (trend[i] ? hh[i]! - 2 * a[i]! : ll[i]! + 2 * a[i]!) : null), bars)]; }
  if (name === 'Volume') return [output(id, 'volume', 'Volume', 1, 'histogram', volume, bars)];
  if (name === 'Volume-Weighted Moving Average (VWMA)') return [fixedLine(id, 'vwma', `VWMA ${period}`, volumeWeightedMa(close, volume, period), bars)];
  if (name === 'Vortex Indicator') { const vi = vortex(high, low, close, period); return [output(id, 'plus', 'VI+', 1, 'line', vi.plus, bars, '#20c997'), output(id, 'minus', 'VI-', 1, 'line', vi.minus, bars, '#f23645')]; }
  if (name === 'Weighted Moving Average') return [fixedLine(id, 'wma', `WMA ${period}`, wma(close, period), bars)];
  if (name === 'Williams %R (%R)') { const hh = highest(high, period); const ll = lowest(low, period); return [output(id, 'williams-r', 'Williams %R', 1, 'line', close.map((value, i) => finite(hh[i]) && finite(ll[i]) && hh[i] !== ll[i] ? -100 * (hh[i]! - value) / (hh[i]! - ll[i]!) : null), bars)]; }
  if (name === 'Williams Alligator') { return [fixedLine(id, 'jaw', 'Jaw 13', rma(hl2, 13), bars, '#74c0fc'), fixedLine(id, 'teeth', 'Teeth 8', rma(hl2, 8), bars, '#f23645'), fixedLine(id, 'lips', 'Lips 5', rma(hl2, 5), bars, '#20c997')]; }
  if (name === 'Williams Fractal') {
    const radius = Math.max(2, Math.min(8, period)); const up = full(close.length); const down = full(close.length);
    for (let i = radius; i < close.length - radius; i += 1) { const hs = high.slice(i - radius, i + radius + 1); const ls = low.slice(i - radius, i + radius + 1); if (high[i] === Math.max(...hs)) up[i] = high[i]; if (low[i] === Math.min(...ls)) down[i] = low[i]; }
    return [markerLine(id, 'up-fractal', 'Up Fractal', up, bars, '#f23645', 'arrowDown'), markerLine(id, 'down-fractal', 'Down Fractal', down, bars, '#20c997', 'arrowUp')];
  }
  if (name === 'Woodies CCI') { const fast = cci(high, low, close, period); const slow = cci(high, low, close, 6); return [output(id, 'trend-cci', 'Trend CCI', 1, 'line', fast, bars), output(id, 'entry-cci', 'Entry CCI', 1, 'line', slow, bars)]; }
  if (name === 'Zig Zag') {
    const pivots = findPatternPivots(bars, Math.max(2, Math.min(8, period))); const values = full(close.length); pivots.forEach((pivot) => { values[pivot.index] = pivot.price; }); return [fixedLine(id, 'zigzag', 'Zig Zag', values, bars)];
  }
  return quickWinOutputs(name, id, period, instance, series, bars) ?? [];
}

export function tradingViewBuiltInPlotDefinitions(instance: TradingViewBuiltInInstance): Array<{ key: string; title: string }> {
  const definition = definitionById.get(instance.id);
  // Intrabar indicators aren't computed here (no golden or server twin) but draw plots all the same.
  if (!definition || !(definition.available || definition.name in INTRABAR_INPUTS) || !isTradingViewBuiltInId(instance.id)) return [];
  const id = instance.id;
  const name = definition.name;
  const multi: Record<string, Array<[string, string]>> = {
    'Aroon Indicator': [['up', 'Aroon Up'], ['down', 'Aroon Down']],
    'Bull Bear Power': [['bull', 'Bull Power'], ['bear', 'Bear Power']],
    'Chande Kroll Stop': [['long-stop', 'Long Stop'], ['short-stop', 'Short Stop']],
    'Chandelier Exit': [['long-stop', 'Long Stop'], ['short-stop', 'Short Stop']],
    'Directional Movement (DMI)': [['plus-di', '+DI'], ['minus-di', '-DI'], ['adx', 'ADX']],
    'Donchian Channels (DC)': [['upper', 'DC Upper'], ['middle', 'DC Middle'], ['lower', 'DC Lower']],
    'Envelope (ENV)': [['upper', 'Envelope Upper'], ['basis', 'Envelope Basis'], ['lower', 'Envelope Lower']],
    'Ichimoku Cloud': [['conversion', 'Conversion'], ['base', 'Base'], ['span-a', 'Leading Span A'], ['span-b', 'Leading Span B'], ['lagging', 'Lagging Span']],
    'Keltner Channels (KC)': [['upper', 'KC Upper'], ['basis', 'KC Basis'], ['lower', 'KC Lower']],
    'Klinger Oscillator': [['klinger', 'Klinger'], ['signal', 'Signal']],
    'Know Sure Thing (KST)': [['kst', 'KST'], ['signal', 'Signal']],
    'MA Cross': [['fast', 'Fast MA'], ['slow', 'Slow MA']],
    'MovingAvg Cross': [['fast', 'Fast MA'], ['slow', 'Slow MA']],
    'MovingAvg2Line Cross': [['fast', 'Fast MA'], ['slow', 'Slow MA']],
    'Percentage Price Oscillator (PPO)': [['line', 'PPO'], ['signal', 'Signal']],
    'Percentage Volume Oscillator (PVO)': [['line', 'PVO'], ['signal', 'Signal']],
    'Pivot Points High Low': [['pivot-high', 'Pivot High'], ['pivot-low', 'Pivot Low']],
    'Pivot Points Standard': [['pp', 'Pivot'], ['r1', 'R1'], ['s1', 'S1']],
    'Relative Vigor Index': [['rvi', 'RVI'], ['signal', 'Signal']],
    'Rob Booker - ADX Breakout': [['upper', 'Box Upper'], ['lower', 'Box Lower'], ['breakout-up', 'Breakout Up'], ['breakout-down', 'Breakout Down']],
    'Rob Booker - Knoxville Divergence': [['bearish', 'Bearish Divergence'], ['bullish', 'Bullish Divergence']],
    'Rob Booker Intraday Pivot Points': [['pp', 'Pivot'], ['r1', 'R1'], ['r2', 'R2'], ['r3', 'R3'], ['s1', 'S1'], ['s2', 'S2'], ['s3', 'S3']],
    'Rob Booker Missed Pivot Points': [['missed-above', 'Missed Pivot Above'], ['missed-below', 'Missed Pivot Below']],
    'Rob Booker Reversal': [['bullish', 'Bullish Reversal'], ['bearish', 'Bearish Reversal']],
    'Rob Booker Ziv Ghost Pivots': [['pp', 'Ghost Pivot'], ['r1', 'Ghost R1'], ['s1', 'Ghost S1']],
    'SMI Ergodic Indicator': [['smi', 'SMI Ergodic'], ['signal', 'Signal']],
    'Stochastic (STOCH)': [['k', '%K'], ['d', '%D']],
    'Stochastic Momentum Index (SMI)': [['smi', 'SMI'], ['signal', 'Signal']],
    'True Strength Index': [['tsi', 'TSI'], ['signal', 'Signal']],
    'Vortex Indicator': [['plus', 'VI+'], ['minus', 'VI-']],
    'Williams Alligator': [['jaw', 'Jaw'], ['teeth', 'Teeth'], ['lips', 'Lips']],
    'Williams Fractal': [['up-fractal', 'Up Fractal'], ['down-fractal', 'Down Fractal']],
    'Woodies CCI': [['trend-cci', 'Trend CCI'], ['entry-cci', 'Entry CCI']],
    // TVP-6.2 indicators that draw (drawingIndicators.ts); the auto fibs', key levels' and seasonality's plots vary with the bars.
    'Auto Pitchfork': [['median', 'Median'], ['upper', 'Upper'], ['lower', 'Lower']],
    'Auto Trendlines': [['resistance', 'Resistance'], ['support', 'Support']],
    'Bollinger Bars': [['bars', 'Bar colors'], ['upper', 'Upper'], ['lower', 'Lower']],
    'Chop Zone': [['zone', 'Chop Zone']],
    'Advance/Decline Ratio (Bars)': [['ratio', 'A/D Ratio (Bars)']],
    'Moon Phases': [['full', 'Full moon'], ['new', 'New moon']],
    'Multi-Time Period Charts indicator': [['candles', 'Period candles'], ['high', 'Period high'], ['low', 'Period low'], ['open', 'Period open']],
    'Trading Sessions': [['sessions', 'Trading Sessions']],
    'Visible Average Price': [['average', 'Visible Average Price']],
    'VWAP Auto Anchored': [['vwap', 'Auto Anchored VWAP']],
    'Volume Delta': [['delta', 'Volume Delta']],
    'Cumulative Volume Delta': [['cvd', 'CVD']],
  };
  if (name === 'All Candlestick Patterns') {
    return selectedCandlestickPatterns(String(builtInParams(name, instance).patterns)).map((pattern) => ({ key: `${id}:${pattern.key}`, title: pattern.name }));
  }
  if (name === 'Moving Average Ribbon') return [20, 50, 100, 200].map((p) => ({ key: `${id}:sma-${p}`, title: `SMA ${p}` }));
  if (name === 'Moving Averages') return [{ key: `${id}:sma`, title: 'SMA' }, { key: `${id}:ema`, title: 'EMA' }];
  if (name === 'RCI Ribbon') return [9, 26, 52].map((p) => ({ key: `${id}:rci-${p}`, title: `RCI ${p}` }));
  const plots = multi[name];
  if (plots) return plots.map(([suffix, title]) => ({ key: `${id}:${suffix}`, title }));
  const suffixByName: Record<string, string> = {
    '24-hour Volume': 'volume-24h', 'Correlation Coefficient (CC)': 'cc', 'Relative Volume at Time': 'rvol-at-time',
    'Accumulation Distribution (ADL)': 'adl', 'Arnaud Legoux Moving Average': 'alma', 'Aroon Oscillator': 'oscillator',
    'Average Daily Range (ADR) indicator': 'adr', 'Average Directional Index (ADX)': 'adx', 'Awesome Oscillator (AO)': 'ao',
    'Balance of Power (BOP)': 'bop', 'BBTrend': 'bbtrend', 'Bollinger Bands %b (%b)': 'percent-b', 'Bollinger BandWidth (BBW)': 'bandwidth',
    'Chaikin Money Flow (CMF)': 'cmf', 'Chaikin Oscillator': 'chaikin', 'Chande Momentum Oscillator (CMO)': 'cmo',
    'Choppiness Index (CHOP)': 'chop', 'Commodity Channel Index (CCI)': 'cci', 'Connors RSI (CRSI)': 'crsi', 'Coppock Curve': 'coppock',
    'Detrended Price Oscillator (DPO)': 'dpo', 'Double Exponential Moving Average (EMA)': 'dema', 'Ease of Movement (EOM)': 'eom',
    "Elder's Force Index (EFI)": 'efi', 'Fisher Transform': 'fisher', 'Historical Volatility': 'hv', 'Hull Moving Average': 'hma',
    "Kaufman's Adaptive Moving Average (KAMA)": 'kama', 'Least Squares Moving Average': 'linreg', 'Linear Regression': 'linreg',
    'Mass Index': 'mass', 'McGinley Dynamic': 'mcginley', 'Median': 'median', 'Momentum': 'momentum', 'Money Flow (MFI)': 'mfi',
    'Negative Volume Index (NVI)': 'nvi', 'Net Volume': 'net-volume', 'On Balance Volume (OBV)': 'obv', 'Parabolic SAR (SAR)': 'sar',
    'Performance': 'performance', 'Positive Volume Index (PVI)': 'pvi', 'Price Momentum Oscillator (PMO)': 'pmo', 'Price Volume Trend (PVT)': 'pvt',
    "Pring's Special K": 'special-k', 'Rank Correlation Index (RCI)': 'rci', 'Rate of Change (ROC)': 'roc', 'Relative Volatility Index': 'rvol',
    'SMI Ergodic Oscillator': 'oscillator', 'Smoothed Moving Average': 'smma', 'Supertrend': 'supertrend', 'Technical Ratings': 'rating',
    'Time Weighted Average Price': 'twap', 'Trend Strength Index': 'trend-strength', 'Triple EMA': 'tema', 'TRIX': 'trix', 'Ulcer Index': 'ulcer',
    'Ultimate Oscillator (UO)': 'uo', 'Up/Down Volume': 'net-volume', 'Volatility Stop': 'vstop', 'Volume': 'volume',
    'Volume-Weighted Moving Average (VWMA)': 'vwma', 'Weighted Moving Average': 'wma', 'Williams %R (%R)': 'williams-r', 'Zig Zag': 'zigzag',
  };
  const suffix = suffixByName[name];
  return suffix ? [{ key: `${id}:${suffix}`, title: name }] : [];
}
