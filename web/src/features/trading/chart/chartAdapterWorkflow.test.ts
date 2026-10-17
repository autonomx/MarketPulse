import { beforeEach, describe, expect, it, vi } from 'vitest';

type SeriesMock = ReturnType<typeof seriesMock>;

const series: SeriesMock[] = [];
let paneRects: Array<{ top: number; height: number }> = [];

function seriesMock() {
  return {
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: vi.fn(),
    priceScale: () => ({ applyOptions: vi.fn() }),
    createPriceLine: vi.fn((options: Record<string, unknown>) => ({ options })),
    removePriceLine: vi.fn(),
    lastValueData: vi.fn(() => ({ noData: false, price: 101, color: '#20c997' })),
    priceToCoordinate: vi.fn(() => 120),
    attachPrimitive: vi.fn(),
    setSeriesOrder: vi.fn(),
  };
}

const chartMock = {
  applyOptions: vi.fn(),
  timeScale: () => ({
    subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(), getVisibleLogicalRange: () => null,
    // 10 px per bar from x = 0.
    coordinateToLogical: (x: number) => x / 10, fitContent: vi.fn(), setVisibleLogicalRange: vi.fn(), scrollToPosition: vi.fn(),
  }),
  subscribeClick: vi.fn(),
  unsubscribeClick: vi.fn(),
  addSeries: vi.fn<(type: unknown) => SeriesMock>(() => {
    const created = seriesMock();
    series.push(created);
    return created;
  }),
  removeSeries: vi.fn(),
  clearCrosshairPosition: vi.fn(),
  priceScale: () => ({ width: () => 64 }),
  panes: () => paneRects.map((rect) => ({
    getHeight: () => rect.height,
    getHTMLElement: () => ({ getBoundingClientRect: () => ({ top: rect.top, height: rect.height }) }),
    priceScale: () => ({ applyOptions: vi.fn() }),
    setStretchFactor: vi.fn(),
    getStretchFactor: () => 1,
  })),
  remove: vi.fn(),
};

vi.mock('lightweight-charts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('lightweight-charts')>()),
  createChart: vi.fn(() => chartMock),
}));

const { TradingChartAdapter } = await import('./chartAdapter');

describe('chart adapter workflow hooks (TVP-2.5)', () => {
  beforeEach(() => {
    series.length = 0;
    paneRects = [{ top: 0, height: 400 }, { top: 400, height: 120 }];
  });

  it('places the countdown under the last-price label and hides it off the pane', () => {
    const adapter = new TradingChartAdapter(document.createElement('div'));
    expect(adapter.lastPriceLabelPosition()).toEqual({ y: 120, color: '#20c997', side: 'right', scaleWidth: 64, paneHeight: 400 });
    series[0].priceToCoordinate.mockReturnValue(450);
    expect(adapter.lastPriceLabelPosition()).toBeNull();
    series[0].lastValueData.mockReturnValue({ noData: true } as never);
    expect(adapter.lastPriceLabelPosition()).toBeNull();
    adapter.setLatestValueLabelVisible(false);
    series[0].lastValueData.mockReturnValue({ noData: false, price: 1, color: '#fff' });
    series[0].priceToCoordinate.mockReturnValue(10);
    expect(adapter.lastPriceLabelPosition()).toBeNull();
  });

  it('draws, keeps and removes the pre/post-market price line', () => {
    const adapter = new TradingChartAdapter(document.createElement('div'));
    const price = series[0];
    adapter.setSessionPriceLine({ price: 187.5, title: 'Post-market', color: '#ff9f43' });
    expect(price.createPriceLine).toHaveBeenCalledTimes(1);
    expect(price.createPriceLine.mock.calls[0][0]).toMatchObject({ price: 187.5, title: 'Post-market', axisLabelVisible: true });
    adapter.setSessionPriceLine({ price: 187.5, title: 'Post-market', color: '#ff9f43' });
    expect(price.createPriceLine).toHaveBeenCalledTimes(1);
    adapter.setSessionPriceLine({ price: 188, title: 'Post-market', color: '#ff9f43' });
    expect(price.removePriceLine).toHaveBeenCalledTimes(1);
    expect(price.createPriceLine).toHaveBeenCalledTimes(2);
    adapter.setSessionPriceLine(null);
    expect(price.removePriceLine).toHaveBeenCalledTimes(2);
  });

  it('keeps indicator bar colours on live updates, for candles and bars (TVP-6.2)', () => {
    const adapter = new TradingChartAdapter(document.createElement('div'));
    const bar = {
      instrument_id: 'fixture', interval: '1m', start_time: '2026-08-05T12:00:00+00:00', end_time: '2026-08-05T12:01:00+00:00',
      open: '10', high: '12', low: '9', close: '11', volume: '5', is_final: false, adjustment_mode: 'raw', session: '24x7',
      provider: 'fixture', ingestion_revision: 1, received_at: '2026-08-05T12:01:00+00:00',
    } as never;
    (adapter as unknown as { barColors: Map<number, string> }).barColors = new Map([[Date.parse('2026-08-05T12:00:00Z') / 1000, '#26a69a']]);
    adapter.updateBar(bar);
    expect(series[0].update.mock.calls.at(-1)?.[0]).toMatchObject({ color: '#26a69a', wickColor: '#26a69a', borderColor: '#26a69a' });
    // The Bars chart type takes the bar-series branch on the same series.
    (adapter as unknown as { chartType: string }).chartType = 'bar';
    series[0].update.mockClear();
    adapter.updateBar({ ...(bar as object), ingestion_revision: 2 } as never);
    expect(series[0].update.mock.calls.at(-1)?.[0]).toMatchObject({ color: '#26a69a' });
  });

  it('draws a candles output as candles in its pane, and a new series when an output changes kind (TVP-6.4)', async () => {
    const { CandlestickSeries, HistogramSeries } = await import('lightweight-charts');
    chartMock.addSeries.mockClear();
    const adapter = new TradingChartAdapter(document.createElement('div'));
    const output = {
      key: 'tv-volume-delta:delta', title: 'Volume Delta (15m)', pane: 1 as const, kind: 'candles' as const,
      points: [{ time: '2026-08-05T12:00:00.000Z', value: 11, open: 0, high: 11, low: -2 }],
    };
    adapter.setIndicatorOutputs([output]);
    // The last candlestick series: the chart's own price series is one too.
    const types = chartMock.addSeries.mock.calls.map(([type]) => type);
    const candleCall = types.lastIndexOf(CandlestickSeries);
    expect(candleCall).toBeGreaterThan(0);
    expect(series[candleCall].setData).toHaveBeenCalledWith([{ time: Date.parse('2026-08-05T12:00:00Z') / 1000, open: 0, high: 11, low: -2, close: 11 }]);
    adapter.setIndicatorOutputs([{ ...output, kind: 'histogram' }]);
    expect(chartMock.removeSeries).toHaveBeenCalledWith(series[candleCall]);
    expect(chartMock.addSeries.mock.calls.at(-1)?.[0]).toBe(HistogramSeries);
  });

  it("draws a script's fill as a band on a hidden line, its plotbar as OHLC bars, and no series for a table (TVP-11.1)", async () => {
    const { BarSeries, LineSeries } = await import('lightweight-charts');
    const { FillBandPrimitive } = await import('./fillBandPrimitive');
    chartMock.addSeries.mockClear();
    const adapter = new TradingChartAdapter(document.createElement('div'));
    const before = chartMock.addSeries.mock.calls.length;
    const t0 = '2026-08-05T12:00:00.000Z';
    const t1 = '2026-08-05T12:01:00.000Z';
    adapter.setIndicatorOutputs([
      { key: 's:f0', title: 'Band', pane: 1, kind: 'fill', color: '#08998133', points: [
        { time: t0, value: 2, high: 3, low: 1 }, { time: t1, value: Number.NaN, high: Number.NaN, low: Number.NaN },
      ] },
      { key: 's:p0', title: 'B', pane: 1, kind: 'candles', barStyle: 'bars', points: [{ time: t0, value: 4, open: 1, high: 5, low: 0, color: '#F23645' }] },
      { key: 's:t1', title: 'table 1', pane: 0, kind: 'table', points: [], table: { position: 'top_right', rows: [] } },
    ]);
    const added = chartMock.addSeries.mock.calls.slice(before).map(([type]) => type);
    expect(added).toEqual([LineSeries, BarSeries]);
    const fill = series[before];
    expect(fill.attachPrimitive.mock.calls[0][0]).toBeInstanceOf(FillBandPrimitive);
    expect(fill.setSeriesOrder).toHaveBeenCalledWith(0);
    // The hidden line runs through the band's middle and skips its gaps.
    expect(fill.setData).toHaveBeenCalledWith([{ time: Date.parse(t0) / 1000, value: 2 }]);
    expect(series[before + 1].setData).toHaveBeenCalledWith([{
      time: Date.parse(t0) / 1000, open: 1, high: 5, low: 0, close: 4, color: '#F23645', wickColor: '#F23645', borderColor: '#F23645',
    }]);
  });

  it('reports a press the pan handling took as a click on the bar under it, for the time link (TVP-4.2)', () => {
    const adapter = new TradingChartAdapter(document.createElement('div'));
    // The chart's time index is built from its series' times.
    (adapter as unknown as { seriesTimes: Map<string, number[]> }).seriesTimes.set('price', [0, 1, 2].map((index) => Date.UTC(2026, 0, 1, index) / 1000));
    const clicks: number[] = [];
    const stop = adapter.onBarClick((timeMs) => clicks.push(timeMs));
    adapter.clickAt(19); // bar 2 (logical 1.9)
    adapter.clickAt(80); // past the last bar: nothing
    expect(clicks).toEqual([Date.UTC(2026, 0, 1, 2)]);
    stop();
    adapter.clickAt(0);
    expect(clicks).toHaveLength(1);
    expect(chartMock.unsubscribeClick).toHaveBeenCalled();
  });

  it('tells which pane a double-click landed on', () => {
    const adapter = new TradingChartAdapter(document.createElement('div'));
    (adapter as unknown as { indicatorPaneIds: string[] }).indicatorPaneIds = ['rsi'];
    expect(adapter.paneAtClientY(100)).toEqual({ kind: 'main' });
    expect(adapter.paneAtClientY(450)).toEqual({ kind: 'indicator', id: 'rsi' });
    expect(adapter.paneAtClientY(600)).toBeNull();
  });
});
