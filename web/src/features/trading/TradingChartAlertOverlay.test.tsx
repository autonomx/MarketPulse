import { describe, expect, it } from 'vitest';
import { editorDefaults } from './TradingChartAlertOverlay';

describe('Trading chart alert placement defaults', () => {
  it('keeps an RSI placement on the indicator scale', () => {
    const editor = editorDefaults({
      time: '2026-08-22T20:00:00.000Z',
      price: 73,
      x: 120,
      y: 240,
      source: 'context-menu',
      indicatorId: 'rsi',
      indicatorPeriod: 14,
    }, 77_286.18);

    expect(editor).toMatchObject({
      condition: 'indicator_above',
      indicator: 'rsi',
      period: '14',
      threshold: '73', // an indicator value keeps its precision (TVP-1.3)
    });
  });

  it('keeps a main-chart placement as a price alert', () => {
    const editor = editorDefaults({
      time: '2026-08-22T20:00:00.000Z',
      price: 78_000,
      x: 120,
      y: 240,
      source: 'context-menu',
    }, 77_286.18);

    expect(editor.condition).toBe('price_above');
    expect(editor.indicator).toBe('rsi');
  });
});

describe('alerts placed from a drawing (TVP-1.4)', () => {
  it('follow the drawing, starting on its first level', () => {
    const levels = [
      { key: 'upper', label: 'Upper', anchors: [{ time: 'a', price: 2 }, { time: 'b', price: 3 }] as const, extend: 'right' as const, interpolation: 'bars' as const },
      { key: 'lower', label: 'Lower', anchors: [{ time: 'a', price: 1 }, { time: 'b', price: 2 }] as const, extend: 'right' as const, interpolation: 'bars' as const },
    ];
    expect(editorDefaults({ time: 'a', price: 2, x: 0, y: 0, source: 'context-menu', drawingId: 'ch', drawingAlertLevels: levels, trendlinePoints: [{ time: 'a', price: 2 }, { time: 'b', price: 3 }] }, 1))
      .toMatchObject({ condition: 'trendline_crossing', drawingId: 'ch', drawingLevel: 'upper', drawingLevels: levels });
  });
});
