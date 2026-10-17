import type { MarketBar } from '../tradingTypes';

export type AutoChartPatternId =
  | 'chart-patterns-all'
  | 'bearish-flag-pattern'
  | 'bullish-flag-pattern'
  | 'cup-handle-pattern'
  | 'inverted-cup-handle-pattern'
  | 'double-bottom-pattern'
  | 'double-top-pattern'
  | 'elliott-wave-pattern'
  | 'head-shoulders-pattern'
  | 'inverse-head-shoulders-pattern'
  | 'bearish-pennant-pattern'
  | 'bullish-pennant-pattern'
  | 'rectangle-pattern'
  | 'triangle-pattern'
  | 'triple-bottom-pattern'
  | 'triple-top-pattern'
  | 'falling-wedge-pattern'
  | 'rising-wedge-pattern'
  | 'auto-trend-detector';

export type AutoChartPatternDefinition = {
  id: AutoChartPatternId;
  name: string;
};

export const AUTO_CHART_PATTERN_DEFINITIONS: readonly AutoChartPatternDefinition[] = [
  { id: 'chart-patterns-all', name: 'All Chart Patterns' },
  { id: 'bearish-flag-pattern', name: 'Bearish Flag Chart Pattern' },
  { id: 'bullish-flag-pattern', name: 'Bullish Flag Chart Pattern' },
  { id: 'cup-handle-pattern', name: 'Cup and Handle Chart Pattern' },
  { id: 'inverted-cup-handle-pattern', name: 'Inverted Cup and Handle Chart Pattern' },
  { id: 'double-bottom-pattern', name: 'Double Bottom Chart Pattern' },
  { id: 'double-top-pattern', name: 'Double Top Chart Pattern' },
  { id: 'elliott-wave-pattern', name: 'Elliott Wave Chart Pattern' },
  { id: 'head-shoulders-pattern', name: 'Head and Shoulders Chart Pattern' },
  { id: 'inverse-head-shoulders-pattern', name: 'Inverted Head and Shoulders Chart Pattern' },
  { id: 'bearish-pennant-pattern', name: 'Bearish Pennant Chart Pattern' },
  { id: 'bullish-pennant-pattern', name: 'Bullish Pennant Chart Pattern' },
  { id: 'rectangle-pattern', name: 'Rectangle Chart Pattern' },
  { id: 'triangle-pattern', name: 'Triangle Chart Pattern' },
  { id: 'triple-bottom-pattern', name: 'Triple Bottom Chart Pattern' },
  { id: 'triple-top-pattern', name: 'Triple Top Chart Pattern' },
  { id: 'falling-wedge-pattern', name: 'Falling Wedge Chart Pattern' },
  { id: 'rising-wedge-pattern', name: 'Rising Wedge Chart Pattern' },
  { id: 'auto-trend-detector', name: 'Auto Trend Detector' },
] as const;

export const AUTO_CHART_PATTERN_IDS = AUTO_CHART_PATTERN_DEFINITIONS.map((definition) => definition.id);

export function isAutoChartPatternId(value: string): value is AutoChartPatternId {
  return (AUTO_CHART_PATTERN_IDS as readonly string[]).includes(value);
}

export type AutoPatternPivot = {
  index: number;
  price: number;
  type: 'high' | 'low';
};

export type AutoPatternSegment = {
  fromIndex: number;
  fromPrice: number;
  toIndex: number;
  toPrice: number;
  dashed?: boolean;
};

export type AutoChartPatternMatch = {
  id: Exclude<AutoChartPatternId, 'chart-patterns-all'>;
  name: string;
  direction: 'bullish' | 'bearish' | 'neutral';
  confidence: number;
  startIndex: number;
  endIndex: number;
  segments: AutoPatternSegment[];
};

export type AutoChartPatternLine = {
  key: string;
  title: string;
  points: Array<{ time: string; value: number }>;
  color: string;
  lineStyle: 'solid' | 'dashed';
  lineWidth: 1 | 2 | 3 | 4;
};

type Regression = { slope: number; intercept: number; r2: number };

const definitionById = new Map(AUTO_CHART_PATTERN_DEFINITIONS.map((definition) => [definition.id, definition.name]));
const detectorIds = AUTO_CHART_PATTERN_IDS.filter((id): id is Exclude<AutoChartPatternId, 'chart-patterns-all' | 'auto-trend-detector'> => (
  id !== 'chart-patterns-all' && id !== 'auto-trend-detector'
));

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function finitePrice(value: string | number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function relativeDifference(left: number, right: number): number {
  return Math.abs(left - right) / Math.max(Math.abs(left), Math.abs(right), Number.EPSILON);
}

function regression(points: readonly AutoPatternPivot[]): Regression | null {
  if (points.length < 2) return null;
  const meanX = points.reduce((sum, point) => sum + point.index, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.price, 0) / points.length;
  let covariance = 0;
  let varianceX = 0;
  let varianceY = 0;
  for (const point of points) {
    const dx = point.index - meanX;
    const dy = point.price - meanY;
    covariance += dx * dy;
    varianceX += dx * dx;
    varianceY += dy * dy;
  }
  if (varianceX <= Number.EPSILON) return null;
  const slope = covariance / varianceX;
  const intercept = meanY - slope * meanX;
  const r2 = varianceY <= Number.EPSILON ? 1 : clamp((covariance * covariance) / (varianceX * varianceY), 0, 1);
  return { slope, intercept, r2 };
}

function lineValue(line: Regression, index: number): number {
  return line.intercept + line.slope * index;
}

function segment(from: AutoPatternPivot, to: AutoPatternPivot, dashed = false): AutoPatternSegment {
  return { fromIndex: from.index, fromPrice: from.price, toIndex: to.index, toPrice: to.price, dashed };
}

function lineSegment(line: Regression, fromIndex: number, toIndex: number, dashed = false): AutoPatternSegment {
  return {
    fromIndex,
    fromPrice: lineValue(line, fromIndex),
    toIndex,
    toPrice: lineValue(line, toIndex),
    dashed,
  };
}

function nameFor(id: Exclude<AutoChartPatternId, 'chart-patterns-all'>): string {
  return definitionById.get(id) ?? id;
}

function match(
  id: Exclude<AutoChartPatternId, 'chart-patterns-all'>,
  direction: AutoChartPatternMatch['direction'],
  confidence: number,
  startIndex: number,
  endIndex: number,
  segments: AutoPatternSegment[],
): AutoChartPatternMatch {
  return {
    id,
    name: nameFor(id),
    direction,
    confidence: clamp(confidence, 0.5, 0.99),
    startIndex,
    endIndex,
    segments,
  };
}

export function findPatternPivots(bars: readonly MarketBar[], strength = 3): AutoPatternPivot[] {
  const radius = clamp(Math.trunc(strength) || 3, 2, 8);
  const candidates: AutoPatternPivot[] = [];
  for (let index = radius; index < bars.length - radius; index += 1) {
    const window = bars.slice(index - radius, index + radius + 1);
    const high = finitePrice(bars[index].high);
    const low = finitePrice(bars[index].low);
    const maximum = Math.max(...window.map((bar) => finitePrice(bar.high)));
    const minimum = Math.min(...window.map((bar) => finitePrice(bar.low)));
    if (high >= maximum) candidates.push({ index, price: high, type: 'high' });
    if (low <= minimum) candidates.push({ index, price: low, type: 'low' });
  }

  const alternating: AutoPatternPivot[] = [];
  for (const candidate of candidates.sort((left, right) => left.index - right.index || (left.type === 'high' ? -1 : 1))) {
    const previous = alternating.at(-1);
    if (!previous) {
      alternating.push(candidate);
      continue;
    }
    if (candidate.index === previous.index) {
      const previousClose = finitePrice(bars[previous.index].close);
      if (Math.abs(candidate.price - previousClose) > Math.abs(previous.price - previousClose)) alternating[alternating.length - 1] = candidate;
      continue;
    }
    if (candidate.type === previous.type) {
      const moreExtreme = candidate.type === 'high' ? candidate.price >= previous.price : candidate.price <= previous.price;
      if (moreExtreme) alternating[alternating.length - 1] = candidate;
      continue;
    }
    alternating.push(candidate);
  }
  return alternating;
}

function scanPivotWindow(
  pivots: readonly AutoPatternPivot[],
  types: readonly AutoPatternPivot['type'][],
  predicate: (window: readonly AutoPatternPivot[]) => AutoChartPatternMatch | null,
): AutoChartPatternMatch | null {
  for (let start = pivots.length - types.length; start >= 0; start -= 1) {
    const window = pivots.slice(start, start + types.length);
    if (window.every((pivot, index) => pivot.type === types[index])) {
      const candidate = predicate(window);
      if (candidate) return candidate;
    }
  }
  return null;
}

function doublePattern(
  id: 'double-top-pattern' | 'double-bottom-pattern',
  pivots: readonly AutoPatternPivot[],
): AutoChartPatternMatch | null {
  const top = id === 'double-top-pattern';
  const types: AutoPatternPivot['type'][] = top ? ['high', 'low', 'high'] : ['low', 'high', 'low'];
  return scanPivotWindow(pivots, types, (window) => {
    const [first, middle, last] = window;
    const similarity = relativeDifference(first.price, last.price);
    const rim = (first.price + last.price) / 2;
    const depth = top ? (rim - middle.price) / Math.max(Math.abs(rim), Number.EPSILON) : (middle.price - rim) / Math.max(Math.abs(rim), Number.EPSILON);
    if (similarity > 0.045 || depth < 0.025) return null;
    const neckline: AutoPatternSegment = { fromIndex: middle.index, fromPrice: middle.price, toIndex: last.index, toPrice: middle.price, dashed: true };
    return match(id, top ? 'bearish' : 'bullish', 0.68 + clamp(depth, 0, 0.15) + (0.045 - similarity) * 2.5, first.index, last.index, [segment(first, middle), segment(middle, last), neckline]);
  });
}

function triplePattern(
  id: 'triple-top-pattern' | 'triple-bottom-pattern',
  pivots: readonly AutoPatternPivot[],
): AutoChartPatternMatch | null {
  const top = id === 'triple-top-pattern';
  const types: AutoPatternPivot['type'][] = top ? ['high', 'low', 'high', 'low', 'high'] : ['low', 'high', 'low', 'high', 'low'];
  return scanPivotWindow(pivots, types, (window) => {
    const rims = [window[0].price, window[2].price, window[4].price];
    const mean = rims.reduce((sum, value) => sum + value, 0) / rims.length;
    const dispersion = Math.max(...rims.map((value) => Math.abs(value - mean))) / Math.max(Math.abs(mean), Number.EPSILON);
    const inner = [window[1].price, window[3].price];
    const depth = top
      ? (Math.min(...rims) - Math.max(...inner)) / Math.max(Math.abs(mean), Number.EPSILON)
      : (Math.min(...inner) - Math.max(...rims)) / Math.max(Math.abs(mean), Number.EPSILON);
    if (dispersion > 0.05 || depth < 0.02) return null;
    const necklinePrice = top ? Math.max(...inner) : Math.min(...inner);
    return match(id, top ? 'bearish' : 'bullish', 0.72 + clamp(depth, 0, 0.14) + (0.05 - dispersion) * 2, window[0].index, window[4].index, [
      ...window.slice(1).map((point, index) => segment(window[index], point)),
      { fromIndex: window[1].index, fromPrice: necklinePrice, toIndex: window[4].index, toPrice: necklinePrice, dashed: true },
    ]);
  });
}

function headAndShoulders(
  id: 'head-shoulders-pattern' | 'inverse-head-shoulders-pattern',
  pivots: readonly AutoPatternPivot[],
): AutoChartPatternMatch | null {
  const inverted = id === 'inverse-head-shoulders-pattern';
  const types: AutoPatternPivot['type'][] = inverted ? ['low', 'high', 'low', 'high', 'low'] : ['high', 'low', 'high', 'low', 'high'];
  return scanPivotWindow(pivots, types, (window) => {
    const [left, neck1, head, neck2, right] = window;
    const shouldersSimilar = relativeDifference(left.price, right.price) <= 0.065;
    const neckSimilar = relativeDifference(neck1.price, neck2.price) <= 0.08;
    const shoulderMean = (left.price + right.price) / 2;
    const prominence = inverted
      ? (shoulderMean - head.price) / Math.max(Math.abs(shoulderMean), Number.EPSILON)
      : (head.price - shoulderMean) / Math.max(Math.abs(shoulderMean), Number.EPSILON);
    if (!shouldersSimilar || !neckSimilar || prominence < 0.025) return null;
    const neckline = regression([neck1, neck2]);
    if (!neckline) return null;
    return match(id, inverted ? 'bullish' : 'bearish', 0.74 + clamp(prominence, 0, 0.14), left.index, right.index, [
      segment(left, neck1), segment(neck1, head), segment(head, neck2), segment(neck2, right), lineSegment(neckline, neck1.index, right.index, true),
    ]);
  });
}

function channelPattern(
  id: 'triangle-pattern' | 'rectangle-pattern' | 'falling-wedge-pattern' | 'rising-wedge-pattern' | 'auto-trend-detector',
  pivots: readonly AutoPatternPivot[],
): AutoChartPatternMatch | null {
  const recent = pivots.slice(-10);
  const highs = recent.filter((pivot) => pivot.type === 'high').slice(-4);
  const lows = recent.filter((pivot) => pivot.type === 'low').slice(-4);
  if (highs.length < 2 || lows.length < 2) return null;
  const highLine = regression(highs);
  const lowLine = regression(lows);
  if (!highLine || !lowLine) return null;
  const startIndex = Math.max(Math.min(highs[0].index, lows[0].index), 0);
  const endIndex = Math.max(highs.at(-1)!.index, lows.at(-1)!.index);
  const midPrice = Math.max(Number.EPSILON, Math.abs((lineValue(highLine, endIndex) + lineValue(lowLine, endIndex)) / 2));
  const normalizedHighSlope = highLine.slope / midPrice;
  const normalizedLowSlope = lowLine.slope / midPrice;
  const startGap = lineValue(highLine, startIndex) - lineValue(lowLine, startIndex);
  const endGap = lineValue(highLine, endIndex) - lineValue(lowLine, endIndex);
  if (startGap <= 0 || endGap <= 0) return null;
  const contraction = 1 - endGap / startGap;
  const segments = [lineSegment(highLine, startIndex, endIndex), lineSegment(lowLine, startIndex, endIndex)];

  if (id === 'auto-trend-detector') {
    if (highLine.r2 < 0.15 && lowLine.r2 < 0.15) return null;
    const averageSlope = (normalizedHighSlope + normalizedLowSlope) / 2;
    return match(id, averageSlope > 0.0005 ? 'bullish' : averageSlope < -0.0005 ? 'bearish' : 'neutral', 0.62 + (highLine.r2 + lowLine.r2) * 0.16, startIndex, endIndex, segments);
  }

  if (id === 'triangle-pattern') {
    const converging = normalizedHighSlope < 0.0005 && normalizedLowSlope > -0.0005 && contraction > 0.18;
    if (!converging || highLine.r2 + lowLine.r2 < 0.35) return null;
    return match(id, 'neutral', 0.68 + clamp(contraction, 0, 0.25), startIndex, endIndex, segments);
  }

  if (id === 'rectangle-pattern') {
    const flat = Math.abs(normalizedHighSlope) < 0.0015 && Math.abs(normalizedLowSlope) < 0.0015;
    const separation = endGap / midPrice;
    if (!flat || separation < 0.02 || highLine.r2 + lowLine.r2 > 1.8) return null;
    return match(id, 'neutral', 0.7 + clamp(separation, 0, 0.15), startIndex, endIndex, segments);
  }

  if (contraction < 0.15) return null;
  if (id === 'rising-wedge-pattern') {
    if (!(normalizedHighSlope > 0 && normalizedLowSlope > normalizedHighSlope)) return null;
    return match(id, 'bearish', 0.7 + clamp(contraction, 0, 0.2) + (highLine.r2 + lowLine.r2) * 0.05, startIndex, endIndex, segments);
  }
  if (!(normalizedHighSlope < normalizedLowSlope && normalizedLowSlope < 0)) return null;
  return match(id, 'bullish', 0.7 + clamp(contraction, 0, 0.2) + (highLine.r2 + lowLine.r2) * 0.05, startIndex, endIndex, segments);
}

function flagOrPennant(
  bars: readonly MarketBar[],
  id: 'bullish-flag-pattern' | 'bearish-flag-pattern' | 'bullish-pennant-pattern' | 'bearish-pennant-pattern',
): AutoChartPatternMatch | null {
  const bullish = id.startsWith('bullish');
  const pennant = id.includes('pennant');
  const lengths = [32, 40, 48, 56];
  for (const length of lengths) {
    if (bars.length < length) continue;
    const startIndex = bars.length - length;
    const poleBars = bars.slice(startIndex, startIndex + Math.max(8, Math.round(length * 0.28)));
    const consolidationBars = bars.slice(startIndex + poleBars.length);
    const poleStart = finitePrice(poleBars[0].open);
    const poleEnd = finitePrice(poleBars.at(-1)!.close);
    if (poleStart <= 0) continue;
    const poleMove = (poleEnd - poleStart) / poleStart;
    if (bullish ? poleMove < 0.07 : poleMove > -0.07) continue;

    const highs: AutoPatternPivot[] = consolidationBars.map((bar, index) => ({ index: startIndex + poleBars.length + index, price: finitePrice(bar.high), type: 'high' }));
    const lows: AutoPatternPivot[] = consolidationBars.map((bar, index) => ({ index: startIndex + poleBars.length + index, price: finitePrice(bar.low), type: 'low' }));
    const highLine = regression(highs);
    const lowLine = regression(lows);
    if (!highLine || !lowLine) continue;
    const endIndex = bars.length - 1;
    const meanPrice = Math.max(Number.EPSILON, Math.abs(finitePrice(bars[endIndex].close)));
    const highSlope = highLine.slope / meanPrice;
    const lowSlope = lowLine.slope / meanPrice;
    const firstConsolidation = highs[0].index;
    const startGap = lineValue(highLine, firstConsolidation) - lineValue(lowLine, firstConsolidation);
    const endGap = lineValue(highLine, endIndex) - lineValue(lowLine, endIndex);
    const contraction = startGap > 0 ? 1 - endGap / startGap : 0;

    if (pennant) {
      if (!(highSlope < 0.001 && lowSlope > -0.001 && contraction > 0.18)) continue;
    } else {
      const averageSlope = (highSlope + lowSlope) / 2;
      const parallel = Math.abs(highSlope - lowSlope) < 0.004;
      const counterTrend = bullish ? averageSlope < 0.001 : averageSlope > -0.001;
      if (!parallel || !counterTrend) continue;
    }

    const polePivotStart: AutoPatternPivot = { index: startIndex, price: poleStart, type: bullish ? 'low' : 'high' };
    const polePivotEnd: AutoPatternPivot = { index: startIndex + poleBars.length - 1, price: poleEnd, type: bullish ? 'high' : 'low' };
    return match(id, bullish ? 'bullish' : 'bearish', 0.72 + clamp(Math.abs(poleMove) - 0.07, 0, 0.12) + (pennant ? clamp(contraction, 0, 0.12) : 0.05), startIndex, endIndex, [
      segment(polePivotStart, polePivotEnd),
      lineSegment(highLine, firstConsolidation, endIndex),
      lineSegment(lowLine, firstConsolidation, endIndex),
    ]);
  }
  return null;
}

function cupAndHandle(
  bars: readonly MarketBar[],
  id: 'cup-handle-pattern' | 'inverted-cup-handle-pattern',
): AutoChartPatternMatch | null {
  const inverted = id === 'inverted-cup-handle-pattern';
  for (const length of [45, 60, 75, 90, 120]) {
    if (bars.length < length) continue;
    const startIndex = bars.length - length;
    const bodyLength = Math.floor(length * 0.8);
    const body = bars.slice(startIndex, startIndex + bodyLength);
    const handle = bars.slice(startIndex + bodyLength);
    const edge = Math.max(4, Math.floor(bodyLength * 0.2));
    const leftSlice = body.slice(0, edge);
    const centerSlice = body.slice(Math.floor(bodyLength * 0.32), Math.ceil(bodyLength * 0.68));
    const rightSlice = body.slice(-edge);
    const extreme = (slice: readonly MarketBar[], high: boolean) => high
      ? Math.max(...slice.map((bar) => finitePrice(bar.high)))
      : Math.min(...slice.map((bar) => finitePrice(bar.low)));
    const leftRim = extreme(leftSlice, !inverted);
    const center = extreme(centerSlice, inverted);
    const rightRim = extreme(rightSlice, !inverted);
    const rimMean = (leftRim + rightRim) / 2;
    const rimDifference = relativeDifference(leftRim, rightRim);
    const depth = inverted
      ? (center - rimMean) / Math.max(Math.abs(rimMean), Number.EPSILON)
      : (rimMean - center) / Math.max(Math.abs(rimMean), Number.EPSILON);
    if (rimDifference > 0.055 || depth < 0.06) continue;
    const handleExtreme = extreme(handle, inverted);
    const handlePullback = inverted
      ? (rimMean - handleExtreme) / Math.max(Math.abs(rimMean), Number.EPSILON)
      : (handleExtreme - rimMean) / Math.max(Math.abs(rimMean), Number.EPSILON);
    if (handlePullback < -0.02 || handlePullback > Math.max(0.14, depth * 0.7)) continue;

    const leftIndex = startIndex + leftSlice.reduce((best, bar, index) => {
      const value = inverted ? finitePrice(bar.low) : finitePrice(bar.high);
      const bestValue = inverted ? finitePrice(leftSlice[best].low) : finitePrice(leftSlice[best].high);
      return inverted ? (value < bestValue ? index : best) : (value > bestValue ? index : best);
    }, 0);
    const centerOffset = Math.floor(bodyLength * 0.32);
    const centerIndex = startIndex + centerOffset + centerSlice.reduce((best, bar, index) => {
      const value = inverted ? finitePrice(bar.high) : finitePrice(bar.low);
      const bestValue = inverted ? finitePrice(centerSlice[best].high) : finitePrice(centerSlice[best].low);
      return inverted ? (value > bestValue ? index : best) : (value < bestValue ? index : best);
    }, 0);
    const rightStart = startIndex + bodyLength - edge;
    const rightIndex = rightStart + rightSlice.reduce((best, bar, index) => {
      const value = inverted ? finitePrice(bar.low) : finitePrice(bar.high);
      const bestValue = inverted ? finitePrice(rightSlice[best].low) : finitePrice(rightSlice[best].high);
      return inverted ? (value < bestValue ? index : best) : (value > bestValue ? index : best);
    }, 0);
    const endIndex = bars.length - 1;
    return match(id, inverted ? 'bearish' : 'bullish', 0.7 + clamp(depth, 0, 0.18) + (0.055 - rimDifference), startIndex, endIndex, [
      { fromIndex: leftIndex, fromPrice: leftRim, toIndex: centerIndex, toPrice: center },
      { fromIndex: centerIndex, fromPrice: center, toIndex: rightIndex, toPrice: rightRim },
      { fromIndex: rightIndex, fromPrice: rightRim, toIndex: endIndex, toPrice: finitePrice(bars[endIndex].close) },
      { fromIndex: leftIndex, fromPrice: rimMean, toIndex: rightIndex, toPrice: rimMean, dashed: true },
    ]);
  }
  return null;
}

function elliottWave(pivots: readonly AutoPatternPivot[]): AutoChartPatternMatch | null {
  const bullish = scanPivotWindow(pivots, ['low', 'high', 'low', 'high', 'low', 'high'], (window) => {
    const [p0, p1, p2, p3, p4, p5] = window;
    if (!(p2.price > p0.price && p3.price > p1.price && p4.price > p2.price && p5.price > p3.price)) return null;
    if ((p3.price - p2.price) < (p1.price - p0.price) * 0.75) return null;
    return match('elliott-wave-pattern', 'bullish', 0.74, p0.index, p5.index, window.slice(1).map((point, index) => segment(window[index], point)));
  });
  if (bullish) return bullish;
  return scanPivotWindow(pivots, ['high', 'low', 'high', 'low', 'high', 'low'], (window) => {
    const [p0, p1, p2, p3, p4, p5] = window;
    if (!(p2.price < p0.price && p3.price < p1.price && p4.price < p2.price && p5.price < p3.price)) return null;
    if ((p2.price - p3.price) < (p0.price - p1.price) * 0.75) return null;
    return match('elliott-wave-pattern', 'bearish', 0.74, p0.index, p5.index, window.slice(1).map((point, index) => segment(window[index], point)));
  });
}

export function detectAutoChartPattern(
  bars: readonly MarketBar[],
  id: Exclude<AutoChartPatternId, 'chart-patterns-all'>,
  strength = 3,
): AutoChartPatternMatch | null {
  if (bars.length < 8) return null;
  const pivots = findPatternPivots(bars, strength);
  if (id === 'double-top-pattern' || id === 'double-bottom-pattern') return doublePattern(id, pivots);
  if (id === 'triple-top-pattern' || id === 'triple-bottom-pattern') return triplePattern(id, pivots);
  if (id === 'head-shoulders-pattern' || id === 'inverse-head-shoulders-pattern') return headAndShoulders(id, pivots);
  if (id === 'triangle-pattern' || id === 'rectangle-pattern' || id === 'falling-wedge-pattern' || id === 'rising-wedge-pattern' || id === 'auto-trend-detector') return channelPattern(id, pivots);
  if (id === 'bullish-flag-pattern' || id === 'bearish-flag-pattern' || id === 'bullish-pennant-pattern' || id === 'bearish-pennant-pattern') return flagOrPennant(bars, id);
  if (id === 'cup-handle-pattern' || id === 'inverted-cup-handle-pattern') return cupAndHandle(bars, id);
  if (id === 'elliott-wave-pattern') return elliottWave(pivots);
  return null;
}

export function detectAutoChartPatterns(bars: readonly MarketBar[], strength = 3): AutoChartPatternMatch[] {
  const matches = detectorIds
    .map((id) => detectAutoChartPattern(bars, id, strength))
    .filter((candidate): candidate is AutoChartPatternMatch => candidate !== null)
    .sort((left, right) => right.endIndex - left.endIndex || right.confidence - left.confidence);
  const selected: AutoChartPatternMatch[] = [];
  for (const candidate of matches) {
    const duplicateGeometry = selected.some((existing) => (
      Math.abs(existing.startIndex - candidate.startIndex) <= 2
      && Math.abs(existing.endIndex - candidate.endIndex) <= 2
    ));
    if (!duplicateGeometry) selected.push(candidate);
    if (selected.length >= 4) break;
  }
  return selected;
}

function colorFor(direction: AutoChartPatternMatch['direction']): string {
  if (direction === 'bullish') return '#20c997';
  if (direction === 'bearish') return '#f23645';
  return '#4dabf7';
}

export function autoChartPatternLines(
  bars: readonly MarketBar[],
  id: AutoChartPatternId,
  strength = 3,
): AutoChartPatternLine[] {
  const matches = id === 'chart-patterns-all'
    ? detectAutoChartPatterns(bars, strength)
    : [detectAutoChartPattern(bars, id, strength)].filter((candidate): candidate is AutoChartPatternMatch => candidate !== null);
  return matches.flatMap((candidate) => candidate.segments.map((patternSegment, index) => ({
    key: `${candidate.id}:${candidate.endIndex}:${index}`,
    title: index === 0 ? `${candidate.name} · ${Math.round(candidate.confidence * 100)}%` : candidate.name,
    points: [
      { time: bars[patternSegment.fromIndex]?.start_time ?? bars[0]?.start_time ?? '', value: patternSegment.fromPrice },
      { time: bars[patternSegment.toIndex]?.start_time ?? bars.at(-1)?.start_time ?? '', value: patternSegment.toPrice },
    ].filter((point) => Boolean(point.time)),
    color: colorFor(candidate.direction),
    lineStyle: patternSegment.dashed ? 'dashed' : 'solid',
    lineWidth: index === 0 ? 2 : 1,
  })));
}
