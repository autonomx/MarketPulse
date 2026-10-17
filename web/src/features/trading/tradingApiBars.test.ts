import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api/gateway';
import { tradingApi } from './tradingApi';

describe('bars requests (TVP-2.5)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('asks for clock alignment only when the chart passes it', async () => {
    const get = vi.spyOn(api, 'GET').mockResolvedValue({ data: { bars: [] }, response: new Response(null, { status: 200 }) } as never);
    await tradingApi.bars('crypto:BINANCE:spot:BTC-USDT', '7m', 1_000, null);
    await tradingApi.bars('equity:NASDAQ:AAPL', '7m', 1_000, 'binding', { alignment: 'clock', extendedHours: false });
    const query = (call: number) => (get.mock.calls[call][1] as { params: { query: Record<string, unknown> } }).params.query;
    expect(query(0)).toEqual({ instrument_id: 'crypto:BINANCE:spot:BTC-USDT', interval: '7m', limit: 1_000 });
    expect(query(1)).toEqual({
      instrument_id: 'equity:NASDAQ:AAPL', interval: '7m', limit: 1_000, binding_id: 'binding', alignment: 'clock', extended_hours: false,
    });
  });
});
