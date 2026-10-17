import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import { indicatorOutputs } from './coreIndicators';
import {
  AUTO_CHART_PATTERN_DEFINITIONS,
  autoChartPatternLines,
  detectAutoChartPattern,
  detectAutoChartPatterns,
  findPatternPivots,
} from './autoPatterns';
import { fixture } from '../../../test/fixture';

function barsFromPrices(prices: readonly number[]): MarketBar[] {
  return prices.map((price, index) => (fixture({
    instrument_id: 'fixture',
    interval: '5m',
    start_time: new Date(Date.UTC(2026, 0, 2, 14, index * 5)).toISOString(),
    end_time: new Date(Date.UTC(2026, 0, 2, 14, index * 5 + 5)).toISOString(),
    open: String(price - 0.15),
    high: String(price + 0.4),
    low: String(price - 0.4),
    close: String(price),
    volume: String(100_000 + index * 1_000),
    is_final: true,
    adjustment_mode: 'raw',
    session: 'regular',
    provider: 'fixture',
    ingestion_revision: 1,
    received_at: new Date(Date.UTC(2026, 0, 2, 15)).toISOString(),
  })));
}

const doubleTopBars = barsFromPrices([
  90, 92, 96, 102, 108, 112, 107, 100, 95, 92,
  95, 101, 107, 111, 107, 100, 96, 93, 91,
]);

const doubleBottomBars = barsFromPrices([
  112, 108, 102, 96, 91, 88, 93, 100, 105, 109,
  105, 99, 93, 89, 94, 101, 106, 109, 112,
]);

describe('auto chart patterns', () => {
  it('exposes the TradingView-style chart-pattern catalog', () => {
    const names = AUTO_CHART_PATTERN_DEFINITIONS.map((definition) => definition.name);
    expect(names).toContain('All Chart Patterns');
    expect(names).toContain('Bullish Flag Chart Pattern');
    expect(names).toContain('Cup and Handle Chart Pattern');
    expect(names).toContain('Head and Shoulders Chart Pattern');
    expect(names).toContain('Triangle Chart Pattern');
    expect(names).toContain('Auto Trend Detector');
    expect(AUTO_CHART_PATTERN_DEFINITIONS).toHaveLength(19);
  });

  it('uses confirmed alternating pivots rather than an unconfirmed last swing', () => {
    const pivots = findPatternPivots(doubleTopBars, 2);
    expect(pivots.map((pivot) => pivot.type)).toEqual(expect.arrayContaining(['high', 'low']));
    expect(pivots.some((pivot) => pivot.index === 13 && pivot.type === 'high')).toBe(true);

    const beforeConfirmation = doubleTopBars.slice(0, 15);
    expect(detectAutoChartPattern(beforeConfirmation, 'double-top-pattern', 2)).toBeNull();
  });

  it('detects a confirmed double top with bearish geometry', () => {
    const pattern = detectAutoChartPattern(doubleTopBars, 'double-top-pattern', 2);
    expect(pattern).not.toBeNull();
    expect(pattern?.direction).toBe('bearish');
    expect(pattern?.confidence).toBeGreaterThan(0.65);
    expect(pattern?.segments.length).toBeGreaterThanOrEqual(3);
    expect(pattern?.startIndex).toBeLessThan(pattern?.endIndex ?? 0);
  });

  it('detects a confirmed double bottom with bullish geometry', () => {
    const pattern = detectAutoChartPattern(doubleBottomBars, 'double-bottom-pattern', 2);
    expect(pattern).not.toBeNull();
    expect(pattern?.direction).toBe('bullish');
    expect(pattern?.confidence).toBeGreaterThan(0.65);
  });

  it('lets All Chart Patterns discover matching recent structures without duplicate geometry', () => {
    const matches = detectAutoChartPatterns(doubleTopBars, 2);
    expect(matches.some((pattern) => pattern.id === 'double-top-pattern')).toBe(true);
    expect(matches.length).toBeLessThanOrEqual(4);
    const geometries = matches.map((pattern) => `${pattern.startIndex}:${pattern.endIndex}`);
    expect(new Set(geometries).size).toBe(geometries.length);
  });

  it('converts matches into finite chart overlay lines and preserves native bearish styling', () => {
    const lines = autoChartPatternLines(doubleTopBars, 'double-top-pattern', 2);
    expect(lines.length).toBeGreaterThanOrEqual(3);
    expect(lines[0]?.color).toBe('#f23645');
    expect(lines[0]?.title).toMatch(/Double Top Chart Pattern · \d+%/);
    for (const line of lines) {
      expect(line.points).toHaveLength(2);
      expect(line.points.every((point) => Number.isFinite(point.value))).toBe(true);
      expect(line.points.every((point) => Boolean(Date.parse(point.time)))).toBe(true);
    }
  });

  it('renders a pattern through the existing core-indicator chart pipeline', () => {
    const outputs = indicatorOutputs(doubleTopBars, {
      id: 'double-top-pattern',
      period: 2,
      enabled: true,
    });
    expect(outputs.length).toBeGreaterThanOrEqual(3);
    expect(outputs.every((output) => output.pane === 0 && output.kind === 'line')).toBe(true);
    expect(outputs[0]?.color).toBe('#f23645');
    expect(outputs[0]?.labelsOnPriceScale).toBe(false);
  });
});
