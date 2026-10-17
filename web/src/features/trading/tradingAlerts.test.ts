import { afterEach, describe, expect, it, vi } from 'vitest';
import { tradingApi } from './tradingApi';
import type { TradingAlert } from './tradingTypes';
import { fixture } from '../../test/fixture';

const alert: TradingAlert = fixture({
  alert_id: 'alert-1',
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  binding_id: null,
  condition_type: 'price_above',
  threshold: '100',
  parameters: {
    lookback_bars: 1,
    indicator_id: null,
    period: 14,
    fast_period: 12,
    slow_period: 26,
    signal_period: 9,
    component: 'value',
    anchor_bars_ago: 0,
  },
  evaluation_policy: {
    interval: '1m',
    allow_partial_bars: false,
    formula_version: 'omnix-indicators-v2',
  },
  enabled: true,
  cooldown_seconds: 60,
  revision: 3,
});

afterEach(() => vi.unstubAllGlobals());

describe('Trading alert client', () => {
  it('sends revision headers without dropping condition policy', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(alert), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await tradingApi.updateAlert(alert, {
      instrument_id: alert.instrument_id,
      binding_id: null,
      condition_type: alert.condition_type,
      threshold: alert.threshold,
      parameters: alert.parameters,
      evaluation_policy: alert.evaluation_policy,
      enabled: false,
      cooldown_seconds: alert.cooldown_seconds,
    });
    await tradingApi.archiveAlert(alert);

    const update = fetchMock.mock.calls[0][1] as RequestInit;
    const archive = fetchMock.mock.calls[1][1] as RequestInit;
    expect(update.method).toBe('PUT');
    expect((update.headers as Record<string, string>)['If-Match']).toBe('3');
    expect(String(update.body)).toContain('omnix-indicators-v2');
    expect(archive.method).toBe('DELETE');
    expect((archive.headers as Record<string, string>)['If-Match']).toBe('3');
  });

  it('keeps alert evaluation on the server endpoint', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ triggers: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await tradingApi.evaluateAlerts(alert.instrument_id, '101.5', '2026-08-05T12:00:00Z');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/trading/alerts/evaluate');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
  });

  it('does not cache alert reads after a mutation', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ alerts: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await tradingApi.alerts();

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: 'no-store' });
  });
});
