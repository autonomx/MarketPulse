import { describe, expect, it } from 'vitest';
import { alertLevelPriceAt } from '../alertLevels';
import { drawingToolDefinition, drawingPropertiesWithDefaults } from '../registry';
import { pointAt, runTool, testServices } from '../../../../../test/drawingTools';
import type { DrawingShape } from '../types';

const segments = (shapes: readonly DrawingShape[]) => shapes.filter((shape): shape is Extract<DrawingShape, { kind: 'segment' }> => shape.kind === 'segment');
const texts = (shapes: readonly DrawingShape[]) => shapes.flatMap((shape) => (shape.kind === 'text' ? [shape.text] : []));
const round = (value: number) => Math.round(value * 1000) / 1000;
const alertLevels = (toolId: string, pixels: readonly [number, number][], properties = {}) => {
  const definition = drawingToolDefinition(toolId)!;
  return definition.alertLevels!(pixels.map(([x, y]) => pointAt(x, y)), drawingPropertiesWithDefaults(toolId, properties), testServices);
};

describe('Fibonacci tools (TVP-3.2)', () => {
  it('trend-based fib extension projects A to B from C, and alerts on each visible level', () => {
    // A at price 500, B at 700, C at 600: level 1 is 800 (y 200), level 0.618 is 723.6.
    const { shapes } = runTool('fib-extension', [[100, 500], [200, 300], [300, 400]]);
    const horizontal = segments(shapes).filter((shape) => shape.y1 === shape.y2);
    expect(horizontal.map((shape) => round(shape.y1))).toEqual([400, 352.8, 323.6, 300, 276.4, 242.8, 200, 76.4, -123.6]);
    expect(horizontal[0]).toMatchObject({ x1: 300, x2: 400 });
    expect(texts(shapes)).toContain('1 (800)');
    const levels = alertLevels('fib-extension', [[100, 500], [200, 300], [300, 400]]);
    expect(levels.map((level) => level.key)).toContain('level-1.618');
    expect(levels.find((level) => level.key === 'level-1')?.anchors[0]).toMatchObject({ time: pointAt(300, 0).time, price: 800 });
    expect(levels.every((level) => level.extend === 'right')).toBe(true);
  });

  it('fib time zone draws verticals at the Fibonacci multiples of A-B in bars', () => {
    const { shapes } = runTool('fib-time-zone', [[100, 300], [110, 300]]);
    expect(segments(shapes).map((shape) => round(shape.x1))).toEqual([100, 110, 120, 130, 150, 180, 230, 310, 440, 650]);
    expect(segments(shapes)[0]).toMatchObject({ y1: 0, y2: 600 });
    const zero = runTool('fib-time-zone', [[100, 300], [100, 200]]);
    expect(segments(zero.shapes)).toHaveLength(1);
  });

  it('trend-based fib time measures A-B in bars from C', () => {
    const { shapes } = runTool('fib-time', [[0, 300], [10, 200], [100, 250]], { properties: { levels: [{ value: 0, color: '', visible: true }, { value: 1.618, color: '', visible: true }] } });
    expect(segments(shapes).filter((shape) => shape.y1 === 0).map((shape) => round(shape.x1))).toEqual([100, 116.18]);
  });

  it('fib channel draws parallels at the levels of the width set by C, and alerts on them', () => {
    // Base 100,500 -> 200,400; C one width (price +50) above at A's time.
    const { shapes } = runTool('fib-channel', [[100, 500], [200, 400], [100, 450]], {
      properties: { levels: [{ value: 0, color: '', visible: true }, { value: 1, color: '', visible: true }, { value: 2, color: '', visible: false }] },
    });
    expect(segments(shapes).map((shape) => [shape.x1, shape.y1, shape.x2, shape.y2])).toEqual([[100, 500, 200, 400], [100, 450, 200, 350]]);
    const levels = alertLevels('fib-channel', [[100, 500], [200, 400], [100, 450]]);
    expect(levels.find((level) => level.key === 'level-1')?.anchors.map((anchor) => anchor.price)).toEqual([550, 650]);
  });

  it('fib speed resistance fan draws rays from A through the box divisions', () => {
    const { shapes } = runTool('fib-speed-fan', [[100, 400], [200, 300]]);
    expect(segments(shapes).filter((shape) => shape.hit !== 'none')).toHaveLength(11);
    expect(shapes.some((shape) => shape.kind === 'rect')).toBe(true);
  });

  it('arcs, circles, spiral and wedge scale with their anchors', () => {
    const arcs = runTool('fib-arcs', [[300, 300], [300, 200]]);
    const halves = arcs.shapes.filter((shape) => shape.kind === 'path');
    expect(halves).toHaveLength(6);
    // Opening upwards (towards B): the largest arc passes through B.
    expect(arcs.hit(300, 200)).not.toBeNull();
    expect(arcs.hit(300, 400)).toBeNull();
    const circles = runTool('fib-circles', [[200, 300], [400, 300]]);
    expect(circles.shapes.filter((shape) => shape.kind === 'ellipse').map((shape) => (shape.kind === 'ellipse' ? round(shape.rx) : 0))).toContain(100);
    const spiral = runTool('fib-spiral', [[300, 300], [350, 300]]);
    expect(spiral.shapes.some((shape) => shape.kind === 'polyline' && shape.points.length > 50)).toBe(true);
    const wedge = runTool('fib-wedge', [[100, 300], [300, 300], [300, 100]]);
    expect(wedge.shapes.filter((shape) => shape.kind === 'path')).toHaveLength(6);
    expect(wedge.hit(200, 300)).not.toBeNull();
  });
});

describe('pitchforks and Gann (TVP-3.3)', () => {
  it('pitchfork: median from A through the middle of B-C, tines through B and C', () => {
    const { shapes } = runTool('pitchfork', [[100, 300], [200, 200], [200, 400]]);
    const lines = segments(shapes).map((shape) => [round(shape.x1), round(shape.y1), round(shape.x2), round(shape.y2)]);
    expect(lines).toContainEqual([100, 300, 800, 300]);
    expect(lines).toContainEqual([200, 200, 800, 200]);
    expect(lines).toContainEqual([200, 400, 800, 400]);
    expect(lines).toContainEqual([200, 250, 800, 250]);
    expect(shapes.some((shape) => shape.kind === 'polygon')).toBe(true);
  });

  it('Schiff and modified Schiff move the origin halfway towards B', () => {
    const schiff = segments(runTool('schiff-pitchfork', [[100, 300], [200, 200], [200, 400]]).shapes);
    expect(schiff.some((shape) => shape.x1 === 100 && shape.y1 === 250 && shape.x2 > 200)).toBe(true);
    const modified = segments(runTool('modified-schiff-pitchfork', [[100, 300], [200, 200], [200, 400]]).shapes);
    expect(modified.some((shape) => round(shape.x1) === 150 && round(shape.y1) === 250 && shape.x2 > 200)).toBe(true);
  });

  it('the inside pitchfork starts halfway between A and B in price and time, as TradingView documents it', () => {
    const inside = segments(runTool('inside-pitchfork', [[100, 300], [200, 200], [200, 400]]).shapes);
    expect(inside.some((shape) => round(shape.x1) === 150 && round(shape.y1) === 250 && shape.x2 > 200)).toBe(true);
    expect(inside.some((shape) => shape.x1 === 100 && shape.y1 === 300 && round(shape.x2) === 150 && shape.dash)).toBe(true);
    expect(alertLevels('inside-pitchfork', [[100, 300], [200, 200], [200, 400]]).map((level) => level.key)[0]).toBe('median');
  });

  it('pitchfork alerts on the median and each visible tine, as the lines are drawn', () => {
    const levels = alertLevels('pitchfork', [[100, 300], [200, 200], [200, 400]]);
    expect(levels.map((level) => level.key)).toEqual(['median', 'level-0.5-upper', 'level-0.5-lower', 'level-1-upper', 'level-1-lower']);
    expect(levels[0].anchors.map((anchor) => anchor.price)).toEqual([700, 700]);
    expect(levels.find((level) => level.key === 'level-1-upper')?.anchors.map((anchor) => anchor.price)).toEqual([800, 800]);
    expect(levels.every((level) => level.extend === 'right')).toBe(true);
  });

  it('pitchfan: rays from A through B-C at each level', () => {
    const { shapes } = runTool('pitchfan', [[100, 300], [200, 200], [200, 400]]);
    expect(segments(shapes)).toHaveLength(8);
  });

  it('Gann box: price and time levels across the box; Shift keeps it square', () => {
    const { shapes } = runTool('gann-box', [[100, 100], [300, 300]]);
    expect(segments(shapes)).toHaveLength(14);
    expect(texts(shapes)).toContain('0.618');
    const square = drawingToolDefinition('gann-box')!.constrain!({ x: 300, y: 200 }, [{ x: 100, y: 100 }], { shift: true, alt: false, ctrl: false });
    expect(square).toEqual({ x: 300, y: 300 });
  });

  it('Gann square keeps square only with Shift', () => {
    const none = { shift: false, alt: false, ctrl: false };
    expect(drawingToolDefinition('gann-square')!.constrain!({ x: 300, y: 200 }, [{ x: 100, y: 100 }], none)).toEqual({ x: 300, y: 200 });
    const { shapes } = runTool('gann-square', [[100, 100], [300, 300]]);
    expect(shapes.filter((shape) => shape.kind === 'polyline')).toHaveLength(6);
  });

  it('Gann fan: nine angles, 1x1 through B', () => {
    const { shapes } = runTool('gann-fan', [[100, 400], [200, 300]]);
    const rays = segments(shapes);
    expect(rays).toHaveLength(9);
    const oneByOne = rays[4];
    // Through B: the 1x1 ray keeps B's slope.
    expect((oneByOne.y2 - oneByOne.y1) / (oneByOne.x2 - oneByOne.x1)).toBeCloseTo(-1);
    expect(texts(shapes)).toEqual(['1x8', '1x4', '1x3', '1x2', '1x1', '2x1', '3x1', '4x1', '8x1']);
  });
});

describe('TVP-3.2/3.3 review fixes', () => {
  // A log price scale: equal price ratios are equal distances.
  const logProject = (point: { time: string; price: number }) => {
    const index = testServices.barIndexForTime(point.time);
    return index === null || point.price <= 0 ? null : { x: index, y: 1000 - 200 * Math.log(point.price) };
  };
  const distanceToLine = (point: { x: number; y: number }, shape: DrawingShape) => {
    if (shape.kind !== 'segment') return Number.POSITIVE_INFINITY;
    const dx = shape.x2 - shape.x1;
    const dy = shape.y2 - shape.y1;
    return Math.abs(dy * (point.x - shape.x1) - dx * (point.y - shape.y1)) / Math.hypot(dx, dy);
  };

  it('pitchfork tines are drawn through the lines they alert on, on a log scale too', () => {
    // A at price 60, B at 120, C at 80 (y = 1000 - price in pixels for the anchors).
    const pixels: [number, number][] = [[100, 940], [200, 880], [260, 920]];
    const { shapes } = runTool('pitchfork', pixels, { access: { project: logProject } });
    const levels = alertLevels('pitchfork', pixels);
    const upper = levels.find((item) => item.key === 'level-1-upper')!;
    expect(upper.label).toBe('B side 1');
    // The B-side tine at level 1 starts at B and passes, one median length (130 bars: A to M) later, through the alert line.
    const at = (index: number) => logProject({ time: testServices.timeForBarIndex(index)!, price: alertLevelPriceAt(upper, index, testServices.barIndexForTime, true)! })!;
    const start = at(200);
    const tine = segments(shapes).find((shape) => Math.abs(shape.x1 - start.x) < 1e-6 && Math.abs(shape.y1 - start.y) < 1e-6 && shape.x2 > 300)!;
    expect(tine).toBeDefined();
    expect(distanceToLine(at(330), tine)).toBeLessThan(0.01);
  });

  it('fib channel keeps its base line when it has no width', () => {
    const vertical = runTool('fib-channel', [[100, 500], [100, 400], [150, 450]]);
    expect(segments(vertical.shapes)).toHaveLength(1);
    const noBars = runTool('fib-channel', [[100, 500], [200, 400], [100, 450]], { access: { barIndexForTime: () => null } });
    expect(segments(noBars.shapes)).toHaveLength(1);
  });

  it('extended levels label at the edge, reading inwards', () => {
    const { shapes } = runTool('fib-extension', [[100, 500], [200, 300], [300, 400]], { properties: { extendRight: true } });
    const labels = shapes.filter((shape) => shape.kind === 'text');
    expect(labels.length).toBeGreaterThan(0);
    expect(labels.every((shape) => shape.kind === 'text' && shape.align === 'end' && shape.x <= 800)).toBe(true);
  });

  it('the spiral reaches the view from an off-screen centre, and a click without a drag still shows', () => {
    const far = runTool('fib-spiral', [[-3000, 300], [-2950, 300]]);
    const spiral = far.shapes.find((shape) => shape.kind === 'polyline');
    // Its arm reaches past the view's farthest corner from the centre.
    const reach = spiral && spiral.kind === 'polyline' ? Math.max(...spiral.points.map((point) => Math.hypot(point.x + 3000, point.y - 300))) : 0;
    expect(reach).toBeGreaterThan(Math.hypot(3800, 300));
    expect(runTool('fib-spiral', [[300, 300], [300, 300]]).shapes.length).toBeGreaterThan(0);
  });

  it('Gann square fixed keeps the price per bar it was drawn with', () => {
    const definition = drawingToolDefinition('gann-square-fixed')!;
    const created = definition.onCreate!([pointAt(100, 500), pointAt(200, 400)], testServices);
    expect(created.properties).toEqual({ pricePerBar: 1 });
    // Moved to 50 bars wide, it is 50 price units tall, whatever B's price.
    const { shapes } = runTool('gann-square-fixed', [[100, 500], [150, 100]], { properties: { pricePerBar: 1 } });
    const box = shapes.find((shape) => shape.kind === 'rect');
    expect(box && box.kind === 'rect' && [box.width, box.height]).toEqual([50, 50]);
  });

  it('Gann fan angles run from flattest to steepest', () => {
    const rays = segments(runTool('gann-fan', [[100, 400], [200, 300]]).shapes);
    const slopes = rays.map((ray) => Math.abs((ray.y2 - ray.y1) / (ray.x2 - ray.x1)));
    expect([...slopes].sort((a, b) => a - b)).toEqual(slopes);
    expect(slopes[0]).toBeCloseTo(1 / 8);
    expect(slopes[8]).toBeCloseTo(8);
  });

  it('fib retracement: reverse, prices or percents, labels on the left', () => {
    const zero = [{ value: 0, color: '', visible: true }];
    expect(segments(runTool('fibonacci', [[100, 500], [200, 300]], { properties: { levels: zero } }).shapes)[0].y1).toBe(500);
    expect(segments(runTool('fibonacci', [[100, 500], [200, 300]], { properties: { reverse: true, levels: zero } }).shapes)[0].y1).toBe(300);
    const prices = runTool('fibonacci', [[100, 500], [200, 300]], { properties: { labelContent: 'prices', labelSide: 'left', levels: [{ value: 0.5, color: '', visible: true }] } });
    const text = prices.shapes.find((shape) => shape.kind === 'text');
    expect(text && text.kind === 'text' && [text.text, text.align, text.x]).toEqual(['600', 'end', 96]);
    const percents = runTool('fibonacci', [[100, 500], [200, 300]], { properties: { labelContent: 'percents', levels: [{ value: 0.618, color: '', visible: true }] } });
    expect(texts(percents.shapes)).toEqual(['61.8%']);
    const reversedAlerts = alertLevels('fibonacci', [[100, 500], [200, 300]], { reverse: true, levels: zero });
    expect(reversedAlerts[0].anchors[0].price).toBe(700);
  });

  it('tools that measure in bars still draw something without bars', () => {
    const access = { access: { barIndexForTime: () => null, timeForBarIndex: () => null } };
    expect(runTool('fib-time-zone', [[100, 300], [110, 300]], access).shapes.length).toBeGreaterThan(0);
    expect(runTool('fib-time', [[0, 300], [10, 200], [100, 250]], access).shapes.length).toBeGreaterThan(0);
    expect(runTool('pitchfork', [[100, 300], [200, 200], [200, 400]], access).shapes.length).toBeGreaterThan(0);
  });
});

describe('TVP-3.2/3.3 follow-ups', () => {
  it('Gann square fixed: B\'s handle sits on the corner, a drag keeps the scale, no scale keeps B', () => {
    const { context } = runTool('gann-square-fixed', [[100, 500], [150, 100]], { properties: { pricePerBar: 1 } });
    const definition = drawingToolDefinition('gann-square-fixed')!;
    const handles = (definition.handles as (c: typeof context) => readonly { id: string; x: number; y: number; drag: (input: never) => { points?: readonly { price: number }[] } }[])(context!);
    const corner = handles.find((handle) => handle.id === 'anchor-1')!;
    expect([corner.x, corner.y]).toEqual([150, 450]);
    const patch = corner.drag({ points: context!.rawPoints, point: pointAt(200, 10), properties: { pricePerBar: 1 }, services: testServices, screen: { x: 200, y: 10 }, modifiers: { shift: false, alt: false, ctrl: false } } as never);
    // 100 bars from A at 1 per bar: 100 above A's 500.
    expect(patch.points?.[1].price).toBe(600);
    const unscaled = runTool('gann-square-fixed', [[100, 500], [150, 400]], { properties: { pricePerBar: 0 } }).shapes.find((shape) => shape.kind === 'rect');
    expect(unscaled && unscaled.kind === 'rect' && [unscaled.width, unscaled.height]).toEqual([50, 100]);
  });

  it('retracement labels on the left read inwards when the levels are extended left', () => {
    const { shapes } = runTool('fibonacci', [[100, 500], [200, 300]], { properties: { labelSide: 'left', extendLeft: true, levels: [{ value: 0.5, color: '', visible: true }] } });
    const text = shapes.find((shape) => shape.kind === 'text');
    expect(text && text.kind === 'text' && [text.x, text.align]).toEqual([4, 'start']);
  });

  it('a spiral far from its centre is sampled finely', () => {
    const { shapes } = runTool('fib-spiral', [[-3000, 300], [-2950, 300]]);
    const spiral = shapes.find((shape) => shape.kind === 'polyline');
    const points = spiral && spiral.kind === 'polyline' ? spiral.points : [];
    // Fine steps where the arm can cross the view (coarse ones further from it stay off-screen).
    const near = (point: { x: number; y: number }) => point.x > -100 && point.x < 900 && point.y > -100 && point.y < 700;
    const longest = Math.max(...points.slice(1).flatMap((point, index) => (near(point) && near(points[index]) ? [Math.hypot(point.x - points[index].x, point.y - points[index].y)] : [])));
    expect(longest).toBeLessThan(8);
    // A centre much further off still reaches across the view within the point cap.
    const farther = runTool('fib-spiral', [[-20000, 300], [-19950, 300]]).shapes.find((shape) => shape.kind === 'polyline');
    const reach = farther && farther.kind === 'polyline' ? Math.max(...farther.points.map((point) => Math.hypot(point.x + 20000, point.y - 300))) : 0;
    expect(reach).toBeGreaterThan(Math.hypot(20800, 300));
  });
});
