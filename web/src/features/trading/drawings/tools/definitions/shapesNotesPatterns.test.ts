import { describe, expect, it } from 'vitest';
import { runTool } from '../../../../../test/drawingTools';
import type { DrawingShape } from '../types';
import { arcThrough, circleThrough, rotatedRectangleCorners, smoothPath } from './shapesExtra';
import { intersection, moveRatio } from './patterns';
import { textBoxWidth } from './annotations';

const kinds = (shapes: readonly DrawingShape[]) => shapes.map((shape) => shape.kind);
const texts = (shapes: readonly DrawingShape[]) => shapes.flatMap((shape) => (shape.kind === 'text' ? [shape.text] : []));
const round = (value: number) => Math.round(value * 1000) / 1000;

describe('shapes and freehand (TVP-3.4)', () => {
  it('brush and highlighter draw a smoothed stroke through their points', () => {
    const points = [[100, 100], [120, 140], [160, 120], [200, 180]] as [number, number][];
    const brush = runTool('brush', points);
    expect(kinds(brush.shapes)).toEqual(['path']);
    expect(brush.hit(100, 100)).not.toBeNull();
    const highlighter = runTool('highlighter', points);
    expect(highlighter.shapes[0]).toMatchObject({ kind: 'path', strokeWidth: 14, opacity: 0.35 });
    expect(smoothPath([{ x: 0, y: 0 }, { x: 10, y: 0 }])).toEqual([{ op: 'M', x: 0, y: 0 }, { op: 'L', x: 10, y: 0 }]);
    const path = smoothPath([{ x: 0, y: 0 }, { x: 10, y: 10 }, { x: 20, y: 0 }, { x: 30, y: 10 }]);
    // Ends exactly at the last point.
    expect(path[path.length - 1]).toMatchObject({ x: 30, y: 10 });
  });

  it('path ends in an arrow; polyline closes into a filled shape', () => {
    const path = runTool('path', [[100, 100], [200, 150], [300, 100]]);
    expect(kinds(path.shapes)).toEqual(['polyline', 'polygon']);
    const polyline = runTool('polyline', [[100, 100], [200, 150], [300, 100]]);
    expect(kinds(polyline.shapes)).toEqual(['polygon']);
    expect(polyline.hit(200, 120)).not.toBeNull();
    const open = runTool('polyline', [[100, 100], [200, 150], [300, 100]], { properties: { closed: false } });
    expect(kinds(open.shapes)).toEqual(['polyline']);
  });

  it('curve passes through its bend point; the double curve crosses its chord', () => {
    const curve = runTool('curve', [[100, 300], [300, 300], [200, 200]]);
    const [shape] = curve.shapes;
    expect(shape.kind).toBe('path');
    // The quadratic's middle is at the bend point.
    expect(curve.hit(200, 200)).not.toBeNull();
    const double = runTool('double-curve', [[100, 300], [300, 300], [150, 250]]);
    expect(double.hit(200, 300)).not.toBeNull();
  });

  it('triangle, rotated rectangle and arc', () => {
    expect(runTool('triangle', [[100, 100], [200, 300], [300, 100]]).hit(200, 150)).not.toBeNull();
    expect(rotatedRectangleCorners([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: 40 }]).map(({ x, y }) => [round(x), round(y)])).toEqual([[0, 0], [100, 0], [100, 40], [0, 40]]);
    const circle = circleThrough({ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: 50 });
    expect(circle && [round(circle.x), round(circle.y), round(circle.r)]).toEqual([50, 0, 50]);
    // From (0,0) to (100,0) through (50,50): the lower half of the circle.
    const arc = arcThrough({ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: 50 });
    expect(Math.max(...arc.map((point) => point.y))).toBeCloseTo(50);
    const upper = arcThrough({ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 50, y: -50 });
    expect(Math.min(...upper.map((point) => point.y))).toBeCloseTo(-50);
    expect(runTool('arc', [[100, 300], [300, 300], [200, 250]]).hit(200, 260)).not.toBeNull();
  });
});

describe('annotations (TVP-3.5)', () => {
  it('note, comment, signpost and callout show their text in a box', () => {
    for (const tool of ['note', 'comment', 'signpost', 'callout']) {
      const points: [number, number][] = tool === 'callout' ? [[100, 300], [200, 200]] : [[200, 300]];
      const { shapes } = runTool(tool, points, { text: 'Breakout' });
      expect(texts(shapes), tool).toEqual(['Breakout']);
      expect(shapes.some((shape) => shape.kind === 'rect'), tool).toBe(true);
    }
    expect(textBoxWidth('Breakout')).toBeGreaterThan(50);
  });

  it('price note and price label show the anchor price', () => {
    expect(texts(runTool('price-note', [[100, 300], [200, 250]]).shapes)).toEqual(['700']);
    const label = runTool('price-label', [[100, 400]]);
    expect(texts(label.shapes)).toEqual(['600']);
    expect(label.hit(130, 400)).not.toBeNull();
  });

  it('arrow marks point their tip at the anchor', () => {
    const up = runTool('arrow-mark-up', [[200, 200]]).shapes[0];
    expect(up.kind === 'polygon' && up.points[0]).toEqual({ x: 200, y: 200 });
    expect(up.kind === 'polygon' && Math.max(...up.points.map((point) => point.y))).toBe(224);
    const left = runTool('arrow-mark-left', [[200, 200]], { text: 'Exit' }).shapes;
    expect(left[0].kind === 'polygon' && Math.max(...left[0].points.map((point) => point.x))).toBe(224);
    expect(texts(left)).toEqual(['Exit']);
    const marker = runTool('arrow-marker', [[100, 300], [300, 300]]);
    expect(marker.hit(200, 300)).not.toBeNull();
  });

  it('icons and emojis are placed at the anchor at their size', () => {
    const icon = runTool('icon', [[200, 200]], { properties: { icon: 'diamond', size: 48 } }).shapes[0];
    expect(icon.kind === 'polygon' && icon.points.map(({ x, y }) => [x, y])).toEqual([[200, 180], [220, 200], [200, 220], [180, 200]]);
    const emoji = runTool('emoji', [[200, 200]], { properties: { emoji: '🎯' } });
    expect(texts(emoji.shapes)).toEqual(['🎯']);
    expect(emoji.hit(205, 205)).not.toBeNull();
  });
});

describe('patterns, waves and cycles (TVP-3.7)', () => {
  it('XABCD labels its points and shows the ratios', () => {
    const { shapes } = runTool('xabcd-pattern', [[0, 400], [100, 200], [200, 323.6], [300, 250], [400, 360]]);
    expect(texts(shapes).slice(0, 5)).toEqual(['X', 'A', 'B', 'C', 'D']);
    // B retraces 61.8% of X-A.
    expect(texts(shapes)).toContain('0.618');
    expect(moveRatio({ time: '', price: 0 }, { time: '', price: 0 }, { time: '', price: 1 }, { time: '', price: 2 })).toBeNull();
  });

  it('Elliott waves label each wave; labels sit above highs and below lows', () => {
    const { shapes } = runTool('elliott-impulse-wave', [[0, 400], [50, 300], [100, 350], [150, 200], [200, 250], [250, 150]]);
    const labels = shapes.filter((shape): shape is Extract<DrawingShape, { kind: 'text' }> => shape.kind === 'text');
    expect(labels.map((label) => label.text)).toEqual(['0', '(1)', '(2)', '(3)', '(4)', '(5)']);
    expect(labels[1].y).toBeLessThan(300);
    expect(labels[2].y).toBeGreaterThan(350);
    const hidden = runTool('elliott-impulse-wave', [[0, 400], [50, 300], [100, 350], [150, 200], [200, 250], [250, 150]], { properties: { showLabels: false } });
    expect(texts(hidden.shapes)).toEqual([]);
  });

  it('head and shoulders draws its neckline through the troughs', () => {
    const { shapes } = runTool('head-and-shoulders', [[0, 400], [50, 250], [100, 320], [150, 150], [200, 320], [250, 250], [300, 400]]);
    expect(texts(shapes)).toContain('Neckline');
    // From the pattern's first point to its last.
    expect(shapes.some((shape) => shape.kind === 'segment' && shape.x1 === 0 && shape.y1 === 320 && shape.x2 === 300 && shape.y2 === 320)).toBe(true);
  });

  it('triangle pattern meets at its apex', () => {
    expect(intersection({ x: 0, y: 0 }, { x: 100, y: 50 }, { x: 0, y: 100 }, { x: 100, y: 50 })).toEqual({ x: 100, y: 50 });
    const { shapes } = runTool('triangle-pattern', [[0, 100], [20, 300], [100, 150], [120, 250]]);
    expect(shapes.filter((shape) => shape.kind === 'segment')).toHaveLength(2);
  });

  it('cyclic lines and time cycles repeat the A-B distance; the sine line spans the chart', () => {
    const lines = runTool('cyclic-lines', [[100, 300], [150, 300]]).shapes;
    expect(lines.map((shape) => (shape.kind === 'segment' ? round(shape.x1) : -1))).toEqual([0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800]);
    const cycles = runTool('time-cycles', [[100, 300], [150, 300]]).shapes;
    // 16 in the view plus one crossing each edge.
    expect(cycles).toHaveLength(18);
    const sine = runTool('sine-line', [[100, 200], [150, 300]]).shapes[0];
    expect(sine.kind === 'polyline' && sine.points[0].x).toBe(0);
    expect(sine.kind === 'polyline' && Math.min(...sine.points.map((point) => point.y))).toBeCloseTo(200, 0);
  });
});

describe('TVP-3.4/3.5/3.7 review fixes', () => {
  it('cycles continue however far the view is from A', () => {
    // A at bar -5000: the lines are still drawn every 50 bars across the view.
    const lines = runTool('cyclic-lines', [[-5000, 300], [-4950, 300]]).shapes;
    expect(lines.map((shape) => (shape.kind === 'segment' ? round(shape.x1) : -1))).toEqual([0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800]);
    // Without a width, time cycles still leave something to select.
    expect(runTool('time-cycles', [[100, 300], [100, 200]]).shapes).toHaveLength(1);
  });

  it('hiding labels keeps the pattern lines and fills', () => {
    const points: [number, number][] = [[0, 400], [50, 250], [100, 320], [150, 150], [200, 320], [250, 250], [300, 400]];
    const hidden = runTool('head-and-shoulders', points, { properties: { showLabels: false } }).shapes;
    expect(texts(hidden)).toEqual([]);
    expect(hidden.some((shape) => shape.kind === 'segment' && shape.y1 === 320 && shape.y2 === 320)).toBe(true);
    const xabcd = runTool('xabcd-pattern', [[0, 400], [100, 200], [200, 323.6], [300, 250], [400, 360]], { properties: { showLabels: false } }).shapes;
    expect(xabcd.filter((shape) => shape.kind === 'polygon')).toHaveLength(2);
    const triangle = runTool('triangle-pattern', [[0, 100], [20, 300], [100, 150], [120, 250]], { properties: { showLabels: false } }).shapes;
    expect(triangle.filter((shape) => shape.kind === 'segment')).toHaveLength(2);
  });

  it('three drives puts each ratio on the line joining the legs it compares', () => {
    const { shapes } = runTool('three-drives-pattern', [[0, 400], [50, 300], [100, 350], [150, 250], [200, 300], [250, 200], [300, 250]]);
    const ratios = shapes.filter((shape): shape is Extract<DrawingShape, { kind: 'segment' }> => shape.kind === 'segment' && shape.strokeWidth === 1);
    expect(ratios.map((shape) => [shape.x1, shape.x2])).toEqual([[0, 100], [50, 150], [100, 200], [150, 250]]);
    // Correction A against drive 1: 50 / 100.
    expect(texts(shapes)).toContain('0.500');
  });

  it('emojis show a selection outline', () => {
    const selected = runTool('emoji', [[200, 200]], { selected: true }).shapes[0];
    expect(selected).toMatchObject({ kind: 'rect', dash: [3, 3] });
  });
});
