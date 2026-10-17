import { afterEach, describe, expect, it, vi } from 'vitest';
import { tradingResearchApi } from './tradingResearchApi';

afterEach(() => vi.unstubAllGlobals());

describe('Trading research client', () => {
  it('uses one read-only research endpoint and preserves source evidence', async () => {
    const payload = {
      summary: 'Fixture summary',
      observations: ['Fixture observation'],
      risks: ['Fixture risk'],
      confidence: '0.7',
      provider: 'fixture',
      model: 'fixture-model',
      source: {
        instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
        interval: '1d',
        provider: 'binance',
        requested_binding_id: 'binance:BTCUSDT',
        resolved_binding_id: 'binance:BTCUSDT',
        dataset_fingerprint: 'fingerprint',
        as_of: '2026-08-05T00:00:00Z',
        freshness_mode: 'polled',
        formula_version: 'omnix-indicators-v2',
        bar_count: 120,
      },
      read_only: true,
      disclaimer: 'Research only. Not financial advice. No order was created or executed.',
    };
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await tradingResearchApi.generate({
      instrument_id: payload.source.instrument_id,
      binding_id: payload.source.resolved_binding_id,
      interval: '1d',
      bar_limit: 120,
      question: 'Summarize structure and risk.',
      selected_levels: ['50000'],
      model: null,
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/trading/research');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(result.read_only).toBe(true);
    expect(result.source.dataset_fingerprint).toBe('fingerprint');
    const endpoint = String(fetchMock.mock.calls[0][0]);
    expect(endpoint).not.toContain('alerts');
    expect(endpoint).not.toContain('backtests');
    expect(endpoint).not.toContain('paper');
    expect(endpoint).not.toContain('orders');
  });
});
