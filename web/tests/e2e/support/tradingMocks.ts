import { type Page, type Route } from '@playwright/test';

// The trading API mocks shared by the trading e2e specs (moved from trading-terminal.spec.ts).


export const instrument = {
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  asset_class: 'crypto',
  instrument_type: 'spot',
  venue: 'BINANCE',
  venue_symbol: 'BTC-USDT',
  display_symbol: 'BTCUSDT',
  base_currency: 'BTC',
  quote_currency: 'USDT',
  exchange_timezone: 'UTC',
  session_calendar: '24x7',
  price_scale: 100,
  minimum_tick: '0.01',
  status: 'active',
} as const;

export const alternateInstrument = {
  ...instrument,
  instrument_id: 'crypto:BINANCE:spot:ETH-USDT',
  venue_symbol: 'ETH-USDT',
  display_symbol: 'ETHUSDT',
  base_currency: 'ETH',
} as const;

export const binding = {
  binding_id: 'binance:historical_polling:crypto:BINANCE:spot:BTC-USDT',
  instrument_id: instrument.instrument_id,
  provider: 'binance',
  provider_symbol: 'BTCUSDT',
  feed_type: 'historical_polling',
  realtime_scope: 'mocked e2e feed',
  delay_seconds: 0,
  adjustment_capabilities: ['raw'],
  supported_intervals: ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '1d', '1w', '1mo'],
  usage_scope: 'personal_local',
  is_official_api: true,
} as const;

export const alternateBinding = {
  ...binding,
  binding_id: 'binance:historical_polling:crypto:BINANCE:spot:ETH-USDT',
  instrument_id: alternateInstrument.instrument_id,
  provider_symbol: alternateInstrument.display_symbol,
} as const;

export const workspacePayload = (name = 'Main Workspace') => ({
  schemaVersion: 3,
  name,
  layout: 'auto',
  activeChartId: 'chart-1',
  charts: [{
    chartId: 'chart-1',
    instrumentId: instrument.instrument_id,
    bindingId: binding.binding_id,
    interval: '1h',
    chartType: 'candlestick',
    indicators: [
      { id: 'sma', period: 20, enabled: true },
      { id: 'rsi', period: 14, enabled: true },
      { id: 'macd', period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9, enabled: false },
    ],
  }],
  links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
  panels: { right: false, bottom: false },
  favoriteInstrumentIds: [],
});

export function bars(marketInstrument: typeof instrument | typeof alternateInstrument = instrument) {
  return Array.from({ length: 120 }, (_, index) => {
    const start = new Date(Date.UTC(2026, 0, 1, index, 0));
    const end = new Date(start.getTime() + 3_600_000);
    const base = 70_000 + index * 10;
    return {
      instrument_id: marketInstrument.instrument_id,
      interval: '1h',
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      open: String(base),
      high: String(base + 30),
      low: String(base - 20),
      close: String(base + 10),
      volume: String(100 + index),
      is_final: true,
      adjustment_mode: 'raw',
      session: '24x7',
      provider: 'binance',
      provider_event_id: String(index),
      provider_sequence: index,
      ingestion_revision: 1,
      received_at: '2026-01-06T00:00:00Z',
    };
  });
}

export type MockState = {
  records: Map<string, { record_id: string; record_type: string; revision: number; payload: Record<string, unknown>; status: string }>;
  alerts: Array<Record<string, unknown>>;
  drawingWrites: number;
  barLimits: number[];
};

export async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

export async function installTradingMocks(page: Page): Promise<MockState> {
  const main = {
    record_id: 'main',
    record_type: 'workspace',
    revision: 1,
    payload: workspacePayload(),
    status: 'active',
  };
  const state: MockState = {
    records: new Map([['main', main]]),
    alerts: [],
    drawingWrites: 0,
    barLimits: [],
  };

  await page.route('**/api/trading/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === '/api/trading/providers/status' || path === '/api/trading/providers') {
      await fulfill(route, {
        ok: true,
        providers: [{
          provider: 'binance',
          display_name: 'Binance fixture',
          enabled: true,
          status: 'ready',
          policy: {
            usage_scope: 'personal_local',
            redistribution_allowed: false,
            authentication_required: false,
            is_official_api: true,
            realtime_scope: 'mocked e2e feed',
            delay_seconds: 0,
            terms_reference: 'https://www.binance.com/en/terms',
            supported_asset_classes: ['crypto'],
            supported_intervals: binding.supported_intervals,
            history_depth: 'fixture',
            rate_limit_policy: 'fixture',
          },
          bindings: [binding, alternateBinding],
          runtime: {
            request_count: 1,
            success_count: 1,
            failure_count: 0,
            consecutive_failures: 0,
            rate_limit_count: 0,
            in_flight: 0,
            max_concurrency: 4,
          },
        }],
      });
      return;
    }
    if (path === '/api/trading/instruments/search') {
      await fulfill(route, { instruments: [instrument, alternateInstrument] });
      return;
    }
    if (path === '/api/trading/bars') {
      state.barLimits.push(Number(url.searchParams.get('limit') ?? '0'));
      const marketInstrument = url.searchParams.get('instrument_id') === alternateInstrument.instrument_id
        ? alternateInstrument
        : instrument;
      const marketBinding = marketInstrument.instrument_id === alternateInstrument.instrument_id ? alternateBinding : binding;
      const dataset = bars(marketInstrument);
      await fulfill(route, {
        instrument: marketInstrument,
        binding: marketBinding,
        provenance: {
          instrument_id: marketInstrument.instrument_id,
          requested_binding: marketBinding.binding_id,
          resolved_binding: marketBinding.binding_id,
          fallback_reason: null,
          dataset_fingerprint: 'playwright-bars-v1',
          freshness_mode: 'polled',
          as_of: dataset.at(-1)?.end_time,
          received_at: '2026-01-06T00:00:00Z',
          delay_seconds: 0,
          cached: false,
          history_complete: true,
        },
        interval: url.searchParams.get('interval') ?? '1h',
        bars: dataset.map((bar) => ({ ...bar, interval: url.searchParams.get('interval') ?? '1h' })),
      });
      return;
    }
    if (path === '/api/trading/diagnostics') {
      await fulfill(route, { ok: true, diagnostics: { providers: [], cache: { disk_bounded: true }, streams: [] } });
      return;
    }
    if (path === '/api/trading/workspaces' && method === 'GET') {
      await fulfill(route, { records: [...state.records.values()] });
      return;
    }
    if (path === '/api/trading/watchlists' && method === 'GET') {
      await fulfill(route, {
        records: [{
          record_id: 'default',
          record_type: 'watchlist',
          revision: 1,
          payload: { name: 'Default Watchlist', instrumentIds: [instrument.instrument_id] },
          status: 'active',
        }],
      });
      return;
    }
    if (path === '/api/trading/watchlist-flags' && method === 'GET') {
      await fulfill(route, { records: [] });
      return;
    }
    if (path === '/api/trading/workspaces' && method === 'POST') {
      const input = request.postDataJSON() as { record_id: string; payload: Record<string, unknown> };
      const record = { record_id: input.record_id, record_type: 'workspace', revision: 1, payload: input.payload, status: 'active' };
      state.records.set(record.record_id, record);
      await fulfill(route, record, 201);
      return;
    }
    if (path.startsWith('/api/trading/workspaces/') && method === 'PUT') {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '');
      const input = request.postDataJSON() as { payload: Record<string, unknown> };
      const previous = state.records.get(id);
      const record = { record_id: id, record_type: 'workspace', revision: (previous?.revision ?? 0) + 1, payload: input.payload, status: 'active' };
      state.records.set(id, record);
      await fulfill(route, record);
      return;
    }
    if (path.startsWith('/api/trading/workspaces/') && method === 'DELETE') {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '');
      const previous = state.records.get(id);
      if (previous) state.records.delete(id);
      await fulfill(route, { ...(previous ?? main), status: 'archived', revision: (previous?.revision ?? 1) + 1 });
      return;
    }
    if (path === '/api/trading/drawings' && method === 'GET') {
      await fulfill(route, { records: [] });
      return;
    }
    if (path === '/api/trading/drawings' && method === 'POST') {
      state.drawingWrites += 1;
      const input = request.postDataJSON() as { record_id: string; payload: Record<string, unknown> };
      await fulfill(route, { record_id: input.record_id, record_type: 'drawing', revision: 1, payload: input.payload, status: 'active' }, 201);
      return;
    }
    if (path.startsWith('/api/trading/drawings/') && method === 'PUT') {
      state.drawingWrites += 1;
      const input = request.postDataJSON() as { record_id: string; payload: Record<string, unknown> };
      await fulfill(route, { record_id: input.record_id, record_type: 'drawing', revision: 2, payload: input.payload, status: 'active' });
      return;
    }
    if (path === '/api/trading/alerts' && method === 'GET') {
      await fulfill(route, { alerts: state.alerts });
      return;
    }
    if (path === '/api/trading/alerts' && method === 'POST') {
      const input = request.postDataJSON() as Record<string, unknown>;
      const alert = { ...input, enabled: true, revision: 1, last_observed_price: null, last_observed_value: null, last_triggered_at: null };
      state.alerts.unshift(alert);
      await fulfill(route, alert, 201);
      return;
    }
    if (path === '/api/trading/alerts/triggers') {
      await fulfill(route, { triggers: [] });
      return;
    }
    if (path.startsWith('/api/trading/alerts/') && method === 'PUT') {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '');
      const input = request.postDataJSON() as Record<string, unknown>;
      const existing = state.alerts.find((alert) => alert.alert_id === id) ?? {};
      const updated = { ...existing, ...input, alert_id: id, revision: Number(existing.revision ?? 1) + 1 };
      state.alerts = state.alerts.map((alert) => alert.alert_id === id ? updated : alert);
      await fulfill(route, updated);
      return;
    }
    if (path.startsWith('/api/trading/alerts/') && method === 'DELETE') {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '');
      const index = state.alerts.findIndex((alert) => alert.alert_id === id);
      const [removed] = index >= 0 ? state.alerts.splice(index, 1) : [];
      await fulfill(route, { ...(removed ?? {}), alert_id: id, enabled: false, revision: Number(removed?.revision ?? 1) + 1 });
      return;
    }
    await fulfill(route, { records: [], alerts: [], triggers: [] });
  });

  return state;
}
