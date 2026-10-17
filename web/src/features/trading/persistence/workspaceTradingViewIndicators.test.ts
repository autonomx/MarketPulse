import { describe, expect, it } from 'vitest';
import { parseTradingWorkspace } from './workspaceDocument';

function payload(indicatorId: string) {
  return {
    schemaVersion: 3,
    name: 'Indicator persistence',
    layout: 'auto',
    activeChartId: 'chart-1',
    charts: [{
      chartId: 'chart-1',
      instrumentId: 'equity:NASDAQ:NVDA',
      bindingId: null,
      interval: '1d',
      chartType: 'candlestick',
      indicators: [{ id: indicatorId, period: 14, enabled: true, visible: true }],
      comparisons: [],
    }],
    links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
    panels: { right: true, bottom: true },
    favoriteInstrumentIds: [],
  };
}

describe('TradingView indicator workspace persistence', () => {
  it('round trips TradingView built-in IDs', () => {
    const parsed = parseTradingWorkspace(payload('tv-average-directional-index-adx'));
    expect(parsed?.charts[0].indicators[0]).toMatchObject({
      id: 'tv-average-directional-index-adx',
      period: 14,
      enabled: true,
    });
  });

  it('round trips automatic chart-pattern IDs', () => {
    const parsed = parseTradingWorkspace(payload('double-bottom-pattern'));
    expect(parsed?.charts[0].indicators[0].id).toBe('double-bottom-pattern');
  });

  it('still rejects arbitrary unknown indicator IDs', () => {
    expect(parseTradingWorkspace(payload('not-a-real-indicator'))).toBeNull();
  });
});
