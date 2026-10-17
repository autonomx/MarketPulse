import { describe, expect, it } from 'vitest';
import { rectangleTool } from './boxShapes';
import { fibonacciTool } from './fibonacci';
import type { DrawingToolServices } from '../types';

const services = {
  timeAfterBars: (time: string, count: number) => new Date(Date.parse(time) + count * 3_600_000).toISOString(),
  formatPrice: (price: number) => price.toFixed(2),
} as unknown as DrawingToolServices;

const T0 = '2026-10-09T14:00:00.000Z';
const T1 = '2026-10-09T20:00:00.000Z';

describe('alert levels of rectangles and fib retracements (TVP-1.4)', () => {
  it('a rectangle offers its top and bottom from its left edge onwards', () => {
    const levels = rectangleTool.alertLevels!([{ time: T1, price: 90 }, { time: T0, price: 110 }], {}, services);
    expect(levels.map((level) => [level.key, level.anchors[0].time, level.anchors[0].price, level.extend])).toEqual([
      ['top', T0, 110, 'right'],
      ['bottom', T0, 90, 'right'],
    ]);
  });

  it('a fib retracement offers each visible level at its price', () => {
    const properties = { levels: [{ value: 0, visible: true, color: '' }, { value: 0.5, visible: true, color: '' }, { value: 0.618, visible: false, color: '' }] };
    const levels = fibonacciTool.alertLevels!([{ time: T0, price: 100 }, { time: T1, price: 200 }], properties, services);
    expect(levels.map((level) => [level.key, level.label, level.anchors[0].price, level.anchors[1].price])).toEqual([
      ['level-0', 'Level 0 (100.00)', 100, 100],
      ['level-0.5', 'Level 0.5 (150.00)', 150, 150],
    ]);
    // Keyed by value: removing a level keeps the others' keys; a repeated value gets its own.
    const edited = { levels: [{ value: 0.5, visible: true, color: '' }, { value: 0.5, visible: true, color: '' }] };
    expect(fibonacciTool.alertLevels!([{ time: T0, price: 100 }, { time: T1, price: 200 }], edited, services).map((level) => level.key)).toEqual(['level-0.5', 'level-0.5-2']);
    // Hiding the first keeps the second's key.
    const hidden = { levels: [{ ...edited.levels[0], visible: false }, edited.levels[1]] };
    expect(fibonacciTool.alertLevels!([{ time: T0, price: 100 }, { time: T1, price: 200 }], hidden, services).map((level) => level.key)).toEqual(['level-0.5-2']);
  });
});
