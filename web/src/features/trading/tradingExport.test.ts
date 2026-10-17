import { describe, expect, it } from 'vitest';
import { buildTradingWorkspaceExport } from './tradingExport';

describe('Trading workspace export', () => {
  it('exports only portable canonical state', () => {
    const payload = buildTradingWorkspaceExport({
      layout: 'columns-3',
      activeChartId: 'chart-2',
      charts: [
        {
          chartId: 'chart-1',
          instrumentId: 'equity:NASDAQ:AAPL',
          bindingId: 'yahoo:historical_polling:equity:NASDAQ:AAPL',
          interval: '1w',
          chartType: 'area',
          indicators: [{ id: 'bollinger', period: 20, standardDeviations: 2, enabled: true }],
        },
        {
          chartId: 'chart-2',
          instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
          bindingId: 'binance:websocket_and_rest:crypto:BINANCE:spot:BTC-USDT',
          interval: '2h',
          chartType: 'candlestick',
          indicators: [],
        },
      ],
      links: { instrument: false, interval: true, crosshair: true, visibleRange: true },
    });
    const text = JSON.stringify(payload);
    expect(payload.schemaVersion).toBe(2);
    expect(payload.layout).toBe('columns-3');
    expect(payload.charts.map((chart) => chart.interval)).toEqual(['1w', '2h']);
    expect(text).not.toContain('WebSocket');
    expect(text).not.toContain('setLayout');
  });
});
