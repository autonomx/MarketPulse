import { expect, test, type Page, type Route } from '@playwright/test';

const instrument = {
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

const alternateInstrument = {
  ...instrument,
  instrument_id: 'crypto:BINANCE:spot:ETH-USDT',
  venue_symbol: 'ETH-USDT',
  display_symbol: 'ETHUSDT',
  base_currency: 'ETH',
} as const;

const binding = {
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

const alternateBinding = {
  ...binding,
  binding_id: 'binance:historical_polling:crypto:BINANCE:spot:ETH-USDT',
  instrument_id: alternateInstrument.instrument_id,
  provider_symbol: alternateInstrument.display_symbol,
} as const;

const workspacePayload = (name = 'Main Workspace') => ({
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

function bars(marketInstrument: typeof instrument | typeof alternateInstrument = instrument) {
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

type MockState = {
  records: Map<string, { record_id: string; record_type: string; revision: number; payload: Record<string, unknown>; status: string }>;
  alerts: Array<Record<string, unknown>>;
  drawingWrites: number;
  barLimits: number[];
};

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function installTradingMocks(page: Page): Promise<MockState> {
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

test('Trading terminal smoke covers flexible layout, saved workspaces, drawings, and chart alerts', async ({ page }) => {
  const state = await installTradingMocks(page);
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => {
    const message = error.stack ?? error.message;
    pageErrors.push(message);
    console.error(`[trading-pageerror] ${message}`);
  });
  page.on('console', (message) => {
    if (message.type() === 'error') console.error(`[trading-console] ${message.text()}`);
  });
  await page.goto('/trading');

  await expect(page.getByRole('main', { name: /Trading/i })).toBeVisible();
  await expect(page.locator('.trading-chart-panel').first()).toBeVisible();
  await expect.poll(() => pageErrors, { message: 'Trading must initialize without browser exceptions' }).toEqual([]);
  await expect(page.locator('.trading-chart-ohlc').first()).toBeVisible();
  await expect(page.locator('.trading-terminal-header .trading-command-bar')).toBeAttached();
  await expect(page.locator('.trading-terminal-header')).toHaveCSS('height', '42px');
  const sideRail = page.getByRole('complementary', { name: 'Trading side panel rail' });
  await expect(sideRail).toBeVisible();
  await expect(sideRail.getByRole('button', { name: 'Expand right panel' })).toBeVisible();
  await page.getByRole('button', { name: 'Right panel', exact: true }).click();
  const sidePanel = page.getByRole('complementary', { name: 'Trading side panel', exact: true });
  await expect(sidePanel.getByRole('tab', { name: 'Alerts', exact: true })).toBeVisible();
  await sidePanel.getByRole('tab', { name: 'Alerts', exact: true }).click();
  await expect(sidePanel.locator('.trading-alerts-panel')).toBeVisible();
  await sidePanel.getByRole('tab', { name: /Log/ }).click();
  await expect(sidePanel.locator('.trading-alert-log-panel')).toBeVisible();
  await sidePanel.getByRole('tablist', { name: 'Trading alerts sections' }).getByRole('tab', { name: 'Alerts', exact: true }).click();
  await sidePanel.getByRole('button', { name: 'Add alert' }).click();
  const sideAlertDialog = page.getByRole('dialog', { name: /Create alert on BTCUSDT/ });
  await expect(sideAlertDialog).toBeVisible();
  await sideAlertDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Open symbol search' }).click();
  const symbolSearch = page.getByRole('dialog', { name: 'Symbol search' });
  await expect(symbolSearch).toBeVisible();
  await expect(symbolSearch.getByRole('button', { name: 'Stocks', exact: true })).toBeVisible();
  await expect(symbolSearch.getByRole('button', { name: 'Crypto', exact: true })).toBeVisible();
  await symbolSearch.getByRole('textbox', { name: 'Search symbols' }).fill('BTC');
  await expect(symbolSearch.getByRole('button', { name: /BTCUSDT/ })).toBeVisible();
  await symbolSearch.getByRole('button', { name: /BTCUSDT/ }).click();
  await expect(symbolSearch).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Focus this chart' })).toHaveCount(1);
  await expect(page.getByRole('group', { name: 'Indicator legend' })).toHaveCount(1);
  await page.getByRole('button', { name: 'Hide SMA 20 overlay' }).click();
  await expect(page.getByRole('button', { name: 'Show SMA 20 overlay' })).toBeVisible();
  await page.getByRole('button', { name: 'Show SMA 20 overlay' }).click();
  await page.getByRole('button', { name: 'Delete SMA 20 overlay' }).click();
  await expect(page.getByRole('button', { name: 'Delete SMA 20 overlay' })).toHaveCount(0);
  await page.locator('.trading-indicator-manager').getByRole('button', { name: 'Indicators' }).click();
  const smaOption = page.getByRole('group', { name: 'Technical indicators' }).getByRole('button', { name: 'SMA 20', exact: true });
  await expect(smaOption).toHaveAttribute('aria-pressed', 'false');
  await smaOption.click();
  await expect(page.locator('.trading-timeframe-buttons > button')).toHaveCount(3);
  await expect(page.getByRole('button', { name: '1H' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'All supported Trading intervals' })).toContainText('1H');
  const rangeNav = page.getByRole('navigation', { name: 'chart-1 visible range' });
  const timeframe = page.getByRole('button', { name: 'All supported Trading intervals' });
  for (const [range, interval] of [['1D', '1m'], ['5D', '5m'], ['1M', '30m'], ['3M', '1h'], ['6M', '2h'], ['YTD', '1d'], ['1Y', '1d'], ['5Y', '1w'], ['All', '1mo']] as const) {
    const rangeButton = rangeNav.getByRole('button', { name: new RegExp(`^${range}:`) });
    await rangeButton.click();
    const expectedLabel = interval.endsWith('mo') ? interval.replace('mo', 'M') : interval.endsWith('m') ? interval : interval.toUpperCase();
    await expect(timeframe).toContainText(expectedLabel);
    await expect(rangeButton).toHaveAttribute('aria-pressed', 'true');
  }
  await timeframe.click();
  const intervalMenu = page.getByRole('dialog', { name: 'TradingView intervals' });
  await expect(intervalMenu).toBeVisible();
  await expect(intervalMenu.getByRole('group', { name: 'Ticks' })).toContainText('1 tick');
  await expect(intervalMenu.getByRole('group', { name: 'Ranges' })).toContainText('1000 ranges');
  await expect(intervalMenu.getByRole('button', { name: '2 minutes', exact: true })).toBeEnabled();
  await intervalMenu.getByRole('button', { name: '5 minutes', exact: true }).click();
  await expect(timeframe).toContainText('5m');
  await expect.poll(() => state.barLimits).toContain(5_000);

  await page.getByLabel('Number of charts').selectOption('3');
  await expect(page.locator('.trading-chart-panel')).toHaveCount(3);
  const secondChart = page.locator('.trading-chart-panel').nth(1);
  await secondChart.getByRole('button', { name: 'Change symbol for Chart 2' }).click();
  const secondChartSymbolSearch = page.getByRole('dialog', { name: 'Symbol search' });
  await secondChartSymbolSearch.getByRole('textbox', { name: 'Search symbols' }).fill('ETH');
  await secondChartSymbolSearch.getByRole('button', { name: /ETHUSDT/ }).click();
  await expect(secondChart).toContainText('ETHUSDT');
  await expect(page.locator('.trading-chart-panel').first()).toContainText('BTCUSDT');
  await expect(page.getByRole('button', { name: 'Focus this chart' })).toHaveCount(3);
  await page.getByLabel('Grid columns').selectOption('columns-3');

  page.once('dialog', (dialog) => dialog.accept('Swing Research'));
  await page.getByRole('button', { name: 'Create workspace' }).click();
  await expect(page.getByLabel('Saved Trading workspace')).toContainText('Swing Research');

  await page.getByRole('button', { name: 'Favorite active instrument' }).click();
  await expect(page.getByRole('button', { name: 'Remove active instrument from favorites' })).toBeVisible();

  await page.getByRole('button', { name: 'Lines' }).click();
  await page.getByRole('menu', { name: 'Lines' }).getByRole('menuitem', { name: 'Trend line', exact: true }).click();
  const overlay = page.locator('.trading-chart-panel.active .trading-drawing-overlay');
  const box = await overlay.boundingBox();
  // The chart header floats over the top of the chart; draw on the chart below it.
  const header = await page.locator('.trading-chart-panel.active .trading-chart-header').boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    const top = Math.max(box.y, header ? header.y + header.height : box.y);
    await page.mouse.move(box.x + 100, top + 120);
    await page.mouse.down();
    await page.mouse.move(box.x + 260, top + 70);
    await page.mouse.up();
  }
  await expect.poll(() => state.drawingWrites).toBeGreaterThan(0);

  // Changing Chart 2 to ETH makes Chart 2 active by design. Create the alert
  // from the Alerts panel; the chart toolbar no longer exposes a direct alert
  // placement button.
  await expect(page.getByRole('button', { name: 'Place price alert' })).toHaveCount(0);
  await sidePanel.getByRole('button', { name: 'Add alert' }).click();
  await expect(page.getByRole('dialog', { name: /Create alert on ETHUSDT/ })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Alert trigger' })).toHaveValue('every_time');
  await expect(page.getByRole('checkbox', { name: 'App' })).toBeChecked();
  await page.getByRole('textbox', { name: 'Alert message' }).fill('ETH alert');
  await page.getByRole('dialog', { name: /Create alert on ETHUSDT/ }).getByRole('button', { name: 'Create alert' }).click();
  await expect.poll(() => state.alerts.length).toBe(1);
  expect((state.alerts[0].parameters as Record<string, unknown>).message).toBe('ETH alert');
  // Only Chart 2 is ETH, so the ETH alert belongs on exactly one chart.
  await expect(page.locator('.trading-alert-price-label')).toHaveCount(1);
  await expect(page.locator('.trading-chart-panel').filter({ has: page.locator('.trading-alert-price-label') })).toHaveCount(1);
  const alertRow = sidePanel.locator('.trading-alert-list li').first();
  await expect(alertRow).toBeVisible();
  await alertRow.getByRole('button', { name: /Options for/ }).click();
  await sidePanel.getByRole('menuitem', { name: 'Delete alert' }).click();
  await expect.poll(() => state.alerts.length).toBe(0);
  await expect(sidePanel.locator('.trading-alert-list li')).toHaveCount(0);
  await expect(page.locator('.trading-alert-price-label')).toHaveCount(0);

  const chartStage = page.locator('.trading-chart-panel.active .trading-chart-stage');
  const chartStageBox = await chartStage.boundingBox();
  // Below the chart header, which floats over the top of the chart.
  const chartHeaderBox = await page.locator('.trading-chart-panel.active .trading-chart-header').boundingBox();
  expect(chartStageBox).not.toBeNull();
  if (chartStageBox) {
    const headerBottom = chartHeaderBox ? chartHeaderBox.y + chartHeaderBox.height - chartStageBox.y : 0;
    await chartStage.click({
      button: 'right',
      position: { x: chartStageBox.width / 2, y: Math.max(0, headerBottom) + 12 },
      force: true,
    });
  }
  const chartMenu = page.getByRole('menu', { name: 'Chart context menu' });
  await expect(chartMenu).toBeVisible();
  await expect(chartMenu.getByRole('menuitem', { name: /Reset chart view/ })).toBeVisible();
  await expect(chartMenu.getByRole('menuitem', { name: /Add alert on ETHUSDT/ })).toBeVisible();
  await chartMenu.getByRole('menuitemcheckbox', { name: 'Table view' }).click();
  await expect(page.getByRole('dialog', { name: /Chart table view/ })).toBeVisible();
  await sideRail.getByRole('button', { name: 'Collapse right panel' }).click();
  await expect(sidePanel).toHaveCount(0);
  await sideRail.getByRole('button', { name: 'Expand right panel' }).click();
  await expect(sidePanel).toBeVisible();
});

test('indicator panes expose close, minimize, and reorder controls', async ({ page }) => {
  await installTradingMocks(page);
  await page.goto('/trading');

  const rsiControls = page.locator('.trading-indicator-pane-controls[data-indicator-id="rsi"]');
  await expect(rsiControls).toBeVisible();
  await expect(rsiControls).toHaveCSS('opacity', '0');
  const rsiTopResize = page.locator('.trading-indicator-pane-resize-handle[data-indicator-id="rsi"][data-edge="top"]');
  await expect(rsiTopResize).toBeVisible();
  const initialRsiHeight = Number(await rsiTopResize.getAttribute('aria-valuenow'));
  // Use Playwright's live locator actionability instead of a previously measured
  // 8px hit target. Indicator geometry can refresh between boundingBox() and a
  // raw mouse down, which made this drag intermittently miss the separator.
  await rsiTopResize.hover();
  await page.mouse.down();
  await expect(rsiTopResize).toHaveClass(/is-resizing/);
  const activeResizeBox = await rsiTopResize.boundingBox();
  expect(activeResizeBox).not.toBeNull();
  if (activeResizeBox) {
    await page.mouse.move(activeResizeBox.x + activeResizeBox.width / 2, activeResizeBox.y - 24, { steps: 10 });
  }
  await page.mouse.up();
  await expect.poll(async () => Number(await rsiTopResize.getAttribute('aria-valuenow'))).not.toBe(initialRsiHeight);
  const rsiBottomResize = page.locator('.trading-indicator-pane-resize-handle[data-indicator-id="rsi"][data-edge="bottom"]');
  const chartCanvas = page.locator('.trading-chart-canvas');
  const topBorderBox = await rsiTopResize.boundingBox();
  const bottomBorderBox = await rsiBottomResize.boundingBox();
  const canvasBox = await chartCanvas.boundingBox();
  expect(topBorderBox).not.toBeNull();
  expect(bottomBorderBox).not.toBeNull();
  expect(canvasBox).not.toBeNull();
  if (topBorderBox && bottomBorderBox && canvasBox) {
    const x = canvasBox.x + canvasBox.width * 0.4;
    const y = (topBorderBox.y + bottomBorderBox.y) / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await expect(chartCanvas).toHaveAttribute('data-panning-indicator', 'rsi');
    await expect(chartCanvas).toHaveClass(/is-grabbing/);
    await page.mouse.move(x, y - 20, { steps: 8 });
    await page.mouse.up();
  }
  const indicatorLegend = page.getByRole('group', { name: 'Indicator legend' });
  await expect(indicatorLegend.getByRole('button', { name: 'Open RSI 14 settings' })).toBeVisible();
  await expect(indicatorLegend.getByRole('button', { name: 'Hide RSI 14 indicator' })).toBeVisible();
  await indicatorLegend.getByRole('button', { name: 'Collapse indicator legend' }).click();
  const expandLegend = indicatorLegend.getByRole('button', { name: 'Expand indicator legend' });
  await expect(expandLegend).toHaveText(/2/);
  await expect(expandLegend).toHaveCSS('flex-direction', 'row');
  await expect(indicatorLegend.getByRole('button', { name: 'Open RSI 14 settings' })).toHaveCount(0);
  await expandLegend.click();
  await expect(indicatorLegend.getByRole('button', { name: 'Open RSI 14 settings' })).toBeVisible();
  await rsiControls.hover();
  await expect(rsiControls).toHaveCSS('opacity', '1');
  await expect(page.getByRole('button', { name: 'Move RSI 14 panel up' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Move RSI 14 panel down' })).toBeDisabled();
  const enterRsiFullscreen = page.getByRole('button', { name: 'Enter fullscreen RSI 14 panel' });
  await expect(enterRsiFullscreen).toBeVisible();
  await enterRsiFullscreen.click();
  const exitRsiFullscreen = page.getByRole('button', { name: 'Exit fullscreen RSI 14 panel' });
  await expect(exitRsiFullscreen).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.trading-chart-panel.is-chart-focus-mode')).toHaveCount(1);
  await expect(page.locator('.trading-chart-panel.is-chart-focus-mode .trading-indicator-pane-controls')).toHaveCount(1);
  await exitRsiFullscreen.click();
  await expect(page.getByRole('button', { name: 'Enter fullscreen RSI 14 panel' })).toHaveAttribute('aria-pressed', 'false');

  const enterChartFocus = page.getByRole('button', { name: 'Focus this chart' });
  await enterChartFocus.click();
  await expect(page.locator('.trading-chart-panel.is-chart-focus-mode')).toHaveCount(1);
  await expect(page.locator('.trading-chart-panel.is-chart-focus-mode .trading-indicator-pane-controls')).toHaveCount(0);
  await page.getByRole('button', { name: 'Exit chart focus mode' }).click();
  await expect(page.getByRole('button', { name: 'Focus this chart' })).toHaveAttribute('aria-pressed', 'false');

  await page.getByRole('button', { name: 'Minimize RSI 14 panel' }).click();
  await expect(page.getByRole('button', { name: 'Restore RSI 14 panel' })).toHaveAttribute('aria-expanded', 'false');
  await page.getByRole('button', { name: 'Restore RSI 14 panel' }).click();

  await page.locator('.trading-indicator-manager').getByRole('button', { name: 'Indicators' }).click();
  await page.getByRole('button', { name: 'MACD 9' }).click();
  await expect(page.locator('.trading-indicator-pane-controls')).toHaveCount(2);
  await page.getByRole('button', { name: 'Move RSI 14 panel down' }).click();
  await expect.poll(async () => page.locator('.trading-indicator-pane-controls').evaluateAll((items) => items.map((item) => item.getAttribute('data-indicator-id')))).toEqual(['macd', 'rsi']);

  await page.getByRole('button', { name: 'Close RSI 14 panel' }).click();
  await expect(page.locator('.trading-indicator-pane-controls[data-indicator-id="rsi"]')).toHaveCount(0);
});

test('selecting an indicator alert loads the missing indicator on its chart', async ({ page }) => {
  const state = await installTradingMocks(page);
  await page.goto('/trading');

  await page.getByRole('button', { name: 'Right panel', exact: true }).click();
  const sidePanel = page.getByRole('complementary', { name: 'Trading side panel', exact: true });
  await sidePanel.getByRole('tab', { name: 'Alerts', exact: true }).click();
  await sidePanel.getByRole('button', { name: 'Add alert' }).click();

  const alertDialog = page.getByRole('dialog', { name: /Create alert on BTCUSDT/ });
  await alertDialog.getByRole('combobox', { name: 'Alert condition' }).selectOption('indicator_above');
  await alertDialog.getByRole('combobox', { name: 'Alert indicator' }).selectOption('stochastic-rsi');
  await alertDialog.getByRole('textbox', { name: 'Alert indicator period' }).fill('14');
  await alertDialog.getByRole('textbox', { name: 'Alert value' }).fill('80');
  await alertDialog.getByRole('button', { name: 'Create alert' }).click();

  await expect.poll(() => state.alerts.length).toBe(1);
  expect((state.alerts[0].parameters as Record<string, unknown>).indicator_id).toBe('stochastic-rsi');
  await expect(page.locator('.trading-chart-panel.active .trading-overlay-indicator-label').filter({ hasText: 'STOCHASTIC-RSI 14' })).toHaveCount(0);

  await sidePanel.getByRole('button', { name: /Open BTCUSDT chart for Stoch RSI/ }).click();
  await expect(page.locator('.trading-chart-panel.active .trading-overlay-indicator-label').filter({ hasText: 'STOCHASTIC-RSI 14' })).toBeVisible();
});

test('Volume Profile renders volume-at-price bars along the price scale', async ({ page }) => {
  await installTradingMocks(page);
  await page.goto('/trading');

  await page.locator('.trading-indicator-manager').getByRole('button', { name: 'Indicators' }).click();
  await page.getByRole('button', { name: 'Volume Profile', exact: true }).click();

  const profileOverlay = page.locator('.trading-volume-profile-overlay');
  await expect(profileOverlay).toBeVisible();
  await expect(profileOverlay.locator('[data-volume-profile-bin]')).not.toHaveCount(0);
  await expect(profileOverlay.locator('[data-volume-profile-bin].is-poc')).toHaveCount(1);
});

test('right panel exposes TradingView-style Object tree and Data window views', async ({ page }) => {
  await installTradingMocks(page);
  await page.goto('/trading');

  await page.getByRole('button', { name: 'Object tree' }).click();
  const objectPanel = page.getByRole('complementary', { name: 'Trading object tree and data window' });
  await expect(objectPanel).toBeVisible();
  await expect(objectPanel.getByRole('tab', { name: 'Object tree' })).toHaveAttribute('aria-selected', 'true');
  await expect(objectPanel).toContainText('BTCUSDT');
  await expect(objectPanel).toContainText('Indicators');
  await expect(objectPanel).toContainText('Simple Moving Average 20');

  await objectPanel.getByRole('tab', { name: 'Data window' }).click();
  await expect(objectPanel.getByRole('tab', { name: 'Data window' })).toHaveAttribute('aria-selected', 'true');
  await expect(objectPanel.locator('.trading-data-window-date strong')).not.toHaveText('—');
  await expect(objectPanel).toContainText('Open');
  await expect(objectPanel).toContainText('Close');
  await expect(objectPanel).toContainText('Relative Strength Index (14)');
});

test('chart session tabs isolate symbols and can be closed', async ({ page }) => {
  await installTradingMocks(page);
  await page.goto('/trading');

  const tabs = page.getByRole('group', { name: 'Independent chart sessions' });
  const sessionTab = { name: /^Open .* chart session$/ };
  await expect(tabs.getByRole('button', sessionTab)).toHaveCount(1);
  // The + button opens the new-tab launcher (TVP-4.x); a new chart tab is its first item.
  await page.getByRole('button', { name: 'Create chart session tab' }).click();
  await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: 'New chart tab' }).click();
  await expect(tabs.getByRole('button', sessionTab)).toHaveCount(2);

  await page.getByRole('button', { name: 'Open symbol search' }).click();
  const symbolSearch = page.getByRole('dialog', { name: 'Symbol search' });
  await symbolSearch.getByRole('textbox', { name: 'Search symbols' }).fill('ETH');
  await symbolSearch.getByRole('button', { name: /ETHUSDT/ }).click();
  await expect(page.locator('.trading-chart-panel').first()).toContainText('ETHUSDT');

  const sessionTabs = tabs.getByRole('button', sessionTab);
  await sessionTabs.nth(0).click();
  await expect(page.locator('.trading-chart-panel').first()).toContainText('BTCUSDT');
  await sessionTabs.nth(1).click();
  await expect(page.locator('.trading-chart-panel').first()).toContainText('ETHUSDT');

  page.once('dialog', (dialog) => dialog.accept());
  await tabs.getByRole('button', { name: /Close ETHUSDT chart session/ }).click();
  await expect(tabs.getByRole('button', sessionTab)).toHaveCount(1);
});

test('chart layout picker applies TradingView-style templates', async ({ page }) => {
  await installTradingMocks(page);
  await page.goto('/trading');

  await page.getByRole('button', { name: 'Open chart layout menu' }).click();
  const layoutMenu = page.getByRole('menu', { name: 'Chart layouts' });
  await expect(layoutMenu).toBeVisible();
  await expect(layoutMenu.getByRole('switch', { name: 'Sync Symbol' })).toBeVisible();
  await layoutMenu.getByRole('switch', { name: 'Sync Symbol' }).click();
  await expect(layoutMenu.getByRole('switch', { name: 'Sync Symbol' })).toHaveAttribute('aria-checked', 'true');
  await expect(layoutMenu.getByRole('menuitemradio', { name: /2 charts · Columns/ })).toBeVisible();
  await layoutMenu.getByRole('menuitemradio', { name: /2 charts · Columns/ }).click();
  await expect(page.locator('.trading-chart-grid')).toHaveClass(/layout-columns-2/);
  await expect(page.locator('.trading-chart-grid-cell')).toHaveCount(2);

  await page.getByRole('button', { name: 'Open chart layout menu' }).click();
  await page.getByRole('menu', { name: 'Chart layouts' }).getByRole('menuitemradio', { name: /3 charts · Main left/ }).click();
  await expect(page.locator('.trading-chart-grid')).toHaveClass(/layout-main-left-3/);
  await expect(page.locator('.trading-chart-grid-cell')).toHaveCount(3);
});
