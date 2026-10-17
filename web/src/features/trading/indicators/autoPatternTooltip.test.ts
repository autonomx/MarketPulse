import { describe, expect, it } from 'vitest';
import type { IndicatorOutput } from './coreIndicators';
import { autoPatternGroupKey, autoPatternTooltipDetails } from './autoPatternTooltip';

function output(
  key: string,
  title: string,
  color = '#20c997',
  from = '2026-01-01T00:00:00.000Z',
  to = '2026-01-03T00:00:00.000Z',
): IndicatorOutput {
  return {
    key,
    title,
    pane: 0,
    kind: 'line',
    points: [
      { time: from, value: 100 },
      { time: to, value: 105 },
    ],
    color,
  };
}

describe('auto pattern tooltip metadata', () => {
  it('recognizes auto pattern segment keys only', () => {
    expect(autoPatternGroupKey('double-bottom-pattern:42:0')).toBe('double-bottom-pattern:42');
    expect(autoPatternGroupKey('sma:20')).toBeNull();
    expect(autoPatternGroupKey('chart-patterns-all:42:0')).toBeNull();
    expect(autoPatternGroupKey('double-bottom-pattern:not-a-number:0')).toBeNull();
  });

  it('builds one tooltip identity across all segments of a detected pattern', () => {
    const outputs = [
      output('double-bottom-pattern:42:0', 'Double Bottom Chart Pattern · 82%'),
      output('double-bottom-pattern:42:1', 'Double Bottom Chart Pattern', '#20c997', '2026-01-03T00:00:00.000Z', '2026-01-05T00:00:00.000Z'),
      output('double-bottom-pattern:42:2', 'Double Bottom Chart Pattern', '#20c997', '2026-01-02T00:00:00.000Z', '2026-01-04T00:00:00.000Z'),
    ];
    const details = autoPatternTooltipDetails(outputs[1], outputs);
    expect(details).toEqual({
      groupKey: 'double-bottom-pattern:42',
      id: 'double-bottom-pattern',
      name: 'Double Bottom Chart Pattern',
      direction: 'bullish',
      confidence: 82,
      fromTime: '2026-01-01T00:00:00.000Z',
      toTime: '2026-01-05T00:00:00.000Z',
    });
  });

  it('uses rendered direction color for dynamic auto-trend and Elliott Wave matches', () => {
    const bearishTrend = output('auto-trend-detector:88:0', 'Auto Trend Detector · 71%', '#f23645');
    const bullishWave = output('elliott-wave-pattern:99:0', 'Elliott Wave Chart Pattern · 74%', '#20c997');
    expect(autoPatternTooltipDetails(bearishTrend, [bearishTrend])?.direction).toBe('bearish');
    expect(autoPatternTooltipDetails(bullishWave, [bullishWave])?.direction).toBe('bullish');
  });
});
