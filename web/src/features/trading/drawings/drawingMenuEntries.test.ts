import { describe, expect, it, vi } from 'vitest';
import type { TradingDrawing } from './drawingCommands';
import { drawingMenuEntries, offeredAlertLevels } from './TradingDrawingOverlay';
import { staticChartAccess } from './tools/scene';
import { pointAt, testBarSeries, testProjector, testServices } from '../../../test/drawingTools';
import type { DrawingToolServices } from './tools/types';

function drawing(toolType: TradingDrawing['toolType'], pixels: [number, number][]): TradingDrawing {
  return { drawingId: `${toolType}-1`, instrumentId: 'fixture', toolType, points: pixels.map(([x, y]) => pointAt(x, y)), selected: false, revision: 1 };
}

// Bars from x = 0 (the base time) on, so anchors at x >= 0 are within the loaded bars.
const services: DrawingToolServices = staticChartAccess(testProjector, { ...testServices, bars: testBarSeries() });

describe('drawing alert levels on the context menu', () => {
  it('offers sloped and flat levels when the chart index is the bars index', () => {
    expect(drawingMenuEntries(drawing('trend-line', [[10, 100], [50, 60]]), services).drawingAlertLevels).toHaveLength(1);
    expect(drawingMenuEntries(drawing('horizontal-line', [[10, 100]]), services).drawingAlertLevels).toHaveLength(1);
  });

  it('offers only flat levels on brick-type charts, where the server would cross other prices', () => {
    const sloped = drawingMenuEntries(drawing('trend-line', [[10, 100], [50, 60]]), services, false);
    expect(sloped.drawingAlertLevels).toBeUndefined();
    expect(sloped.trendlinePoints).toBeUndefined();
    const flatTrend = drawingMenuEntries(drawing('trend-line', [[10, 100], [50, 100]]), services, false);
    expect(flatTrend.drawingAlertLevels).toHaveLength(1);
    expect(drawingMenuEntries(drawing('horizontal-line', [[10, 100]]), services, false).drawingAlertLevels).toHaveLength(1);
  });
});

describe('sloped levels need the loaded bars', () => {
  it('drops sloped levels that start before the first loaded bar, keeps flat ones', () => {
    const early = drawing('trend-line', [[-30, 100], [50, 60]]);
    expect(drawingMenuEntries(early, services).drawingAlertLevels).toBeUndefined();
    expect(drawingMenuEntries(drawing('trend-line', [[-30, 100], [50, 100]]), services).drawingAlertLevels).toHaveLength(1);
    // Without loaded bars no sloped level is offered.
    expect(drawingMenuEntries(drawing('trend-line', [[10, 100], [50, 60]]), testServices).drawingAlertLevels).toBeUndefined();
  });
});

describe('a tool that throws', () => {
  it('loses only the feature that failed', async () => {
    const { guardToolCall } = await import('./tools/guard');
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const broken = () => { throw new Error('broken'); };
    expect(guardToolCall('test-tool', 'geometry', broken, [])).toEqual([]);
    expect(guardToolCall('test-tool', 'geometry', broken, [])).toEqual([]);
    expect(guardToolCall('test-tool', 'geometry', () => [1], [])).toEqual([1]);
    expect(error).toHaveBeenCalledTimes(1);
    error.mockRestore();
  });
});

describe('alert levels a tool returns', () => {
  it('drops malformed ones instead of breaking the menu', () => {
    const good = { key: 'a', label: 'A', anchors: [pointAt(10, 100), pointAt(20, 100)], extend: 'none', interpolation: 'bars' };
    const malformed = [null, {}, { anchors: [pointAt(10, 100)] }, { anchors: [pointAt(10, Number.NaN), pointAt(20, 1)] }, { anchors: [{ time: 'x', price: 1 }, pointAt(20, 1)] }];
    expect(offeredAlertLevels([...malformed, good], services, true)).toEqual([good]);
    expect(offeredAlertLevels('not a list', services, true)).toEqual([]);
  });
});

describe('handle edits', () => {
  it('commit only when they change the drawing', async () => {
    const { patchChanges } = await import('./useDrawingEditing');
    const trend = drawing('trend-line', [[10, 100], [50, 60]]);
    expect(patchChanges(trend, { points: trend.points.map((point) => ({ ...point })) })).toBe(false);
    expect(patchChanges(trend, { properties: { extendLeft: false } })).toBe(false);
    expect(patchChanges(trend, { properties: { extendLeft: true } })).toBe(true);
    expect(patchChanges(trend, { points: [pointAt(11, 100), trend.points[1]] })).toBe(true);
  });
});
