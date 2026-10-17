import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tradingApi } from './tradingApi';
import { useTradingStore } from './tradingStore';
import type { MarketBar } from './tradingTypes';
import { useChartWorkflow } from './useTradingChartPanelWorkflow';

type WorkflowInput = Parameters<typeof useChartWorkflow>[0];

function hourlyBars(firstIso: string, count: number, overrides: Partial<MarketBar> = {}): MarketBar[] {
  const first = Date.parse(firstIso);
  return Array.from({ length: count }, (_, index) => {
    const start = first + index * 3_600_000;
    return {
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      interval: '1h',
      start_time: new Date(start).toISOString(),
      end_time: new Date(start + 3_600_000).toISOString(),
      open: '1', high: '1', low: '1', close: '1', volume: '1',
      is_final: true,
      adjustment_mode: 'raw',
      session: '24x7',
      provider: 'binance',
      provider_event_id: String(start),
      provider_sequence: null,
      ingestion_revision: 1,
      received_at: new Date(start).toISOString(),
      ...overrides,
    } as MarketBar;
  });
}

function fakeAdapter() {
  const timeScale = {
    setVisibleLogicalRange: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => ({ from: 40, to: 100 })),
  };
  return {
    timeScale,
    adapter: {
      api: () => ({ timeScale: () => timeScale }),
      setSessionPriceLine: vi.fn(),
      snapshotDataUrl: vi.fn(() => 'data:image/png;base64,iVBORw0KGgo='),
      isPriceScaleCoordinate: vi.fn(() => false),
      paneAtClientY: vi.fn(() => ({ kind: 'indicator', id: 'rsi' })),
    },
  };
}

function workflowInput(overrides: Partial<Record<string, unknown>> = {}) {
  const { adapter, timeScale } = fakeAdapter();
  const bars = hourlyBars('2026-10-01T00:00:00Z', 100);
  const host = document.createElement('div');
  const input = {
    active: true,
    adapter,
    adapterRef: { current: adapter },
    allBarsRef: { current: bars },
    chartFocusMode: false,
    chartId: 'chart-1',
    chartQuery: { data: { bars }, isFetching: false },
    chartSettings: undefined,
    chartType: 'candlestick',
    drawingTool: 'cursor',
    fullscreenIndicator: null,
    fullscreenMainPane: false,
    historyLimit: 100,
    hostRef: { current: host },
    indicators: [{ id: 'rsi', period: 14, enabled: true }],
    instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
    interval: '1h',
    loadedBars: bars,
    paneIndicators: [{ id: 'rsi', period: 14, enabled: true }],
    provenance: { delay_seconds: 0, freshness_mode: 'live' },
    replayMode: false,
    resolvedBinding: { delay_seconds: 900 },
    selectedRangeRef: { current: 7 },
    selectedTimezone: 'UTC',
    setHistoryLimitOverride: vi.fn(),
    setSelectedRangeLabel: vi.fn(),
    showExtendedHours: true,
    toggleFullscreen: vi.fn(),
    toggleFullscreenIndicator: vi.fn(),
    toggleMinimizedIndicator: vi.fn(),
    ...overrides,
  };
  return { input: input as unknown as WorkflowInput, raw: input, timeScale, adapter, host };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** An always-open market status for every workflow test. */
function mockMarketStatus(): void {
  beforeEach(() => {
    vi.spyOn(tradingApi, 'marketStatus').mockResolvedValue({
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      session_calendar: '24x7',
      exchange_timezone: 'UTC',
      status: 'open',
      always_open: true,
      as_of: '2026-10-08T00:00:00Z',
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
}

describe('chart workflow hook (TVP-2.5)', () => {
  mockMarketStatus();

  it('scrolls straight to a loaded date and centres it', () => {
    const { input, timeScale, raw } = workflowInput();
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    let outcome: string | undefined;
    act(() => { outcome = result.current.goToDate('2026-10-03T00:00'); });
    expect(outcome).toBe('scrolled');
    expect(timeScale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 18, to: 78 });
    expect(raw.selectedRangeRef.current).toBeNull();
    expect(raw.setHistoryLimitOverride).not.toHaveBeenCalled();
  });

  it('loads older history on the existing bars request, then scrolls when it arrives', () => {
    const setup = workflowInput();
    let current = setup.input;
    const { result, rerender } = renderHook(() => useChartWorkflow(current), { wrapper });
    let outcome: string | undefined;
    act(() => { outcome = result.current.goToDate('2026-09-25'); });
    expect(outcome).toBe('loading');
    expect(result.current.goToDateLoading).toBe(true);
    const [[override]] = setup.raw.setHistoryLimitOverride.mock.calls as [[{ key: string; limit: number }]];
    expect(override.key).toBe('crypto:BINANCE:spot:BTC-USDT|1h');
    expect(override.limit).toBeGreaterThan(100);

    const older = hourlyBars('2026-09-20T00:00:00Z', 316);
    current = workflowInput({
      ...setup.raw,
      allBarsRef: { current: older },
      loadedBars: older,
      chartQuery: { data: { bars: older }, isFetching: false },
      historyLimit: override.limit,
    }).input;
    rerender();
    // 2026-09-25T00:00Z is 120 bars after 2026-09-20T00:00Z.
    expect(setup.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 90, to: 150 });
    expect(result.current.goToDateLoading).toBe(false);
    expect(result.current.goToDateError).toBeNull();
  });

  it('says so when the feed has no older history', () => {
    const setup = workflowInput();
    let current = setup.input;
    const { result, rerender } = renderHook(() => useChartWorkflow(current), { wrapper });
    act(() => { result.current.goToDate('2026-09-25'); });
    // The larger request returned the same first bar: the feed's history starts there.
    current = workflowInput({ ...setup.raw, loadedBars: [...setup.raw.loadedBars as MarketBar[]], historyLimit: 500 }).input;
    rerender();
    expect(result.current.goToDateLoading).toBe(false);
    expect(result.current.goToDateError).toBe('The feed serves history back to 2026-10-01 only.');
  });

  it('distinguishes complete history, the bar cap and a failed fetch', () => {
    const complete = workflowInput({ provenance: { delay_seconds: 0, freshness_mode: 'live', history_complete: true } });
    const { result: completeResult } = renderHook(() => useChartWorkflow(complete.input), { wrapper });
    act(() => { expect(completeResult.current.goToDate('2026-09-25T00:00')).toBe('unavailable'); });
    expect(completeResult.current.goToDateError).toBe("No earlier history exists: this market's data starts 2026-10-01.");

    const capped = workflowInput({ historyLimit: 5_000 });
    const { result: cappedResult } = renderHook(() => useChartWorkflow(capped.input), { wrapper });
    act(() => { cappedResult.current.goToDate('2026-09-25T00:00'); });
    expect(cappedResult.current.goToDateError).toBe('The chart loads at most 5,000 bars, back to 2026-10-01. A longer interval reaches further back.');

    const setup = workflowInput();
    let current = setup.input;
    const { result, rerender } = renderHook(() => useChartWorkflow(current), { wrapper });
    act(() => { result.current.goToDate('2026-09-25'); });
    current = workflowInput({ ...setup.raw, chartQuery: { data: setup.raw.chartQuery.data, isFetching: false, isError: true, error: new Error('Trading request failed (502)') } }).input;
    rerender();
    expect(result.current.goToDateError).toBe('Could not load older history: Trading request failed (502)');
    expect(result.current.goToDateLoading).toBe(false);
  });

  it('rejects go to date during replay and for unparseable input', () => {
    const replay = workflowInput({ replayMode: true });
    const { result } = renderHook(() => useChartWorkflow(replay.input), { wrapper });
    act(() => { expect(result.current.goToDate('2026-10-02')).toBe('invalid'); });
    const live = workflowInput();
    const { result: liveResult } = renderHook(() => useChartWorkflow(live.input), { wrapper });
    act(() => { expect(liveResult.current.goToDate('yesterday')).toBe('invalid'); });
    expect(liveResult.current.goToDateError).toBe('Choose a date.');
  });

  it('copies the chart image to the clipboard', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('ClipboardItem', class { constructor(public readonly items: Record<string, Blob>) {} });
    Object.defineProperty(navigator, 'clipboard', { value: { write }, configurable: true });
    const { input, adapter } = workflowInput();
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    let copied = false;
    await act(async () => { copied = await result.current.copyChartImage(); });
    expect(copied).toBe(true);
    expect(adapter.snapshotDataUrl).toHaveBeenCalled();
    expect(write).toHaveBeenCalledTimes(1);
    expect(result.current.chartImageCopyStatus).toBe('copied');
  });

  it('maximises a pane on double-click and collapses it on Ctrl+double-click', () => {
    const { input, raw, host } = workflowInput();
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    const event = (ctrlKey: boolean) => ({
      target: host, clientX: 100, clientY: 200, ctrlKey, metaKey: false, preventDefault: vi.fn(),
    }) as unknown as React.MouseEvent<HTMLDivElement>;
    result.current.handleStageDoubleClick(event(false));
    expect(raw.toggleFullscreenIndicator).toHaveBeenCalledWith('rsi');
    result.current.handleStageDoubleClick(event(true));
    expect(raw.toggleMinimizedIndicator).toHaveBeenCalledWith('rsi');
    (raw.adapter as ReturnType<typeof fakeAdapter>['adapter']).paneAtClientY.mockReturnValue({ kind: 'main' } as never);
    result.current.handleStageDoubleClick(event(false));
    expect(raw.toggleFullscreen).toHaveBeenCalledTimes(1);
  });

  it('ignores double-clicks on the price scale and while a drawing tool is active', () => {
    const scale = workflowInput();
    (scale.adapter.isPriceScaleCoordinate as ReturnType<typeof vi.fn>).mockReturnValue(true);
    const { result } = renderHook(() => useChartWorkflow(scale.input), { wrapper });
    const event = { target: scale.host, clientX: 1, clientY: 1, ctrlKey: false, metaKey: false, preventDefault: vi.fn() } as unknown as React.MouseEvent<HTMLDivElement>;
    result.current.handleStageDoubleClick(event);
    expect(scale.raw.toggleFullscreenIndicator).not.toHaveBeenCalled();
    const drawing = workflowInput({ drawingTool: 'trend-line' });
    const { result: drawingResult } = renderHook(() => useChartWorkflow(drawing.input), { wrapper });
    drawingResult.current.handleStageDoubleClick({ ...event, target: drawing.host } as unknown as React.MouseEvent<HTMLDivElement>);
    expect(drawing.raw.toggleFullscreenIndicator).not.toHaveBeenCalled();
  });

  it('draws the post-market price line and reports market status and delay', async () => {
    vi.mocked(tradingApi.marketStatus).mockResolvedValue({
      instrument_id: 'equity:NASDAQ:AAPL', session_calendar: 'XNYS', exchange_timezone: 'America/New_York',
      status: 'post_market', always_open: false, as_of: '2026-10-07T21:00:00Z',
    });
    const bars = hourlyBars('2026-10-07T18:00:00Z', 3, { session: 'regular' });
    bars[2] = { ...bars[2], session: 'extended_post', close: '187.5' };
    const { input, adapter } = workflowInput({ chartQuery: { data: { bars }, isFetching: false } });
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    expect(adapter.setSessionPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ price: 187.5, title: 'Post-market' }));
    expect(result.current.extendedHoursAvailable).toBe(true);
    expect(result.current.dataDelay).toBe('Delayed 15 min');
    await vi.waitFor(() => expect(result.current.marketStatus).toBe('Post-market'));
    expect(adapter.setSessionPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ title: 'Post-market' }));
  });
});

describe('chart workflow hook (TVP-2.5)', () => {
  mockMarketStatus();

  it('never shows a pre/post-market line while the regular session is open', async () => {
    vi.mocked(tradingApi.marketStatus).mockResolvedValue({
      instrument_id: 'equity:NASDAQ:AAPL', session_calendar: 'XNYS', exchange_timezone: 'America/New_York',
      status: 'open', always_open: false, as_of: '2026-10-07T15:00:00Z',
    });
    const bars = hourlyBars('2026-10-07T12:00:00Z', 2, { session: 'extended_pre' });
    const { input, adapter } = workflowInput({ chartQuery: { data: { bars }, isFetching: false } });
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    await vi.waitFor(() => expect(result.current.marketStatus).toBe('Market open'));
    expect(adapter.setSessionPriceLine).toHaveBeenLastCalledWith(null);
  });

  it('reports a 24/7 market', async () => {
    const { input } = workflowInput();
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    await vi.waitFor(() => expect(result.current.marketStatus).toBe('Market open 24/7'));
  });

  it('updates chart settings and applies a template without touching the symbol or interval', () => {
    useTradingStore.setState({
      charts: [{ chartId: 'chart-1', instrumentId: 'btc', bindingId: null, interval: '7m', chartType: 'candlestick', indicators: [] }],
      activeChartId: 'chart-1',
    });
    const { input } = workflowInput();
    const { result } = renderHook(() => useChartWorkflow(input), { wrapper });
    act(() => result.current.updateChartSettings({ extendedHours: false }));
    act(() => result.current.applyChartTemplateRecord({
      recordId: 't', name: 'T', chartType: 'line', settings: { barCountdown: false }, indicators: [{ id: 'ema', period: 9, enabled: true }],
    }));
    const chart = useTradingStore.getState().charts[0];
    expect(chart).toMatchObject({ instrumentId: 'btc', interval: '7m', chartType: 'line', settings: { barCountdown: false } });
    expect(chart.indicators).toEqual([{ id: 'ema', period: 9, enabled: true }]);
  });
});
