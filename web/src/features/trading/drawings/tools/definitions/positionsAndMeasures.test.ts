import { describe, expect, it } from 'vitest';
import { parsePaperTicketRequest } from '../../../paperTicketRequests';
import { drawingPropertiesWithDefaults, drawingToolDefinition } from '../registry';
import { pointAt, runTool, testServices } from '../../../../../test/drawingTools';
import type { DrawingBarSeries, DrawingProperties, DrawingShape, DrawingToolServices } from '../types';
import { keepSide, positionLevels, positionOutcome, positionQuantity, riskReward } from './positions';
import { MAX_GHOST_CANDLES, forecastState, ghostCandles, patternBars, sectorPoints } from './projections';
import { formatSpan, formatVolume, rangeStats, volumeBetween } from './ranges';
import { rangeVolumeProfile } from './volumeProfile';

function only<K extends DrawingShape['kind']>(shapes: DrawingShape[], kind: K): Extract<DrawingShape, { kind: K }>[] {
  return shapes.filter((shape): shape is Extract<DrawingShape, { kind: K }> => shape.kind === kind);
}

const BASE = Date.parse(pointAt(0, 0).time);
type Bar = { open: number; high: number; low: number; close: number; volume?: number };

/** One-minute bars from the test projector's base time. */
function series(bars: readonly Bar[]): DrawingBarSeries {
  return {
    length: bars.length,
    at: (index) => (index >= 0 && index < bars.length ? { time: new Date(BASE + index * 60_000).toISOString(), volume: 1, ...bars[index] } : undefined),
    indexAtOrBefore: (time) => Math.min(bars.length - 1, Math.max(-1, Math.floor((Date.parse(time) - BASE) / 60_000))),
  };
}

const flat = (count: number, price = 100): Bar[] => Array.from({ length: count }, () => ({ open: price, high: price + 1, low: price - 1, close: price }));
const services = (bars: DrawingBarSeries, tickSize: number | null = 0.5): DrawingToolServices => ({ ...testServices, bars, instrument: { tickSize, pointValue: 1 } });

describe('long and short position (TVP-3.6)', () => {
  it('sizes the position from the account and risk, and computes risk/reward', () => {
    const levels = { entry: 100, stop: 95, target: 110 };
    // 2% of 1000 is 20 at risk; 5 per unit -> 4 units.
    expect(positionQuantity(levels, 1_000, 2, 1)).toBe(4);
    expect(riskReward(levels)).toBe(2);
    expect(positionQuantity({ entry: 100, stop: 100 }, 1_000, 2, 1)).toBe(0);
  });

  it('opens on the first bar from the entry and closes at the stop or the target first reached', () => {
    const levels = { entry: 100, stop: 95, target: 110, start: pointAt(1, 0).time, end: pointAt(10, 0).time };
    const rising = series([...flat(2), { open: 100, high: 106, low: 99, close: 105 }, { open: 105, high: 111, low: 104, close: 110 }]);
    expect(positionOutcome('long', levels, 4, rising)).toEqual({ state: 'closed', at: 'target', price: 110, pnl: 40 });
    expect(positionOutcome('long', levels, 4, series([...flat(2), { open: 100, high: 103, low: 99, close: 102 }]))).toEqual({ state: 'open', price: 102, pnl: 8 });
    // A bar reaching both: the stop (the cautious reading).
    expect(positionOutcome('long', levels, 4, series([...flat(2), { open: 100, high: 111, low: 94, close: 100 }]))).toMatchObject({ at: 'stop', pnl: -20 });
    expect(positionOutcome('short', { ...levels, stop: 105, target: 90 }, 2, series([...flat(2), { open: 100, high: 101, low: 89, close: 90 }]))).toEqual({ state: 'closed', at: 'target', price: 90, pnl: 20 });
    // Before the entry time there is nothing yet.
    expect(positionOutcome('long', { ...levels, start: pointAt(50, 0).time, end: pointAt(60, 0).time }, 4, rising)).toEqual({ state: 'waiting' });
  });

  it('opens only once a bar reaches the entry, and ends at the right edge', () => {
    const levels = { entry: 90, stop: 85, target: 110, start: pointAt(1, 0).time, end: pointAt(3, 0).time };
    // Price stays around 100: the limit at 90 never fills.
    expect(positionOutcome('long', levels, 4, series(flat(10)))).toEqual({ state: 'waiting' });
    const dip = series([...flat(2), { open: 95, high: 96, low: 89, close: 92 }, { open: 92, high: 95, low: 91, close: 94 }, ...flat(5)]);
    expect(positionOutcome('long', levels, 4, dip)).toEqual({ state: 'ended', price: 94, pnl: 16 });
    // The point value turns price into money.
    // Still inside its window at the last loaded bar: open, at the last close.
    expect(positionOutcome('long', { ...levels, end: pointAt(100, 0).time }, 4, dip, 10)).toEqual({ state: 'open', price: 100, pnl: 400 });
  });

  it('keeps the stop and target on their side of the entry while dragged', () => {
    expect(keepSide(105, 100, false, 0.5)).toBe(99.5);
    expect(keepSide(95, 100, false, 0.5)).toBe(95);
    expect(keepSide(99, 100, true, null)).toBeGreaterThan(100);
  });

  it('one click creates entry, stop and target from the recent bar ranges', () => {
    const definition = drawingToolDefinition('long-position')!;
    // Bar ranges of 2: risk 1.5 * 2 = 3 below the entry, reward 6 above.
    const created = definition.onCreate!([pointAt(20, 900)], services(series(flat(30))));
    expect(positionLevels(created.points)).toMatchObject({ entry: 100, stop: 97, target: 106 });
    expect(created.points[1].time).toBe(pointAt(50, 0).time);
    const short = drawingToolDefinition('short-position')!.onCreate!([pointAt(20, 900)], services(series(flat(30))));
    expect(positionLevels(short.points)).toMatchObject({ stop: 103, target: 94 });
  });

  it('draws target and stop zones with labels, and the order ticket request carries the levels', () => {
    const points: [number, number][] = [[10, 900], [40, 905], [40, 890]];
    const { shapes, context } = runTool('long-position', points, { access: services(series(flat(5))) });
    const texts = only(shapes, 'text').map((shape) => shape.text);
    expect(texts[0]).toBe('Target: 110 (10.00%) 20, Amount: 40');
    expect(texts[1]).toBe('Stop: 95 (-5.00%) 10, Amount: -20');
    expect(texts[2]).toContain('Qty: 4');
    expect(texts[2]).toContain('Risk/reward ratio: 2.00');
    const handles = drawingToolDefinition('long-position')!.handles as unknown as (c: unknown) => { id: string; x: number; y: number }[];
    expect(handles(context).map((handle) => [handle.id, handle.x, handle.y])).toEqual([['entry', 10, 900], ['stop', 10, 905], ['target', 10, 890], ['width', 40, 900]]);
    const [action] = drawingToolDefinition('long-position')!.contextActions!;
    const request = action.request(
      { drawingId: 'p', instrumentId: 'crypto:BTC', points: points.map(([x, y]) => pointAt(x, y)), properties: drawingPropertiesWithDefaults('long-position', {}), text: '' },
      services(series(flat(5))),
    );
    expect(request).toEqual({ type: 'order-ticket', payload: { instrumentId: 'crypto:BTC', side: 'buy', entry: 100, stop: 95, target: 110, quantity: 4 } });
    expect(parsePaperTicketRequest(request.payload)).toEqual({ instrumentId: 'crypto:BTC', side: 'buy', orderType: 'limit', entry: 100, stop: 95, target: 110, quantity: 4 });
    expect(parsePaperTicketRequest({ side: 'buy', entry: 100 })).toBeNull();
  });
});

describe('measurers (TVP-3.6)', () => {
  it('date range and date-and-price range show bars, span, volume, change and ticks', () => {
    const bars = series(flat(100).map((bar, index) => ({ ...bar, volume: index < 50 ? 1_000 : 0 })));
    const stats = rangeStats(pointAt(10, 900), pointAt(40, 880), services(bars));
    expect(stats).toMatchObject({ delta: 20, ticks: 40, bars: 30, span: 30 * 60_000, volume: 31_000 });
    expect(only(runTool('date-range', [[10, 900], [40, 880]], { access: services(bars) }).shapes, 'text').map((shape) => shape.text)).toEqual(['30 bars, 30m', 'Vol 31K']);
    expect(only(runTool('date-price-range', [[10, 900], [40, 880]], { access: services(bars) }).shapes, 'text').map((shape) => shape.text))
      .toEqual(['20 (20.00%) 40', '30 bars, 30m', 'Vol 31K']);
    expect(volumeBetween(bars, pointAt(60, 0).time, pointAt(55, 0).time)).toBe(0);
  });

  it('formats spans and volumes like TradingView', () => {
    expect(formatSpan(3 * 86_400_000 + 4 * 3_600_000 + 5 * 60_000)).toBe('3d 4h');
    expect(formatSpan(135 * 60_000)).toBe('2h 15m');
    expect(formatSpan(30_000)).toBe('30s');
    expect(formatVolume(1_234)).toBe('1.23K');
    expect(formatVolume(2_500_000)).toBe('2.5M');
  });
});

describe('forecasting (TVP-3.6)', () => {
  it('position forecast: success when the target price is reached by the target time', () => {
    const bars = series([...flat(5), { open: 100, high: 112, low: 99, close: 111 }, ...flat(10)]);
    expect(forecastState(pointAt(2, 900), pointAt(8, 890), bars)).toBe('success');
    expect(forecastState(pointAt(2, 900), pointAt(4, 890), bars)).toBe('failure');
    expect(forecastState(pointAt(10, 900), pointAt(40, 880), bars)).toBe('in-progress');
    expect(only(runTool('position-forecast', [[2, 900], [8, 890]], { access: services(bars, null) }).shapes, 'text')[0].text).toBe('10 (10.00%) · Success');
  });

  it('bars pattern copies its source bars to where it is placed, optionally flipped', () => {
    const source = [{ open: 100, high: 104, low: 99, close: 103 }, { open: 103, high: 106, low: 102, close: 105 }];
    const bars = series([...flat(5), ...source, ...flat(20)]);
    expect(patternBars(bars, pointAt(5, 0).time, pointAt(6, 0).time).map((bar) => bar.close)).toEqual([103, 105]);
    const created = drawingToolDefinition('bars-pattern')!.onCreate!([pointAt(5, 950), pointAt(6, 950)], services(bars));
    expect(created.points[0].price).toBe(100);
    expect(created.properties).toEqual({ sourceFrom: pointAt(5, 0).time, sourceTo: pointAt(6, 0).time });
    // Placed 10 bars later and 50 higher: the copy's first body runs 150 -> 153 (y 850 -> 847).
    const placed = [pointAt(15, 850), pointAt(16, 850)];
    const run = (properties: DrawingProperties) => runTool('bars-pattern', [[15, 850], [16, 850]], {
      access: services(bars), properties: { ...(created.properties ?? {}), ...properties },
    }).shapes;
    expect(placed[0].price).toBe(150);
    const [, firstBody] = run({});
    expect(firstBody).toMatchObject({ kind: 'rect', x: 14.5, y: 847, height: 3 });
    const flippedBodies = only(run({ flipped: true }), 'rect');
    expect(flippedBodies[0]).toMatchObject({ y: 850, height: 3 });
    expect(only(run({ mode: 'line' }), 'polyline')[0].points.map((point) => point.y)).toEqual([847, 845]);
  });

  it('ghost feed draws a candle per bar along the sketched path', () => {
    const candles = ghostCandles([pointAt(0, 900), pointAt(4, 880), pointAt(6, 890)], () => 0);
    expect(candles).toHaveLength(2);
    const many = ghostCandles([pointAt(0, 900), pointAt(4, 880)], () => 4);
    expect(many.map((candle) => candle.close)).toEqual([105, 110, 115, 120]);
    expect(many[0].open).toBe(100);
    expect(many.every((candle) => candle.high >= Math.max(candle.open, candle.close) && candle.low <= Math.min(candle.open, candle.close))).toBe(true);
    expect(only(runTool('ghost-feed', [[0, 900], [4, 880]]).shapes, 'rect')).toHaveLength(4);
    // A path far ahead on a lower interval stays bounded.
    expect(ghostCandles([pointAt(0, 900), pointAt(100_000, 880)], () => 100_000)).toHaveLength(MAX_GHOST_CANDLES);
  });

  it('sector spans from the first radius to the second direction', () => {
    const points = sectorPoints({ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 5 });
    expect(points[0]).toEqual({ x: 0, y: 0 });
    expect(points[1]).toEqual({ x: 10, y: 0 });
    expect(points.at(-1)!.x).toBeCloseTo(0, 9);
    expect(points.at(-1)!.y).toBeCloseTo(10, 9);
  });
});

describe('fixed range volume profile (TVP-3.6)', () => {
  it('profiles the bars in the range with the indicator\'s computation', () => {
    const bars = series([...flat(5, 100).map((bar) => ({ ...bar, volume: 10 })), ...flat(5, 120).map((bar) => ({ ...bar, volume: 30 }))]);
    const profile = rangeVolumeProfile(bars, pointAt(0, 0).time, pointAt(9, 0).time)!;
    expect(profile.poc).toBeGreaterThan(115);
    expect(profile.bins.reduce((sum, bin) => sum + bin.volume, 0)).toBe(200);
    expect(rangeVolumeProfile(bars, pointAt(50, 0).time, pointAt(60, 0).time)).toBeNull();
    const shapes = runTool('fixed-range-volume-profile', [[0, 900], [9, 870]], { access: services(bars) }).shapes;
    expect(only(shapes, 'rect').length).toBeGreaterThan(2);
    expect(only(shapes, 'segment')).toHaveLength(1);
  });
});
