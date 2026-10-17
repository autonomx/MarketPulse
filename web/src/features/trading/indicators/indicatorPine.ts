import type { CoreIndicatorId, CoreIndicatorInstance } from './coreIndicators';
import { PRODUCT_NAME } from '../../../shared/productIdentity';

const indicatorPineTitles: Partial<Record<CoreIndicatorId, string>> = {
  sma: 'Simple Moving Average',
  ema: 'Exponential Moving Average',
  rsi: 'Relative Strength Index',
  macd: 'Moving Average Convergence Divergence',
  bollinger: 'Bollinger Bands',
  atr: 'Average True Range',
  vwap: 'Volume Weighted Average Price',
  'bull-market-band': 'Bull Market Support Band',
  'death-cross': 'Death Cross',
  'ema-stack': 'EMA Stack',
  'fair-value-gap': 'Fair Value Gap',
  'golden-cross': 'Golden Cross',
  'ideal-bb': 'IDEAL BB with MA',
  'log-macd': 'Log MACD',
  'macd-dema': 'MACD DEMA',
  'rsi-divergence': 'RSI Divergence',
  'stochastic-rsi': 'Stochastic RSI',
  'swing-liquidity': 'Swing Levels and Liquidity',
  'volume-profile': 'Volume Profile',
};

function script(lines: string[]): string {
  return lines.join('\n');
}

const indicatorPineTemplates: Partial<Record<CoreIndicatorId, string>> = {
  sma: script([
    '//@version=6',
    'indicator("Simple Moving Average", shorttitle="SMA", overlay=true)',
    'length = input.int({{PERIOD}}, "Length", minval=1)',
    'plot(ta.sma(close, length), "SMA", color=color.yellow, linewidth=1)',
  ]),
  ema: script([
    '//@version=6',
    'indicator("Exponential Moving Average", shorttitle="EMA", overlay=true)',
    'length = input.int({{PERIOD}}, "Length", minval=1)',
    'plot(ta.ema(close, length), "EMA", color=color.aqua, linewidth=1)',
  ]),
  rsi: script([
    '//@version=6',
    'indicator("Relative Strength Index", shorttitle="RSI", format=format.price, precision=2)',
    'length = input.int({{PERIOD}}, "RSI Length", minval=1)',
    'source = input.source(close, "RSI Source")',
    'rsi = ta.rsi(source, length)',
    'upper = hline(70, "Upper Band", color=#787B86)',
    'middle = hline(50, "Middle Band", color=color.new(#787B86, 50))',
    'lower = hline(30, "Lower Band", color=#787B86)',
    'fill(upper, lower, color=color.rgb(33, 150, 243, 90), title="Background")',
    'plot(rsi, "RSI", color=#7E57C2, linewidth=1)',
  ]),
  macd: script([
    '//@version=6',
    'indicator("Moving Average Convergence Divergence", shorttitle="MACD")',
    'fastLength = input.int({{FAST}}, "Fast Length", minval=1)',
    'slowLength = input.int({{SLOW}}, "Slow Length", minval=1)',
    'signalLength = input.int({{SIGNAL}}, "Signal Smoothing", minval=1)',
    '[macdLine, signalLine, histogram] = ta.macd(close, fastLength, slowLength, signalLength)',
    'plot(macdLine, "MACD", color=#2962FF)',
    'plot(signalLine, "Signal", color=#FF6D00)',
    'plot(histogram, "Histogram", style=plot.style_columns, color=histogram >= 0 ? #26A69A : #EF5350)',
  ]),
  bollinger: script([
    '//@version=6',
    'indicator("Bollinger Bands", shorttitle="BB", overlay=true)',
    'length = input.int({{PERIOD}}, "Length", minval=1)',
    'mult = input.float(2.0, "StdDev", minval=0.001, maxval=50)',
    'basis = ta.sma(close, length)',
    'dev = mult * ta.stdev(close, length)',
    'upper = basis + dev',
    'lower = basis - dev',
    'plot(basis, "Basis", color=#2962FF)',
    'p1 = plot(upper, "Upper", color=#F23645)',
    'p2 = plot(lower, "Lower", color=#089981)',
    'fill(p1, p2, title="Background", color=color.new(#2962FF, 90))',
  ]),
  atr: script([
    '//@version=6',
    'indicator("Average True Range", shorttitle="ATR")',
    'length = input.int({{PERIOD}}, "Length", minval=1)',
    'plot(ta.atr(length), "ATR", color=#2962FF)',
  ]),
  vwap: script([
    '//@version=6',
    'indicator("Volume Weighted Average Price", shorttitle="VWAP", overlay=true)',
    'source = input.source(hlc3, "Source")',
    'vwap = ta.vwap(source)',
    'plot(vwap, "VWAP", color=#2962FF, linewidth=2)',
  ]),
  'bull-market-band': script([
    '//@version=6',
    'indicator("Bull Market Support Band", shorttitle="Bull Market Band", overlay=true)',
    'weeklySma = request.security(syminfo.tickerid, "W", ta.sma(close, 20))',
    'weeklyEma = request.security(syminfo.tickerid, "W", ta.ema(close, 21))',
    'smaPlot = plot(weeklySma, "20w SMA", color=#F23645)',
    'emaPlot = plot(weeklyEma, "21w EMA", color=#4CAF50)',
    'fill(smaPlot, emaPlot, color=color.new(#4CAF50, 85), title="Support Band")',
  ]),
  'death-cross': script([
    '//@version=6',
    'indicator("Death Cross", shorttitle="Death Cross", overlay=true)',
    'fast = ta.sma(close, 50)',
    'slow = ta.sma(close, 200)',
    'plot(fast, "50 SMA", color=#FF6D00)',
    'plot(slow, "200 SMA", color=#2962FF)',
    'plotshape(ta.crossunder(fast, slow), "Death Cross", shape.labeldown, location.abovebar, color=#F23645, text="X")',
  ]),
  'ema-stack': script([
    '//@version=6',
    'indicator("EMA Stack", shorttitle="EMA Stack", overlay=true)',
    'ema9 = ta.ema(close, 9)',
    'ema21 = ta.ema(close, 21)',
    'ema50 = ta.ema(close, 50)',
    'ema200 = ta.ema(close, 200)',
    'plot(ema9, "EMA 9", color=#FFD600)',
    'plot(ema21, "EMA 21", color=#FF6D00)',
    'plot(ema50, "EMA 50", color=#E91E63)',
    'plot(ema200, "EMA 200", color=#4CAF50)',
  ]),
  'fair-value-gap': script([
    '//@version=6',
    'indicator("Fair Value Gap", shorttitle="FVG", overlay=true, max_boxes_count=200)',
    'showBullish = input.bool(true, "Bullish gaps")',
    'showBearish = input.bool(true, "Bearish gaps")',
    'bullishGap = low > high[2]',
    'bearishGap = high < low[2]',
    'if bullishGap and showBullish',
    '    box.new(bar_index - 2, low, bar_index, high[2], bgcolor=color.new(#20C997, 84), border_color=#20C997)',
    'if bearishGap and showBearish',
    '    box.new(bar_index - 2, low[2], bar_index, high, bgcolor=color.new(#F23645, 84), border_color=#F23645)',
  ]),
  'golden-cross': script([
    '//@version=6',
    'indicator("Golden Cross", shorttitle="Golden Cross", overlay=true)',
    'fast = ta.sma(close, 50)',
    'slow = ta.sma(close, 200)',
    'plot(fast, "50 SMA", color=#FFD600)',
    'plot(slow, "200 SMA", color=#2962FF)',
    'plotshape(ta.crossover(fast, slow), "Golden Cross", shape.labelup, location.belowbar, color=#20C997, text="X")',
  ]),
  'ideal-bb': script([
    '//@version=6',
    'indicator("IDEAL BB with MA", shorttitle="IDEAL BB", overlay=true)',
    'length = input.int({{PERIOD}}, "BB Length", minval=1)',
    'mult = input.float(2.0, "StdDev", minval=0.1)',
    'basis = ta.sma(close, length)',
    'dev = mult * ta.stdev(close, length)',
    'plot(basis, "Middle", color=#FFD600)',
    'plot(basis + dev, "Upper", color=#26A69A)',
    'plot(basis - dev, "Lower", color=#EF5350)',
  ]),
  'log-macd': script([
    '//@version=6',
    'indicator("Log MACD", shorttitle="Log MACD")',
    'fastLength = input.int({{FAST}}, "Fast Length", minval=1)',
    'slowLength = input.int({{SLOW}}, "Slow Length", minval=1)',
    'signalLength = input.int({{SIGNAL}}, "Signal Smoothing", minval=1)',
    'logSource = math.log(close)',
    '[macdLine, signalLine, histogram] = ta.macd(logSource, fastLength, slowLength, signalLength)',
    'plot(macdLine, "MACD", color=#2962FF)',
    'plot(signalLine, "Signal", color=#FF6D00)',
    'plot(histogram, "Histogram", style=plot.style_columns, color=histogram >= 0 ? #20C997 : #F23645)',
  ]),
  'macd-dema': script([
    '//@version=6',
    'indicator("MACD DEMA", shorttitle="MACD DEMA")',
    'fastLength = input.int({{FAST}}, "Fast Length", minval=1)',
    'slowLength = input.int({{SLOW}}, "Slow Length", minval=1)',
    'signalLength = input.int({{SIGNAL}}, "Signal Smoothing", minval=1)',
    'dema(source, length) =>',
    '    first = ta.ema(source, length)',
    '    2 * first - ta.ema(first, length)',
    'fast = dema(close, fastLength)',
    'slow = dema(close, slowLength)',
    'macdLine = fast - slow',
    'signalLine = ta.ema(macdLine, signalLength)',
    'plot(macdLine, "MACD", color=#2962FF)',
    'plot(signalLine, "Signal", color=#FF6D00)',
    'plot(macdLine - signalLine, "Histogram", style=plot.style_columns, color=#20C997)',
  ]),
  'rsi-divergence': script([
    '//@version=6',
    'indicator("RSI Divergence", shorttitle="RSI Div")',
    'length = input.int({{PERIOD}}, "RSI Length", minval=1)',
    'left = input.int(5, "Pivot Left", minval=1)',
    'right = input.int(5, "Pivot Right", minval=1)',
    'rsi = ta.rsi(close, length)',
    'priceLow = ta.pivotlow(low, left, right)',
    'priceHigh = ta.pivothigh(high, left, right)',
    'plot(rsi, "RSI", color=#7E57C2)',
    'hline(70, "Overbought", color=#787B86)',
    'hline(30, "Oversold", color=#787B86)',
    'plotshape(not na(priceLow), "Bullish pivot", shape.triangleup, location.bottom, color=#20C997)',
    'plotshape(not na(priceHigh), "Bearish pivot", shape.triangledown, location.top, color=#F23645)',
  ]),
  'stochastic-rsi': script([
    '//@version=6',
    'indicator("Stochastic RSI", shorttitle="Stoch RSI")',
    'rsiLength = input.int({{PERIOD}}, "RSI Length", minval=1)',
    'stochLength = input.int({{PERIOD}}, "Stochastic Length", minval=1)',
    'smoothK = input.int({{FAST}}, "Smooth K", minval=1)',
    'smoothD = input.int({{SIGNAL}}, "Smooth D", minval=1)',
    'rsi = ta.rsi(close, rsiLength)',
    'k = ta.sma(ta.stoch(rsi, rsi, rsi, stochLength), smoothK)',
    'd = ta.sma(k, smoothD)',
    'h0 = hline(80, "Upper Band", color=#787B86)',
    'h1 = hline(20, "Lower Band", color=#787B86)',
    'fill(h0, h1, color=color.rgb(33, 150, 243, 90))',
    'plot(k, "K", color=#2962FF)',
    'plot(d, "D", color=#FF6D00)',
  ]),
  'swing-liquidity': script([
    '//@version=6',
    'indicator("Swing Levels and Liquidity", shorttitle="Swing Liquidity", overlay=true)',
    'left = input.int(5, "Pivot Left", minval=1)',
    'right = input.int(5, "Pivot Right", minval=1)',
    'swingHigh = ta.pivothigh(high, left, right)',
    'swingLow = ta.pivotlow(low, left, right)',
    'plot(swingHigh, "Swing High", color=#F23645, style=plot.style_linebr, offset=-right)',
    'plot(swingLow, "Swing Low", color=#20C997, style=plot.style_linebr, offset=-right)',
    'plotshape(not na(swingHigh), "High liquidity", shape.circle, location.abovebar, color=#F23645, offset=-right)',
    'plotshape(not na(swingLow), "Low liquidity", shape.circle, location.belowbar, color=#20C997, offset=-right)',
  ]),
  'volume-profile': script([
    '//@version=6',
    'indicator("Volume Profile", shorttitle="Volume Profile", overlay=true, max_boxes_count=200)',
    'rows = input.int(24, "Rows", minval=5, maxval=100)',
    'lookback = input.int({{PERIOD}}, "Lookback", minval=1)',
    '// The chart implementation bins volume by price and draws the profile at the right edge.',
    '// Pine volume profiles can be used for the same visual in TradingView.',
    'profile = volume.profile_fixed(lookback, rows)',
    'if barstate.islast and not na(profile)',
    '    profile.plot(line_color=color.new(#2962FF, 0), fill_color=color.new(#2962FF, 82), value_area_color=color.new(#20C997, 78))',
  ]),
};

function pineNumber(value: number | undefined, fallback: number): string {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? String(value) : String(fallback);
}

export function indicatorPineTitle(id: CoreIndicatorId): string {
  return indicatorPineTitles[id] ?? id.split('-').map((part) => part[0]?.toUpperCase() + part.slice(1)).join(' ');
}

export function indicatorPineSource(indicator: CoreIndicatorInstance): string {
  const period = pineNumber(indicator.period, 14);
  const fast = pineNumber(indicator.fastPeriod, 12);
  const slow = pineNumber(indicator.slowPeriod, 26);
  const signal = pineNumber(indicator.signalPeriod, 9);
  const template = indicatorPineTemplates[indicator.id];
  if (!template) {
    return script([
      '//@version=6',
      `// ${indicatorPineTitle(indicator.id)} is implemented by the ${PRODUCT_NAME} deterministic chart-pattern engine.`,
      '// It is a native study and does not currently have a Pine-equivalent source template.',
    ]);
  }
  return template
    .replaceAll('{{PERIOD}}', period)
    .replaceAll('{{FAST}}', fast)
    .replaceAll('{{SLOW}}', slow)
    .replaceAll('{{SIGNAL}}', signal);
}

export function allIndicatorPineSources(): Readonly<Partial<Record<CoreIndicatorId, string>>> {
  return indicatorPineTemplates;
}
