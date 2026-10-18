import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type StatusListener = (status: string) => void;

const streamHub = vi.hoisted(() => ({ subscribe: vi.fn<(...args: unknown[]) => () => void>(() => () => undefined) }));

vi.mock('./streaming/tradingStreamHub', () => ({ tradingStreamHub: streamHub }));

import { useChartView } from './useTradingChartPanelView';
import { fixture } from '../../test/fixture';

type ViewInput = Parameters<typeof useChartView>[0];

const BINANCE = { binding_id: 'binance-btc', provider: 'binance', feed_type: 'websocket_and_rest', supported_intervals: ['1m'] };
const IBKR = { binding_id: 'ibkr-aapl', provider: 'ibkr', feed_type: 'socket', supported_intervals: ['1m'] };
const YAHOO = { binding_id: 'yahoo-aapl', provider: 'yahoo', feed_type: 'historical_polling', supported_intervals: ['1m', '5m'] };

function viewModel(
  replayMode: boolean,
  setStreamStatus: (status: string) => void,
  options: { binding?: object; instrumentId?: string; interval?: string; extendedHours?: boolean; refetch?: () => unknown } = {},
): ViewInput {
  return fixture<ViewInput>({
    active: false,
    adapterRef: { current: null },
    allBarsRef: { current: [] },
    barsRef: { current: [] },
    chartFocusMode: false,
    chartId: 'chart-2',
    chartQuery: {
      data: { bars: [], binding: options.binding ?? BINANCE },
      refetch: options.refetch ?? vi.fn(),
    },
    chartSettings: options.extendedHours === undefined ? undefined : { extendedHours: options.extendedHours },
    comparisonRenderData: [],
    contextMenu: null,
    forceLiveRender: vi.fn(),
    fullscreenIndicator: null,
    fullscreenIndicatorRef: { current: null },
    fullscreenMainPane: false,
    fullscreenMainPaneRef: { current: false },
    indicatorOutputs: [],
    indicators: [],
    instrumentId: options.instrumentId ?? 'crypto:BINANCE:spot:BTC-USDT',
    interval: options.interval ?? '1m',
    replayMode,
    scheduleIndicators: vi.fn(),
    selectedIndicator: null,
    setFullscreenIndicator: vi.fn(),
    setFullscreenMainPane: vi.fn(),
    setStreamError: vi.fn(),
    setStreamStatus,
    streamRevisionRef: { current: 1 },
    timezoneId: 'exchange',
  });
}

beforeEach(() => {
  streamHub.subscribe.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('chart streaming around replay', () => {
  it('stops streaming on every chart during replay and resumes it at real time', () => {
    const setStreamStatus = vi.fn();
    const view = renderHook((props: ViewInput) => useChartView(props), { initialProps: viewModel(true, setStreamStatus) });

    expect(streamHub.subscribe).not.toHaveBeenCalled();
    expect(setStreamStatus).toHaveBeenLastCalledWith('replay');

    view.rerender(viewModel(false, setStreamStatus));

    expect(streamHub.subscribe).toHaveBeenCalledOnce();
    // 24x7 streams carry no extended-hours scope.
    expect(streamHub.subscribe).toHaveBeenCalledWith('chart-2', 'crypto:BINANCE:spot:BTC-USDT', '1m', expect.any(Function), expect.any(Function), 'binance-btc', null);
  });
});

describe('live IBKR stock charts', () => {
  it('streams intraday IBKR charts, including derived intervals, with the chart extended-hours setting', () => {
    renderHook(() => useChartView(viewModel(false, vi.fn(), { binding: IBKR, instrumentId: 'equity:NASDAQ:AAPL', interval: '5m', extendedHours: false })));

    expect(streamHub.subscribe).toHaveBeenCalledWith('chart-2', 'equity:NASDAQ:AAPL', '5m', expect.any(Function), expect.any(Function), 'ibkr-aapl', false);
  });

  it('polls daily IBKR charts and Yahoo charts instead of streaming them', () => {
    const setStreamStatus = vi.fn();
    renderHook(() => useChartView(viewModel(false, setStreamStatus, { binding: IBKR, instrumentId: 'equity:NASDAQ:AAPL', interval: '1d' })));
    renderHook(() => useChartView(viewModel(false, setStreamStatus, { binding: YAHOO, instrumentId: 'equity:NASDAQ:AAPL', interval: '1m' })));

    expect(streamHub.subscribe).not.toHaveBeenCalled();
    expect(setStreamStatus).toHaveBeenLastCalledWith('polling');
  });

  it('reloads the history once when the first live bar leaves a gap after it', () => {
    const lastBar = { start_time: '2026-09-17T13:30:00+00:00', end_time: '2026-09-17T13:31:00+00:00' };
    const liveBar = (start: string) => ({
      type: 'bar',
      bar: { instrument_id: 'equity:NASDAQ:AAPL', binding_id: 'ibkr-aapl', interval: '1m', start_time: start, end_time: start, open: '1', high: '1', low: '1', close: '1', volume: '1', is_final: false, ingestion_revision: 1 },
    });
    const subscribeWithHistory = (refetch: () => unknown) => {
      streamHub.subscribe.mockClear();
      const model = viewModel(false, vi.fn(), { binding: IBKR, instrumentId: 'equity:NASDAQ:AAPL', interval: '1m', refetch });
      renderHook(() => useChartView({ ...model, barsRef: { current: [lastBar] } as ViewInput['barsRef'] }));
      return streamHub.subscribe.mock.calls[0][3] as (message: unknown) => void;
    };

    // 13:31 closed between the history request and the stream's first bar (13:32).
    const gapRefetch = vi.fn();
    const onGapMessage = subscribeWithHistory(gapRefetch);
    onGapMessage(liveBar('2026-09-17T13:32:00+00:00'));
    onGapMessage(liveBar('2026-09-17T13:34:00+00:00'));
    expect(gapRefetch).toHaveBeenCalledTimes(1);

    // The first live bar continues the history: nothing to reload.
    const contiguousRefetch = vi.fn();
    subscribeWithHistory(contiguousRefetch)(liveBar('2026-09-17T13:31:00+00:00'));
    expect(contiguousRefetch).not.toHaveBeenCalled();
  });

  it('polls while the server reconnects its upstream and stops once it is live again', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const view = renderHook(() => useChartView(viewModel(false, vi.fn(), { binding: IBKR, instrumentId: 'equity:NASDAQ:AAPL', interval: '1m', refetch })));
    const onStatus = streamHub.subscribe.mock.calls[0][4] as StatusListener;

    act(() => onStatus('polling'));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(refetch).toHaveBeenCalledTimes(1);

    act(() => onStatus('live'));
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(refetch).toHaveBeenCalledTimes(1);

    // Disposing the chart stops a poll that is still running.
    act(() => onStatus('polling'));
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
