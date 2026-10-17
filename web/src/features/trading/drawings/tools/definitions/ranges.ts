// Date range and date-and-price range (TVP-3.6), with TradingView's labels:
// price change, percent and ticks; bars and time span; volume.
import { chartPalette } from '../../../chartPalette';
import {
  DEFAULT_DRAWING_STYLE,
  defineDrawingTool,
  type DrawingBarSeries,
  type DrawingGeometryContext,
  type DrawingPoint,
  type DrawingShape,
  type DrawingToolServices,
  type ScreenPoint,
} from '../types';

export type RangeStats = {
  delta: number;
  percent: number;
  /** Price change in ticks; null when the instrument's tick size is unknown. */
  ticks: number | null;
  /** Bars between the anchors on the chart (gaps don't count); null without an index. */
  bars: number | null;
  /** Milliseconds between the anchors' times. */
  span: number;
  /** Volume of the bars that start between the anchors (inclusive); null without loaded bars. */
  volume: number | null;
};

/** Volume of the loaded bars starting between `from` and `to` (either order, inclusive). */
export function volumeBetween(bars: DrawingBarSeries, from: string, to: string): number | null {
  if (bars.length === 0) return null;
  const [start, end] = Date.parse(from) <= Date.parse(to) ? [from, to] : [to, from];
  const last = bars.indexAtOrBefore(end);
  if (last < 0) return 0;
  let total = 0;
  for (let index = last; index >= 0; index -= 1) {
    const bar = bars.at(index);
    if (!bar || Date.parse(bar.time) < Date.parse(start)) break;
    total += Number.isFinite(bar.volume) ? bar.volume : 0;
  }
  return total;
}

export function rangeStats(first: DrawingPoint, second: DrawingPoint, services: Pick<DrawingToolServices, 'barIndexForTime' | 'bars' | 'instrument'>): RangeStats {
  const delta = second.price - first.price;
  const tick = services.instrument.tickSize;
  const firstIndex = services.barIndexForTime(first.time);
  const secondIndex = services.barIndexForTime(second.time);
  return {
    delta,
    percent: first.price === 0 ? 0 : delta / first.price * 100,
    ticks: tick ? Math.round(delta / tick) : null,
    bars: firstIndex === null || secondIndex === null ? null : Math.round(secondIndex - firstIndex),
    span: Date.parse(second.time) - Date.parse(first.time),
    volume: volumeBetween(services.bars, first.time, second.time),
  };
}

/** A time span as TradingView writes it: `3d 4h`, `2h 15m`, `45m`, `30s`. */
export function formatSpan(milliseconds: number): string {
  const sign = milliseconds < 0 ? '-' : '';
  let seconds = Math.round(Math.abs(milliseconds) / 1_000);
  const days = Math.floor(seconds / 86_400);
  seconds -= days * 86_400;
  const hours = Math.floor(seconds / 3_600);
  seconds -= hours * 3_600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;
  const parts = [days && `${days}d`, hours && `${hours}h`, minutes && `${minutes}m`, !days && !hours && seconds && `${seconds}s`].filter(Boolean);
  return `${sign}${parts.length ? parts.slice(0, 2).join(' ') : '0m'}`;
}

/** 1234 -> `1.23K`, 2500000 -> `2.5M`. */
export function formatVolume(volume: number): string {
  const abs = Math.abs(volume);
  for (const [size, suffix] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
    if (abs >= size) return `${(volume / size).toLocaleString('en-US', { maximumFractionDigits: 2 })}${suffix}`;
  }
  return volume.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

export function priceLine(stats: RangeStats, formatPrice: (price: number) => string): string {
  const sign = stats.delta < 0 ? '-' : '';
  const percent = `${stats.percent < 0 ? '-' : ''}${Math.abs(stats.percent).toFixed(2)}%`;
  return `${sign}${formatPrice(Math.abs(stats.delta))} (${percent})${stats.ticks === null ? '' : ` ${stats.ticks.toLocaleString('en-US')}`}`;
}

export function timeLine(stats: RangeStats): string {
  return `${stats.bars === null ? '' : `${stats.bars.toLocaleString('en-US')} bars, `}${formatSpan(stats.span)}`;
}

export function volumeLine(stats: RangeStats): string | null {
  return stats.volume === null ? null : `Vol ${formatVolume(stats.volume)}`;
}

function measureColor(context: DrawingGeometryContext): string {
  return context.style.color === DEFAULT_DRAWING_STYLE.color ? chartPalette.drawingBlue : context.style.color;
}

/** The label box under (or over) the range with one text line per entry. */
function labelBox(center: number, top: number, lines: readonly string[]): DrawingShape[] {
  const width = Math.max(110, ...lines.map((line) => line.length * 7 + 18));
  const height = lines.length * 17 + 9;
  return [
    { kind: 'rect', x: center - width / 2, y: top, width, height, radius: 5, fill: '#ffffff', stroke: 'rgba(92, 99, 106, 0.28)', strokeWidth: 1, hit: 'none', className: 'trading-measurement-label-box' },
    ...lines.map((text, index): DrawingShape => ({
      kind: 'text', x: center, y: top + 18 + index * 17, text, align: 'middle', fontSize: 12, fontWeight: 500, fill: '#202124', halo: false, hit: 'none', className: 'trading-measurement-label-text',
    })),
  ];
}

function arrowHeadAt(tip: ScreenPoint, direction: 'left' | 'right' | 'up' | 'down', paint: Partial<DrawingShape>): DrawingShape {
  const size = 7;
  const points = direction === 'right' ? [{ x: tip.x - size, y: tip.y - size }, tip, { x: tip.x - size, y: tip.y + size }]
    : direction === 'left' ? [{ x: tip.x + size, y: tip.y - size }, tip, { x: tip.x + size, y: tip.y + size }]
      : direction === 'up' ? [{ x: tip.x - size, y: tip.y + size }, tip, { x: tip.x + size, y: tip.y + size }]
        : [{ x: tip.x - size, y: tip.y - size }, tip, { x: tip.x + size, y: tip.y - size }];
  return { kind: 'polyline', points, ...paint } as DrawingShape;
}

function rangeGeometry(context: DrawingGeometryContext, axes: { horizontal: boolean; vertical: boolean }): DrawingShape[] {
  const [first, second] = context.points;
  const color = measureColor(context);
  const left = Math.min(first.x, second.x);
  const right = Math.max(first.x, second.x);
  const top = Math.min(first.y, second.y);
  const bottom = Math.max(first.y, second.y);
  const middleX = (left + right) / 2;
  const middleY = (top + bottom) / 2;
  const edge = { stroke: color, strokeWidth: 1.5, hit: 'none' } as const;
  const shapes: DrawingShape[] = [{ kind: 'rect', x: left, y: top, width: right - left, height: bottom - top, fill: color, fillOpacity: 0.14, className: 'trading-measurement-area' }];
  if (axes.horizontal) {
    shapes.push({ kind: 'segment', x1: left, y1: middleY, x2: right, y2: middleY, ...edge });
    shapes.push(arrowHeadAt({ x: second.x, y: middleY }, second.x >= first.x ? 'right' : 'left', edge));
    shapes.push({ kind: 'segment', x1: left, y1: top, x2: left, y2: bottom, ...edge }, { kind: 'segment', x1: right, y1: top, x2: right, y2: bottom, ...edge });
  }
  if (axes.vertical) {
    shapes.push({ kind: 'segment', x1: middleX, y1: top, x2: middleX, y2: bottom, ...edge });
    shapes.push(arrowHeadAt({ x: middleX, y: second.y }, second.y <= first.y ? 'up' : 'down', edge));
    if (!axes.horizontal) shapes.push({ kind: 'segment', x1: left, y1: top, x2: right, y2: top, ...edge }, { kind: 'segment', x1: left, y1: bottom, x2: right, y2: bottom, ...edge });
  }
  const stats = rangeStats(context.rawPoints[0], context.rawPoints[1], context);
  const lines = [
    axes.vertical ? priceLine(stats, context.formatPrice) : null,
    axes.horizontal ? timeLine(stats) : null,
    axes.horizontal ? volumeLine(stats) : null,
  ].filter((line): line is string => Boolean(line));
  // TradingView puts the label on the side the range was drawn towards.
  const downward = second.y > first.y;
  const boxTop = downward ? bottom + 8 : top - 8 - (lines.length * 17 + 9);
  shapes.push(...labelBox(middleX, boxTop, lines));
  return shapes;
}

export const dateRangeTool = defineDrawingTool({
  id: 'date-range',
  label: 'Date range',
  group: 'measurers',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  geometry: (context) => rangeGeometry(context, { horizontal: true, vertical: false }),
});

export const datePriceRangeTool = defineDrawingTool({
  id: 'date-price-range',
  label: 'Date and price range',
  group: 'measurers',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  geometry: (context) => rangeGeometry(context, { horizontal: true, vertical: true }),
});
