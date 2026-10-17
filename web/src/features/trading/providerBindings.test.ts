import { describe, expect, it } from 'vitest';
import { tradingStreamUrl } from './tradingApi';
import { parseTradingWorkspace, serializeTradingWorkspace } from './persistence/workspaceDocument';
import { TradingStreamHub } from './streaming/tradingStreamHub';

describe('provider binding ownership', () => {
  it('persists the selected binding independently from the instrument', () => {
    const payload = serializeTradingWorkspace({
      layout: 'auto',
      activeChartId: 'chart-1',
      charts: [{
        chartId: 'chart-1',
        instrumentId: 'equity:NASDAQ:AAPL',
        bindingId: 'yahoo:historical_polling:equity:NASDAQ:AAPL',
        interval: '1d',
        chartType: 'candlestick',
        indicators: [],
      }],
      links: { instrument: false, interval: false, crosshair: true, visibleRange: true },
    });
    const parsed = parseTradingWorkspace(payload);
    expect(parsed?.charts[0].instrumentId).toBe('equity:NASDAQ:AAPL');
    expect(parsed?.charts[0].bindingId).toContain('yahoo:');
  });

  it('migrates pre-binding workspaces to provider resolution', () => {
    const parsed = parseTradingWorkspace({
      schemaVersion: 1,
      name: 'Old workspace',
      layout: 'one',
      activeChartId: 'chart-1',
      charts: [{
        chartId: 'chart-1',
        instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
        interval: '1m',
        chartType: 'candlestick',
        indicators: [],
      }],
      links: { instrument: false, interval: false, crosshair: true, visibleRange: true },
      panels: {},
    });
    expect(parsed?.charts[0].bindingId).toBeNull();
  });

  it('keys streams by binding so feeds cannot be silently mixed', () => {
    const instrument = 'crypto:BINANCE:spot:BTC-USDT';
    expect(TradingStreamHub.key(instrument, '1m', 'binance-a'))
      .not.toBe(TradingStreamHub.key(instrument, '1m', 'binance-b'));
    expect(tradingStreamUrl(instrument, '1m', 'binance-a')).toContain('binding_id=binance-a');
  });
});
