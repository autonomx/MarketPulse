// Long and short position (TVP-3.6).
//
// Three anchors: [entry (left edge time, entry price), stop (right edge time, stop price), target (right edge time,
// target price)]. Keeping stop and target as anchors means moving, cloning and pasting carry them along. One click
// creates the drawing (`onCreate` fills in stop, target and width from the recent bars); handles move the entry, the
// stop, the target and the right edge. The context menu pre-fills the paper order ticket through the action bus.
import { numberProperty } from '../properties';
import {
  defineDrawingTool,
  type DrawingBarSeries,
  type DrawingGeometryContext,
  type DrawingHandle,
  type DrawingPoint,
  type DrawingShape,
  type DrawingToolServices,
} from '../types';
import { formatSpan } from './ranges';

export type PositionSide = 'long' | 'short';

const PROFIT = '#089981';
const LOSS = '#f23645';

export type PositionLevels = { entry: number; stop: number; target: number; start: string; end: string };

export function positionLevels(points: readonly DrawingPoint[]): PositionLevels | null {
  const [entry, stop, target] = points;
  if (!entry || !stop || !target) return null;
  return { entry: entry.price, stop: stop.price, target: target.price, start: entry.time, end: stop.time };
}

/** Quantity from the account size and the risk per trade: the loss at the stop is `riskPercent` of the account. */
export function positionQuantity(levels: Pick<PositionLevels, 'entry' | 'stop'>, accountSize: number, riskPercent: number, pointValue: number | null): number {
  const risk = Math.abs(levels.entry - levels.stop) * (pointValue ?? 1);
  if (!(risk > 0) || !(accountSize > 0) || !(riskPercent > 0)) return 0;
  return accountSize * riskPercent / 100 / risk;
}

export function riskReward(levels: Pick<PositionLevels, 'entry' | 'stop' | 'target'>): number | null {
  const risk = Math.abs(levels.entry - levels.stop);
  return risk > 0 ? Math.abs(levels.target - levels.entry) / risk : null;
}

export type PositionOutcome =
  | { state: 'waiting' }
  | { state: 'open'; price: number; pnl: number }
  | { state: 'ended'; price: number; pnl: number }
  | { state: 'closed'; at: 'target' | 'stop'; price: number; pnl: number };

/**
 * What happened to the position on the loaded bars, as its limit order would: it opens on the first bar from the
 * entry time whose range reaches the entry price, then closes at the target or the stop, whichever a bar reaches
 * first (the stop when one bar reaches both, as the cautious reading). Without either it is open at the last close,
 * or `ended` once bars pass the right edge. `pointValue` turns price into money (1 when unknown).
 */
export function positionOutcome(side: PositionSide, levels: PositionLevels, quantity: number, bars: DrawingBarSeries, pointValue: number | null = 1): PositionOutcome {
  const direction = (side === 'long' ? 1 : -1) * (pointValue ?? 1);
  const start = Date.parse(levels.start);
  const end = Date.parse(levels.end);
  const first = bars.indexAtOrBefore(levels.start);
  let index = first < 0 ? 0 : first;
  const firstBar = bars.at(index);
  if (firstBar && Date.parse(firstBar.time) < start) index += 1;
  let last: number | null = null;
  let opened = false;
  for (; index < bars.length; index += 1) {
    const bar = bars.at(index);
    if (!bar) break;
    if (Date.parse(bar.time) > end) return opened && last !== null ? { state: 'ended', price: last, pnl: (last - levels.entry) * direction * quantity } : { state: 'waiting' };
    if (!opened) {
      if (bar.low > levels.entry || bar.high < levels.entry) continue;
      opened = true;
    }
    const stopped = side === 'long' ? bar.low <= levels.stop : bar.high >= levels.stop;
    const reached = side === 'long' ? bar.high >= levels.target : bar.low <= levels.target;
    if (stopped) return { state: 'closed', at: 'stop', price: levels.stop, pnl: (levels.stop - levels.entry) * direction * quantity };
    if (reached) return { state: 'closed', at: 'target', price: levels.target, pnl: (levels.target - levels.entry) * direction * quantity };
    last = bar.close;
  }
  return last === null ? { state: 'waiting' } : { state: 'open', price: last, pnl: (last - levels.entry) * direction * quantity };
}

/** Stop and target for a new position: 1.5 average bar ranges of risk (1% without bars), twice that as reward. */
function defaultLevels(side: PositionSide, entry: DrawingPoint, services: DrawingToolServices): { stop: number; target: number } {
  const bars = services.bars;
  const last = bars.indexAtOrBefore(entry.time);
  let total = 0;
  let count = 0;
  for (let index = last; index >= 0 && count < 14; index -= 1) {
    const bar = bars.at(index);
    if (bar && Number.isFinite(bar.high - bar.low)) {
      total += bar.high - bar.low;
      count += 1;
    }
  }
  const tick = services.instrument.tickSize ?? 0;
  const risk = Math.max(tick, count > 0 && total > 0 ? 1.5 * total / count : Math.abs(entry.price) * 0.01);
  const direction = side === 'long' ? 1 : -1;
  return { stop: entry.price - direction * risk, target: entry.price + direction * 2 * risk };
}

function quantityText(quantity: number): string {
  return quantity.toLocaleString('en-US', { maximumFractionDigits: quantity >= 100 ? 0 : quantity >= 1 ? 2 : 6 });
}

function money(value: number, formatPrice: (price: number) => string): string {
  return `${value < 0 ? '-' : ''}${formatPrice(Math.abs(value))}`;
}

function percentOf(value: number, base: number): string {
  return base === 0 ? '0.00%' : `${(value / base * 100).toFixed(2)}%`;
}

function positionGeometry(side: PositionSide, context: DrawingGeometryContext): DrawingShape[] {
  const levels = positionLevels(context.rawPoints);
  if (!levels) return [];
  const [entryPoint, stopPoint, targetPoint] = context.points;
  const left = Math.min(entryPoint.x, stopPoint.x);
  const right = Math.max(entryPoint.x, stopPoint.x);
  const width = right - left;
  const center = left + width / 2;
  const accountSize = numberProperty(context.properties, 'accountSize', 1_000);
  const riskPercent = numberProperty(context.properties, 'riskPercent', 2);
  const quantity = positionQuantity(levels, accountSize, riskPercent, context.instrument.pointValue);
  const ratio = riskReward(levels);
  const tick = context.instrument.tickSize;
  const ticks = (from: number, to: number) => (tick ? ` ${Math.round(Math.abs(to - from) / tick).toLocaleString('en-US')}` : '');
  const box = (y1: number, y2: number, color: string): DrawingShape => ({ kind: 'rect', x: left, y: Math.min(y1, y2), width, height: Math.abs(y2 - y1), fill: color, fillOpacity: 0.2 });
  const label = (y: number, text: string, color: string, above: boolean): DrawingShape[] => {
    const labelWidth = Math.max(120, text.length * 6.6 + 16);
    const top = above ? y - 24 : y + 4;
    return [
      { kind: 'rect', x: center - labelWidth / 2, y: top, width: labelWidth, height: 20, radius: 3, fill: color, hit: 'none' },
      { kind: 'text', x: center, y: top + 14, text, align: 'middle', fontSize: 11, fill: '#ffffff', halo: false, hit: 'none' },
    ];
  };
  const targetAbove = targetPoint.y < entryPoint.y;
  const pointValue = context.instrument.pointValue ?? 1;
  const outcome = positionOutcome(side, levels, quantity, context.bars, pointValue);
  const shapes: DrawingShape[] = [
    box(entryPoint.y, targetPoint.y, PROFIT),
    box(entryPoint.y, stopPoint.y, LOSS),
    { kind: 'segment', x1: left, y1: entryPoint.y, x2: right, y2: entryPoint.y, stroke: '#787b86', strokeWidth: 1, hit: 'none' },
    ...label(targetPoint.y, `Target: ${context.formatPrice(levels.target)} (${percentOf(levels.target - levels.entry, levels.entry)})${ticks(levels.entry, levels.target)}, Amount: ${money((levels.target - levels.entry) * (side === 'long' ? 1 : -1) * quantity * pointValue, context.formatPrice)}`, PROFIT, targetAbove),
    ...label(stopPoint.y, `Stop: ${context.formatPrice(levels.stop)} (${percentOf(levels.stop - levels.entry, levels.entry)})${ticks(levels.entry, levels.stop)}, Amount: ${money((levels.stop - levels.entry) * (side === 'long' ? 1 : -1) * quantity * pointValue, context.formatPrice)}`, LOSS, !targetAbove),
  ];
  const result = outcome.state === 'waiting' ? null
    : outcome.state === 'open' ? `Open P&L: ${money(outcome.pnl, context.formatPrice)}`
      : outcome.state === 'ended' ? `P&L at end: ${money(outcome.pnl, context.formatPrice)}`
      : `Closed P&L: ${money(outcome.pnl, context.formatPrice)} (${outcome.at})`;
  const summary = [
    result,
    `Qty: ${quantityText(quantity)}`,
    `Risk/reward ratio: ${ratio === null ? '—' : ratio.toFixed(2)}`,
    `${formatSpan(Date.parse(levels.end) - Date.parse(levels.start))}`,
  ].filter(Boolean).join(' · ');
  const summaryWidth = Math.max(160, summary.length * 6.4 + 16);
  const summaryTop = entryPoint.y + (targetAbove ? 4 : -24);
  shapes.push(
    { kind: 'rect', x: center - summaryWidth / 2, y: summaryTop, width: summaryWidth, height: 20, radius: 3, fill: outcome.state === 'closed' ? (outcome.at === 'target' ? PROFIT : LOSS) : '#2a2e39', fillOpacity: 0.9, hit: 'none' },
    { kind: 'text', x: center, y: summaryTop + 14, text: summary, align: 'middle', fontSize: 11, fill: '#ffffff', halo: false, hit: 'none' },
  );
  return shapes;
}

/** `price` kept strictly on `side` of `entry` (`above` for a long's target and a short's stop). */
export function keepSide(price: number, entry: number, above: boolean, tick: number | null): number {
  const gap = tick ?? Math.max(Math.abs(entry) * 1e-6, 1e-9);
  return above ? Math.max(price, entry + gap) : Math.min(price, entry - gap);
}

/**
 * Handles: the entry (moves it in price and time), the stop and the target (price only, each kept on its side of
 * the entry), the right edge (time only).
 */
function positionHandles(side: PositionSide, context: DrawingGeometryContext): DrawingHandle[] {
  const [entry, stop, target] = context.points;
  if (!entry || !stop || !target) return [];
  const left = Math.min(entry.x, stop.x);
  const long = side === 'long';
  const tick = context.instrument.tickSize;
  return [
    {
      id: 'entry',
      x: left,
      y: entry.y,
      drag: ({ points, point }) => {
        // Between the stop and the target.
        const low = Math.min(points[1].price, points[2].price);
        const high = Math.max(points[1].price, points[2].price);
        const price = Math.min(keepSide(point.price, low, true, tick), keepSide(point.price, high, false, tick));
        return { points: [{ ...points[0], time: point.time, price }, points[1], points[2]] };
      },
    },
    { id: 'stop', x: left, y: stop.y, drag: ({ points, point }) => ({ points: [points[0], { ...points[1], price: keepSide(point.price, points[0].price, !long, tick) }, points[2]] }) },
    { id: 'target', x: left, y: target.y, drag: ({ points, point }) => ({ points: [points[0], points[1], { ...points[2], price: keepSide(point.price, points[0].price, long, tick) }] }) },
    {
      id: 'width',
      x: Math.max(entry.x, stop.x),
      y: entry.y,
      drag: ({ points, point }) => ({ points: [points[0], { ...points[1], time: point.time }, { ...points[2], time: point.time }] }),
    },
  ];
}

function positionTool<const Id extends string>(id: Id, side: PositionSide, label: string) {
  return defineDrawingTool({
    id,
    label,
    group: 'forecasting',
    creation: { gesture: 'click' },
    defaultProperties: { accountSize: 1_000, riskPercent: 2 },
    propertySchema: [
      { key: 'accountSize', label: 'Account size', type: 'number', min: 0, step: 100 },
      { key: 'riskPercent', label: 'Risk %', type: 'number', min: 0, max: 100, step: 0.25 },
    ],
    handles: (context) => positionHandles(side, context),
    onCreate: ([entry], services) => {
      const { stop, target } = defaultLevels(side, entry, services);
      const end = services.timeAfterBars(entry.time, 30) ?? new Date(Date.parse(entry.time) + 30 * 60_000).toISOString();
      return { points: [{ ...entry }, { time: end, price: stop }, { time: end, price: target }] };
    },
    geometry: (context) => positionGeometry(side, context),
    contextActions: [
      {
        id: 'order-ticket',
        label: side === 'long' ? 'Create buy order' : 'Create sell order',
        request: (drawing, services) => {
          const levels = positionLevels(drawing.points);
          const quantity = levels
            ? positionQuantity(levels, numberProperty(drawing.properties, 'accountSize', 1_000), numberProperty(drawing.properties, 'riskPercent', 2), services.instrument.pointValue)
            : 0;
          return {
            type: 'order-ticket',
            payload: { instrumentId: drawing.instrumentId, side: side === 'long' ? 'buy' : 'sell', entry: levels?.entry, stop: levels?.stop, target: levels?.target, quantity },
          };
        },
      },
    ],
  });
}

export const longPositionTool = positionTool('long-position', 'long', 'Long position');
export const shortPositionTool = positionTool('short-position', 'short', 'Short position');
