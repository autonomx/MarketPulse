import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PaperAccount } from './paperTypes';
import { tradingPaperApi } from './tradingPaperApi';
import { fixture } from '../../test/fixture';

const account: PaperAccount = fixture({
  account_id: 'paper-1',
  name: 'Paper Account',
  base_currency: 'USD',
  commission_bps: '0',
  enabled: true,
  revision: 4,
});

const snapshot = {
  account,
  balances: [],
  positions: [],
  open_orders: [],
  recent_fills: [],
  recent_ledger: [],
};

afterEach(() => vi.unstubAllGlobals());

describe('Trading paper client', () => {
  it('uses revision headers for reset and archive', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(snapshot), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await tradingPaperApi.resetAccount(account, '50000');
    await tradingPaperApi.archiveAccount(account);

    const reset = fetchMock.mock.calls[0][1] as RequestInit;
    const archive = fetchMock.mock.calls[1][1] as RequestInit;
    expect(reset.method).toBe('POST');
    expect((reset.headers as Record<string, string>)['If-Match']).toBe('4');
    expect(archive.method).toBe('DELETE');
    expect((archive.headers as Record<string, string>)['If-Match']).toBe('4');
  });

  it('submits only paper namespace orders', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      account_id: 'paper-1',
      order_id: 'order-1',
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      side: 'buy',
      order_type: 'market',
      quantity: '1',
      status: 'open',
      filled_quantity: '0',
      idempotency_key: 'order-1',
    }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await tradingPaperApi.placeOrder('paper-1', {
      order_id: 'order-1',
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      side: 'buy',
      order_type: 'market',
      quantity: '1',
      idempotency_key: 'order-1',
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/trading/paper/accounts/paper-1/orders');
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('broker');
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('live');
  });

  it('hydrates an absent protection from the account list without a per-symbol 404', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ protections: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(tradingPaperApi.protection('paper-1', 'equity:NYSE:GME')).resolves.toBeNull();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/trading/paper/accounts/paper-1/protections?active_only=true');
  });
});
