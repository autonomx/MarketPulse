import { describe, expect, it } from 'vitest';
import { tradingDrawingRecordId } from './useTradingDrawings';

describe('trading drawing persistence scope', () => {
  it('isolates the same tab and instrument across workspaces', () => {
    const first = tradingDrawingRecordId('equity:NASDAQ:AAPL', 'workspace-one:tab-1');
    const second = tradingDrawingRecordId('equity:NASDAQ:AAPL', 'workspace-two:tab-1');

    expect(first).not.toBe(second);
    expect(first).toContain('workspace-one-tab-1');
    expect(second).toContain('workspace-two-tab-1');
  });

  it('isolates distinct tabs inside one workspace', () => {
    const first = tradingDrawingRecordId('crypto:BINANCE:spot:BTC-USDT', 'main:tab-a');
    const second = tradingDrawingRecordId('crypto:BINANCE:spot:BTC-USDT', 'main:tab-b');

    expect(first).not.toBe(second);
  });
});
