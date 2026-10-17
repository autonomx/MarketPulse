import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const streamHub = vi.hoisted(() => ({ subscribe: vi.fn(() => () => undefined) }));

vi.mock('./streaming/tradingStreamHub', () => ({ tradingStreamHub: streamHub }));

import { useChartView } from './useTradingChartPanelView';
import { fixture } from '../../test/fixture';

type ViewInput = Parameters<typeof useChartView>[0];

function viewModel(replayMode: boolean, setStreamStatus: (status: string) => void): ViewInput {
  return fixture<ViewInput>({
    active: false,
    adapterRef: { current: null },
    allBarsRef: { current: [] },
    barsRef: { current: [] },
    chartFocusMode: false,
    chartId: 'chart-2',
    chartQuery: {
      data: {
        bars: [],
        binding: { binding_id: 'binance-btc', provider: 'binance', feed_type: 'websocket_and_rest', supported_intervals: ['1m'] },
      },
      refetch: vi.fn(),
    },
    comparisonRenderData: [],
    contextMenu: null,
    forceLiveRender: vi.fn(),
    fullscreenIndicator: null,
    fullscreenIndicatorRef: { current: null },
    fullscreenMainPane: false,
    fullscreenMainPaneRef: { current: false },
    indicatorOutputs: [],
    indicators: [],
    instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
    interval: '1m',
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

describe('chart streaming around replay', () => {
  it('stops streaming on every chart during replay and resumes it at real time', () => {
    const setStreamStatus = vi.fn();
    const view = renderHook((props: ViewInput) => useChartView(props), { initialProps: viewModel(true, setStreamStatus) });

    expect(streamHub.subscribe).not.toHaveBeenCalled();
    expect(setStreamStatus).toHaveBeenLastCalledWith('replay');

    view.rerender(viewModel(false, setStreamStatus));

    expect(streamHub.subscribe).toHaveBeenCalledOnce();
    expect(streamHub.subscribe).toHaveBeenCalledWith('chart-2', 'crypto:BINANCE:spot:BTC-USDT', '1m', expect.any(Function), expect.any(Function), 'binance-btc');
  });
});
