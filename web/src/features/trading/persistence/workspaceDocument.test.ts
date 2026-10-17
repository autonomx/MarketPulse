import { describe, expect, it, vi } from 'vitest';
import { parseTradingWorkspace, serializeTradingWorkspace } from './workspaceDocument';

const state = {
  name: 'Research desk',
  layout: 'auto' as const,
  activeChartId: 'chart-2',
  charts: [
    { chartId: 'chart-1', instrumentId: 'btc', interval: '2h', chartType: 'candlestick' as const },
    { chartId: 'chart-2', instrumentId: 'eth', interval: '1w', chartType: 'line' as const },
    { chartId: 'chart-3', instrumentId: 'sol', interval: '4h', chartType: 'area' as const },
  ],
  links: { instrument: false, interval: true, crosshair: true, visibleRange: false },
  panels: { right: false, bottom: true },
  favoriteInstrumentIds: ['eth', 'btc', 'eth'],
};

describe('Trading workspace document', () => {
  it('round trips exact charts, panels, favorites, active chart, and links', () => {
    const serialized = serializeTradingWorkspace(state);
    expect(serialized.schemaVersion).toBe(3);
    expect(serialized.panels).toEqual({ right: false, bottom: true });
    expect(serialized.favoriteInstrumentIds).toEqual(['eth', 'btc']);
    expect(parseTradingWorkspace(serialized)).toEqual(serialized);
  });

  it('round trips independent chart session tabs when present', () => {
    const serialized = serializeTradingWorkspace({
      ...state,
      activeTabId: 'tab-2',
      tabs: [
        { tabId: 'tab-1', name: 'Main Session', layout: 'auto', activeChartId: 'chart-1', charts: state.charts.slice(0, 1).map((chart) => ({ ...chart, bindingId: null, indicators: [] })), links: state.links, panels: state.panels },
        { tabId: 'tab-2', name: 'Swing Research', layout: 'columns-2', activeChartId: 'chart-2', charts: state.charts.slice(1).map((chart) => ({ ...chart, bindingId: null, indicators: [] })), links: state.links, panels: state.panels },
      ],
    });
    expect(serialized.activeTabId).toBe('tab-2');
    expect(serialized.tabs?.map((tab) => tab.name)).toEqual(['Main Session', 'Swing Research']);
    expect(parseTradingWorkspace(serialized)).toEqual(serialized);
  });

  it('migrates fixed version-one layouts without exposing hidden charts', () => {
    const migrated = parseTradingWorkspace({
      schemaVersion: 1,
      layout: 'one',
      activeChartId: 'chart-3',
      charts: [
        { chartId: 'chart-1', instrumentId: 'btc', bindingId: null, interval: '1m', chartType: 'candlestick', indicators: [] },
        { chartId: 'chart-2', instrumentId: 'eth', bindingId: null, interval: '5m', chartType: 'candlestick', indicators: [] },
        { chartId: 'chart-3', instrumentId: 'sol', bindingId: null, interval: '15m', chartType: 'candlestick', indicators: [] },
        { chartId: 'chart-4', instrumentId: 'spy', bindingId: null, interval: '1d', chartType: 'line', indicators: [] },
      ],
      links: state.links,
      panels: { right: false },
      favoriteInstrumentIds: ['sol'],
    });
    expect(migrated?.schemaVersion).toBe(3);
    expect(migrated?.layout).toBe('columns-1');
    expect(migrated?.charts.map((chart) => chart.chartId)).toEqual(['chart-3']);
    expect(migrated?.activeChartId).toBe('chart-3');
    expect(migrated?.panels).toEqual({ right: false, bottom: true });
    expect(migrated?.favoriteInstrumentIds).toEqual(['sol']);
  });

  it('migrates saved Coinbase spot charts and favorites to Binance', () => {
    const migrated = parseTradingWorkspace({
      schemaVersion: 2,
      name: 'Main Workspace',
      layout: 'auto',
      activeChartId: 'chart-1',
      charts: [{
        chartId: 'chart-1',
        instrumentId: 'crypto:COINBASE:spot:BTC-USD',
        bindingId: 'coinbase:rest:crypto:COINBASE:spot:BTC-USD',
        interval: '1d',
        chartType: 'candlestick',
        indicators: [],
      }],
      links: state.links,
      panels: state.panels,
      favoriteInstrumentIds: ['crypto:COINBASE:spot:BTC-USD'],
    });

    expect(migrated?.charts[0]).toMatchObject({
      instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
      bindingId: null,
    });
    expect(migrated?.favoriteInstrumentIds).toEqual(['crypto:BINANCE:spot:BTC-USDT']);
  });

  it('migrates legacy linked ranges to independent chart navigation', () => {
    const migrated = parseTradingWorkspace({
      schemaVersion: 2,
      layout: 'auto',
      charts: state.charts,
      links: { ...state.links, visibleRange: true },
    });

    expect(migrated?.schemaVersion).toBe(3);
    expect(migrated?.links.visibleRange).toBe(false);
  });

  it('rejects unknown schema versions, layouts, and malformed charts', () => {
    expect(parseTradingWorkspace({ schemaVersion: 4, layout: 'auto', charts: [], links: state.links })).toBeNull();
    expect(parseTradingWorkspace({ schemaVersion: 2, layout: 'four', charts: state.charts, links: state.links })).toBeNull();
    expect(parseTradingWorkspace({
      schemaVersion: 2,
      layout: 'auto',
      charts: [{ chartId: 42 }],
      links: state.links,
    })).toBeNull();
  });

  it('round trips interval favourites and per-chart settings (TVP-2.5)', () => {
    const serialized = serializeTradingWorkspace({
      ...state,
      favoriteIntervals: ['7m', '4h'],
      charts: [{ ...state.charts[0], interval: '7m', settings: { barCountdown: false, extendedHours: false } }],
      activeChartId: 'chart-1',
    });
    expect(serialized.favoriteIntervals).toEqual(['7m', '4h']);
    expect(serialized.charts[0].settings).toEqual({ barCountdown: false, extendedHours: false });
    expect(parseTradingWorkspace(serialized)).toEqual(serialized);
  });

  it('drops malformed favourites and chart settings without failing the workspace', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const legacy = serializeTradingWorkspace(state);
    expect(parseTradingWorkspace(legacy)?.favoriteIntervals).toBeUndefined();
    expect(parseTradingWorkspace({ ...legacy, favoriteIntervals: ['7m', 'nonsense', 3] })?.favoriteIntervals).toEqual(['7m']);
    const parsed = parseTradingWorkspace({
      ...legacy,
      charts: [{ ...legacy.charts[0], settings: { extendedHours: 'no', barCountdown: false } }, legacy.charts[1]],
    });
    expect(parsed?.charts[0].settings).toEqual({ barCountdown: false });
    expect(parsed?.charts).toHaveLength(2);
    expect(parseTradingWorkspace({ ...legacy, charts: [{ ...legacy.charts[0], settings: 'broken' }] })?.charts[0].settings).toBeUndefined();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
    expect(parseTradingWorkspace({
      ...legacy,
      charts: [{ ...legacy.charts[0], settings: { extendedHours: true, unknown: 1 } }],
    })?.charts[0].settings).toEqual({ extendedHours: true });
  });

  it('does not persist runtime functions or provider payloads', () => {
    const serialized = serializeTradingWorkspace(state);
    const text = JSON.stringify(serialized);
    expect(text).not.toContain('setLayout');
    expect(text).not.toContain('rawProviderPayload');
    expect(serialized.charts[0].instrumentId).toBe('btc');
  });
});
