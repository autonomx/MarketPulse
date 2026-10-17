import { describe, expect, it } from 'vitest';
import { alignIndicatorPoints, alignSeriesToTimes, backgroundLabelMarkers, candlestickData, constrainZoomOutRange, drawingLogicalIndexForTime, drawingTimeForLogicalIndex, DrawingTimeIndex, heikinAshiBars, indicatorBarColors, indicatorLineData, indicatorMarkers, indicatorOutputOnBars, lineData, normalizeChartBars, renkoBars, TradingChartAdapter, opaqueColor, TRADING_CHART_TYPE_OPTIONS, upsertChartBar, visibleAverageClose, volumeData } from './chartAdapter';
import type { IndicatorOutput } from '../indicators/coreIndicators';
import type { UTCTimestamp } from 'lightweight-charts';
import type { MarketBar } from '../tradingTypes';
import { fixture } from '../../../test/fixture';

const bar: MarketBar = fixture({
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  interval: '1m',
  start_time: '2026-08-05T12:00:00+00:00',
  end_time: '2026-08-05T12:01:00+00:00',
  open: '100.25',
  high: '103.00',
  low: '99.50',
  close: '102.75',
  volume: '45.5',
  is_final: true,
  adjustment_mode: 'raw',
  session: '24x7',
  provider: 'binance',
  ingestion_revision: 1,
  received_at: '2026-08-05T12:01:00+00:00',
});
const secondBar: MarketBar = {
  ...bar,
  start_time: '2026-08-05T12:01:00+00:00',
  end_time: '2026-08-05T12:02:00+00:00',
  open: '12',
  high: '16',
  low: '10',
  close: '14',
};

describe('Trading chart adapter normalization', () => {
  it('merges equivalent ISO timestamps without duplicating indicator or replay bars', () => {
    const update = { ...bar, start_time: '2026-08-05T12:00:00.000Z', close: '104', ingestion_revision: 2 };
    const source = [bar, secondBar];
    const result = upsertChartBar(source, update);
    expect(result).toEqual([update, secondBar]);
    expect(source).toEqual([bar, secondBar]);
    expect(upsertChartBar(result, bar)).toEqual(result);
  });

  it('inserts an out-of-order stream bar in time order', () => {
    expect(upsertChartBar([secondBar], bar)).toEqual([bar, secondBar]);
    expect(upsertChartBar([], bar)).toEqual([bar]);
  });

  it('exposes the complete TradingView-style chart type catalog', () => {
    expect(TRADING_CHART_TYPE_OPTIONS.map((option) => option.label)).toEqual([
      'Bars', 'Candles', 'Hollow candles', 'Volume candles', 'Line', 'Line with markers', 'Step line',
      'Area', 'HLC area', 'Baseline', 'Columns', 'High-low', 'Volume footprint', 'Time price opportunity',
      'Session volume profile', 'Heikin Ashi', 'Renko', 'Line break', 'Kagi', 'Point & figure', 'Range',
    ]);
  });

  it('converts backend decimal strings at the chart boundary', () => {
    expect(candlestickData(bar)).toMatchObject({
      open: 100.25,
      high: 103,
      low: 99.5,
      close: 102.75,
    });
    expect(lineData(bar).value).toBe(102.75);
    expect(volumeData(bar).value).toBe(45.5);
  });

  it('applies a selected display-currency multiplier to price data', () => {
    const converted = candlestickData(bar, 1.4);
    expect(converted.open).toBeCloseTo(140.35);
    expect(converted.high).toBeCloseTo(144.2);
    expect(converted.low).toBeCloseTo(139.3);
    expect(converted.close).toBeCloseTo(143.85);
    expect(lineData(bar, 1.4).value).toBeCloseTo(143.85);
  });

  it('builds standard Heikin Ashi candles from the source bars', () => {
    const [first, second] = heikinAshiBars([{ ...bar, open: '10', high: '14', low: '8', close: '12' }, secondBar]);
    expect(first).toMatchObject({ open: '11', high: '14', low: '8', close: '11' });
    expect(second).toMatchObject({ open: '11', high: '16', low: '10', close: '13' });
  });

  it('creates ordered synthetic Renko bars without duplicate chart timestamps', () => {
    const derived = renkoBars([
      { ...bar, close: '100', high: '101', low: '99' },
      { ...secondBar, close: '106', high: '107', low: '99' },
    ]);
    expect(derived.length).toBeGreaterThan(0);
    expect(new Set(derived.map((item) => item.start_time)).size).toBe(derived.length);
    expect(derived.every((item) => Number(item.high) >= Number(item.open) && Number(item.high) >= Number(item.close))).toBe(true);
    expect(derived.every((item) => Number(item.low) <= Number(item.open) && Number(item.low) <= Number(item.close))).toBe(true);
  });

  it('uses epoch seconds and deterministic volume direction colors', () => {
    expect(candlestickData(bar).time).toBe(Date.parse(bar.start_time) / 1_000);
    expect(volumeData(bar).color).toContain('32,201,151');
    expect(volumeData({ ...bar, close: '99' }).color).toContain('255,107,107');
  });

  it('normalizes chart bars to strictly increasing epoch seconds', () => {
    const corrected = {
      ...secondBar,
      start_time: '2026-08-05T12:01:00.500+00:00',
      close: '15',
      ingestion_revision: 2,
    };
    const normalized = normalizeChartBars([corrected, { ...bar, start_time: 'not-a-date' }, bar, secondBar]);

    expect(normalized).toHaveLength(2);
    expect(normalized[0]).toBe(bar);
    expect(normalized[1]).toBe(corrected);
    expect(Math.floor(Date.parse(normalized[1].start_time) / 1_000)).toBe(
      Math.floor(Date.parse(secondBar.start_time) / 1_000),
    );
  });

  it('rejects invalid provider timestamps', () => {
    expect(() => candlestickData({ ...bar, start_time: 'not-a-date' })).toThrow(/Invalid Trading timestamp/);
  });

  it('caps zoom width without pulling a panned chart back over the data', () => {
    expect(constrainZoomOutRange({ from: 130, to: 330 }, { from: 0, to: 100 }, 230)).toEqual({ from: 180, to: 280 });
    expect(constrainZoomOutRange({ from: -330, to: -130 }, { from: 0, to: 100 }, -230)).toEqual({ from: -280, to: -180 });
  });

  it('extrapolates drawing timestamps beyond the loaded bar range', () => {
    const bars = [bar, secondBar];
    expect(drawingTimeForLogicalIndex(2, bars)).toBe('2026-08-05T12:02:00.000Z');
    expect(drawingLogicalIndexForTime('2026-08-05T12:03:00.000Z', bars)).toBe(3);
  });

  it('indexes bar times once for repeated drawing projections', () => {
    const thirdBar = { ...bar, start_time: '2026-08-05T12:02:00+00:00' };
    const index = new DrawingTimeIndex([bar, secondBar, thirdBar]);
    expect(index.logicalIndexForTime('2026-08-05T12:01:00.000Z')).toBe(1);
    expect(index.logicalIndexForTime('2026-08-05T12:02:00.000Z')).toBe(2);
    // Between bars a time interpolates; past the last bar it extrapolates at the bar interval.
    expect(index.logicalIndexForTime('2026-08-05T12:00:30.000Z')).toBe(0.5);
    expect(index.logicalIndexForTime('2026-08-05T12:05:00.000Z')).toBe(5);
    expect(index.logicalIndexForTime('2026-08-05T11:58:00.000Z')).toBe(-2);
    expect(index.logicalIndexForTime('not-a-date')).toBeNull();
    expect(index.timeForLogicalIndex(1)).toBe('2026-08-05T12:01:00.000Z');
    expect(index.timeForLogicalIndex(1.2)).toBe('2026-08-05T12:01:12.000Z');
    expect(index.timeForLogicalIndex(6)).toBe('2026-08-05T12:06:00.000Z');
    expect(index.timeAfterBars('2026-08-05T12:01:00.000Z', 3)).toBe('2026-08-05T12:04:00.000Z');
    expect(new DrawingTimeIndex([]).logicalIndexForTime('2026-08-05T12:00:00.000Z')).toBeNull();
  });
});

describe('Trading chart adapter normalization', () => {
  describe('drawing times on gapped data', () => {
    // Two 5m sessions, 13:30-19:55 UTC, with an overnight gap between them.
    const sessions = ['2026-10-05', '2026-10-06'].flatMap((day) => Array.from({ length: 78 }, (_, minute) => ({
      ...bar,
      interval: '5m',
      start_time: new Date(Date.parse(`${day}T13:30:00.000Z`) + minute * 300_000).toISOString(),
    })));
    const index = new DrawingTimeIndex(sessions);

    it('maps a time inside a later session to its own bars, not by the first bar and the interval', () => {
      expect(index.logicalIndexForTime('2026-10-06T13:32:00.000Z')).toBeCloseTo(78.4, 9);
      expect(index.logicalIndexForTime('2026-10-06T19:55:00.000Z')).toBe(155);
    });

    it('extrapolates past the last bar from that bar', () => {
      expect(index.logicalIndexForTime('2026-10-06T20:05:00.000Z')).toBe(157);
      expect(index.timeForLogicalIndex(157)).toBe('2026-10-06T20:05:00.000Z');
      expect(index.timeForLogicalIndex(-1)).toBe('2026-10-05T13:25:00.000Z');
    });

    it('is the exact inverse of timeForLogicalIndex', () => {
      for (let logical = -20; logical <= 180; logical += 0.37) {
        const time = index.timeForLogicalIndex(logical)!;
        expect(index.logicalIndexForTime(time)).toBeCloseTo(logical, 5);
        expect(index.timeForLogicalIndex(index.logicalIndexForTime(time)!)).toBe(time);
      }
    });

    it('places drawings made on another interval between the bars they fall in', () => {
      // A 1m anchor on a 5m chart.
      expect(index.logicalIndexForTime('2026-10-05T13:41:00.000Z')).toBeCloseTo(2.2, 9);
      // A 1h anchor on a 1D chart, over a weekend.
      const daily = ['2026-10-02', '2026-10-05', '2026-10-06'].map((day) => ({ ...bar, interval: '1d', start_time: `${day}T00:00:00.000Z` }));
      const days = new DrawingTimeIndex(daily);
      expect(days.logicalIndexForTime('2026-10-05T15:00:00.000Z')).toBeCloseTo(1 + 15 / 24, 9);
      // Past the data it steps by the interval's duration (a day), not an estimate from the gaps.
      expect(days.logicalIndexForTime('2026-10-07T12:00:00.000Z')).toBeCloseTo(3.5, 9);
      expect(days.timeForLogicalIndex(3)).toBe('2026-10-07T00:00:00.000Z');
      expect(days.timeForLogicalIndex(-1)).toBe('2026-10-01T00:00:00.000Z');
      expect(new DrawingTimeIndex([daily[0]]).timeForLogicalIndex(1)).toBe('2026-10-03T00:00:00.000Z');
    });
  });
});

describe('Trading chart adapter normalization', () => {
  describe("drawing anchors on the chart's own time scale", () => {
    // Lightweight Charts indexes the union of every series' times. This fake time
    // scale does the same; like the real one, it puts fractional logical indices at 0.
    function chartWith(seriesTimes: number[][], bars: MarketBar[]) {
      const union = [...new Set(seriesTimes.flat())].sort((left, right) => left - right);
      const series = seriesTimes.map((times) => ({
        data: () => times.map((time) => ({ time })),
        priceToCoordinate: (price: number) => 500 - price,
        coordinateToPrice: (y: number) => 500 - y,
      }));
      const timeScale = {
        timeToCoordinate: (time: number) => (union.includes(time) ? union.indexOf(time) * 10 : null),
        logicalToCoordinate: (logical: number) => (Number.isInteger(logical) ? logical * 10 : 0),
        coordinateToLogical: (x: number) => x / 10,
        coordinateToTime: () => null,
        getVisibleLogicalRange: () => ({ from: 0, to: union.length - 1 }),
      };
      const adapter = Object.create(TradingChartAdapter.prototype) as TradingChartAdapter;
      Object.assign(adapter, {
        chart: { timeScale: () => timeScale },
        priceSeries: series[0],
        volumeSeries: series[1] ?? series[0],
        indicatorSeries: new Map(),
        comparisonSeries: new Map(series.slice(2).map((item, index) => [`comparison-${index}`, item])),
        bars,
        priceTimes: seriesTimes[0] ?? [],
        priceScaleMultiplier: 1,
        destroyed: false,
        chartTimeIndex: null,
        seriesTimes: new Map(seriesTimes.map((times, index) => [`series-${index}`, times])),
        rawBarIndex: null,
        barSeries: null,
        barSeriesCache: [],
      });
      return { adapter, union };
    }
    const seconds = (time: string) => Date.parse(time) / 1_000;

    it('counts every time any series plots, as the chart does', () => {
      // 1h equity bars at :30 for two sessions and a series plotting every hour on the hour. Comparisons no longer add
      // times (alignSeriesToTimes), but the index must still follow whatever times the chart plots.
      const equity = ['2026-10-05', '2026-10-06'].flatMap((day) => Array.from({ length: 7 }, (_, hour) => ({
        ...bar, interval: '1h', start_time: new Date(Date.parse(`${day}T13:30:00.000Z`) + hour * 3_600_000).toISOString(),
      })));
      const crypto = Array.from({ length: 48 }, (_, hour) => seconds('2026-10-05T00:00:00.000Z') + hour * 3_600);
      const equityTimes = equity.map((item) => seconds(item.start_time));
      const { adapter, union } = chartWith([equityTimes, equityTimes, crypto], equity);
      const before = union.indexOf(seconds('2026-10-06T14:30:00.000Z'));
      // 14:45 lies halfway between the equity bar at 14:30 and the comparison point at 15:00.
      expect(adapter.drawingBarIndexForTime('2026-10-06T14:45:00.000Z')).toBeCloseTo(before + 0.5, 9);
      expect(adapter.projectDrawingPoint({ time: '2026-10-06T14:45:00.000Z', price: 100 })).toEqual({ x: (before + 0.5) * 10, y: 400 });
      // From a coordinate back to a time snaps to the chart's own point there.
      expect(adapter.drawingPointFromCoordinate((before + 1) * 10, 400)?.time).toBe('2026-10-06T15:00:00.000Z');
      expect(adapter.drawingPointFromCoordinate((before + 0.25) * 10, 400, { exactTime: true })?.time).toBe('2026-10-06T14:37:30.000Z');
      // The whole chart is in view: every loaded bar is visible.
      expect(adapter.drawingVisibleBars()).toEqual({ from: 0, to: equity.length - 1 });
    });

    it('maps anchors on a Renko chart by the bricks it plots', () => {
      const raw = Array.from({ length: 6 }, (_, minute) => ({ ...bar, interval: '1m', start_time: new Date(Date.parse('2026-10-05T13:30:00.000Z') + minute * 60_000).toISOString() }));
      // Two bricks from the same source bar (one second apart), none for the next three bars.
      const bricks = [seconds(raw[0].start_time), seconds(raw[1].start_time), seconds(raw[1].start_time) + 1, seconds(raw[5].start_time)];
      const { adapter, union } = chartWith([bricks, raw.map((item) => seconds(item.start_time))], raw);
      expect(union).toHaveLength(7);
      expect(adapter.drawingBarIndexForTime(raw[5].start_time)).toBe(6);
      expect(adapter.drawingBarIndexForTime(new Date(Date.parse(raw[1].start_time) + 500).toISOString())).toBeCloseTo(1.5, 9);
      // Past the data, anchors step by the interval (1m) from the last plotted point.
      expect(adapter.drawingBarIndexForTime('2026-10-05T13:37:00.000Z')).toBe(8);
    });

    it('rebuilds the chart time index when a time in the middle of a series changes', () => {
      const raw = Array.from({ length: 3 }, (_, minute) => ({ ...bar, interval: '1m', start_time: new Date(Date.parse('2026-10-05T13:30:00.000Z') + minute * 60_000).toISOString() }));
      const times = raw.map((item) => seconds(item.start_time));
      const { adapter } = chartWith([times], raw);
      const note = (next: number[]) => (adapter as unknown as { noteSeriesTimes: (key: string, data: { time: number }[]) => void })
        .noteSeriesTimes('series-0', next.map((time) => ({ time })));
      expect(adapter.drawingBarIndexForTime('2026-10-05T13:31:00.000Z')).toBe(1);
      note(times);
      expect(adapter.drawingBarIndexForTime('2026-10-05T13:31:00.000Z')).toBe(1);
      // Same length, first and last time; the middle point moves 30 s later.
      note([times[0], times[1] + 30, times[2]]);
      expect(adapter.drawingBarIndexForTime('2026-10-05T13:31:00.000Z')).toBeCloseTo(2 / 3, 9);
    });

    it('checks that the chart index is the bars index before offering sloped alerts', () => {
      const raw = Array.from({ length: 3 }, (_, minute) => ({ ...bar, interval: '1m', start_time: new Date(Date.parse('2026-10-05T13:30:00.000Z') + minute * 60_000).toISOString() }));
      const times = raw.map((item) => seconds(item.start_time));
      expect(chartWith([times, times], raw).adapter.drawingBarIndexMatchesBars()).toBe(true);
      // A series plotting between the bars (data on its own clock) breaks the match.
      expect(chartWith([times, [times[0] + 30]], raw).adapter.drawingBarIndexMatchesBars()).toBe(false);
      // Points after the last bar don't.
      expect(chartWith([times, [times[2] + 60, times[2] + 120]], raw).adapter.drawingBarIndexMatchesBars()).toBe(true);
    });

    it('has no visible bars when the view is past the loaded bars', () => {
      const raw = Array.from({ length: 3 }, (_, minute) => ({ ...bar, interval: '1m', start_time: new Date(Date.parse('2026-10-05T13:30:00.000Z') + minute * 60_000).toISOString() }));
      const { adapter } = chartWith([raw.map((item) => seconds(item.start_time))], raw);
      const timeScale = (adapter as unknown as { chart: { timeScale: () => { getVisibleLogicalRange: () => unknown } } }).chart.timeScale();
      timeScale.getVisibleLogicalRange = () => ({ from: 5.2, to: 12 });
      expect(adapter.drawingVisibleBars()).toBeNull();
      timeScale.getVisibleLogicalRange = () => ({ from: 1.4, to: 12 });
      expect(adapter.drawingVisibleBars()).toEqual({ from: 1, to: 2 });
    });

    it('rebuilds the drawing time index only when times change, not on in-place ticks', () => {
      const raw = Array.from({ length: 3 }, (_, minute) => ({ ...bar, interval: '1m', start_time: new Date(Date.parse('2026-10-05T13:30:00.000Z') + minute * 60_000).toISOString() }));
      const { adapter } = chartWith([raw.map((item) => seconds(item.start_time))], raw);
      Object.assign(adapter, { revisions: new Map(), updatePriceData: () => undefined, volumeSeries: { update: () => undefined, data: () => [] } });
      const first = adapter.drawingBars();
      expect(first.indexAtOrBefore('2026-10-05T13:20:00.000Z')).toBe(-1);
      expect(first.at(2)?.close).toBe(102.75);
      adapter.updateBar({ ...raw[2], close: '110', ingestion_revision: 2 });
      expect(adapter.drawingBars()).toBe(first);
      expect(first.at(2)?.close).toBe(110);
      adapter.updateBar({ ...raw[2], start_time: '2026-10-05T13:33:00.000Z', ingestion_revision: 1 });
      expect(adapter.drawingBars()).not.toBe(first);
      expect(adapter.drawingBars().length).toBe(4);
    });
  });

  describe('indicator rendering', () => {
    const times = [0, 60, 120, 180, 240].map((seconds) => (Date.UTC(2026, 7, 5, 12) / 1_000 + seconds) as UTCTimestamp);
    const iso = (time: number) => new Date(time * 1_000).toISOString();
    const levels: IndicatorOutput = {
      key: 'tv-rob-booker-missed-pivot-points:missed-above', title: 'Missed Pivot Above', pane: 0, kind: 'line', render: 'levels',
      points: [{ time: iso(times[0]), value: 10 }, { time: iso(times[1]), value: 10 }, { time: iso(times[3]), value: 12 }],
    };

    it('leaves gaps between separate levels instead of joining them', () => {
      expect(indicatorLineData(levels, times, 2)).toEqual([
        { time: times[0], value: 20 }, { time: times[1], value: 20 }, { time: times[2] }, { time: times[3], value: 24 },
      ]);
      // Joined lines and pane outputs keep their points and price scale.
      expect(indicatorLineData({ ...levels, render: 'line', pane: 1 }, times, 2)).toEqual([
        { time: times[0], value: 10 }, { time: times[1], value: 10 }, { time: times[3], value: 12 },
      ]);
    });

    it('draws point signals as markers at their price', () => {
      const signals: IndicatorOutput = { ...levels, key: 'tv-rob-booker-reversal:bullish', render: 'markers', marker: 'arrowUp', color: '#20c997' };
      expect(indicatorMarkers(signals, 2)[2]).toEqual({ time: times[3], position: 'atPriceMiddle', price: 24, shape: 'arrowUp', color: '#20c997', size: 1 });
    });
  });
});

describe('comparison series on the main series times (TVP-0.4 decision)', () => {
  const t = (value: number) => value as UTCTimestamp;

  it('drops times only the comparison has and carries its value over times it lacks', () => {
    const crypto = [1, 2, 3, 4, 5, 6].map((time) => ({ time: t(time * 100), value: time }));
    expect(alignSeriesToTimes(crypto, [t(200), t(500)])).toEqual([{ time: 200, value: 2 }, { time: 500, value: 5 }]);
    const equity = [{ time: t(100), value: 1 }, { time: t(400), value: 4 }];
    expect(alignSeriesToTimes(equity, [t(100), t(200), t(300), t(400)])).toEqual([
      { time: 100, value: 1 }, { time: 200, value: 1 }, { time: 300, value: 1 }, { time: 400, value: 4 },
    ]);
  });

  it('plots nothing before the comparison starts or after it ends', () => {
    const points = [{ time: t(200), value: 2 }, { time: t(300), value: 3 }];
    expect(alignSeriesToTimes(points, [t(100), t(200), t(250), t(300), t(400)])).toEqual([
      { time: 200, value: 2 }, { time: 250, value: 2 }, { time: 300, value: 3 },
    ]);
    expect(alignSeriesToTimes([], [t(100)])).toEqual([]);
  });

  it('maps onto brick times on Renko-type charts', () => {
    const points = [{ time: t(100), value: 10 }, { time: t(160), value: 16 }, { time: t(220), value: 22 }];
    expect(alignSeriesToTimes(points, [t(100), t(190), t(220)])).toEqual([{ time: 100, value: 10 }, { time: 190, value: 16 }, { time: 220, value: 22 }]);
  });
});

describe('indicator points on the chart bars', () => {
  const t = (value: number) => value as UTCTimestamp;
  const day = 86_400;
  const bars = [t(0), t(day), t(2 * day)];

  it('shows the latest value during each bar for data on its own clock', () => {
    // Funding every 8 hours, through the forming last bar.
    const funding = Array.from({ length: 8 }, (_, index) => ({ time: t(index * 28_800), value: index }));
    expect(alignIndicatorPoints(funding, bars, day)).toEqual([{ time: 0, value: 2 }, { time: day, value: 5 }, { time: 2 * day, value: 7 }]);
    // Data that starts late and stops early covers only its own bars.
    expect(alignIndicatorPoints([{ time: t(day + 3_600), value: 9 }], bars, day)).toEqual([{ time: day, value: 9 }]);
  });

  it('keeps bar-time outputs and points beyond the last bar', () => {
    const onBars = bars.map((time, index) => ({ time, value: index }));
    expect(alignIndicatorPoints(onBars, bars, day)).toEqual(onBars);
    const ahead = [{ time: t(day), value: 1 }, { time: t(3 * day), value: 3 }];
    expect(alignIndicatorPoints(ahead, bars, day)).toEqual(ahead);
    expect(alignIndicatorPoints([{ time: t(3_600), value: 1 }, { time: t(4 * day), value: 4 }], bars, day))
      .toEqual([{ time: 0, value: 1 }, { time: 4 * day, value: 4 }]);
  });
});

describe('indicators that draw (TVP-6.2)', () => {
  const colors = (key: string, points: Array<{ time: string; color?: string }>, visible = true): IndicatorOutput => ({
    key, title: key, pane: 0, kind: 'bar-colors', visible, points: points.map((point) => ({ ...point, value: 1 })),
  });

  it('collects bar colours by bar time, a later output winning, hidden outputs and other kinds ignored', () => {
    const line: IndicatorOutput = { key: 'line', title: 'line', pane: 0, kind: 'line', points: [{ time: bar.start_time, value: 1, color: '#000000' }] };
    const result = indicatorBarColors([
      colors('a', [{ time: bar.start_time, color: '#111111' }, { time: secondBar.start_time, color: '#222222' }]),
      colors('b', [{ time: secondBar.start_time, color: '#333333' }]),
      colors('hidden', [{ time: bar.start_time, color: '#444444' }], false),
      line,
    ]);
    expect([...result]).toEqual([[Date.parse(bar.start_time) / 1000, '#111111'], [Date.parse(secondBar.start_time) / 1000, '#333333']]);
  });

  it('averages the closes of the bars in the visible logical range', () => {
    const bars = [bar, secondBar, { ...secondBar, close: '20' }];
    expect(visibleAverageClose(bars, { from: 0, to: 2 })).toBeCloseTo((102.75 + 14 + 20) / 3);
    expect(visibleAverageClose(bars, { from: 0.5, to: 1.5 })).toBe(14);
    expect(visibleAverageClose(bars, { from: -10, to: 0 })).toBe(102.75);
    expect(visibleAverageClose(bars, { from: 5, to: 9 })).toBeNull();
    expect(visibleAverageClose(bars, null)).toBeNull();
    expect(visibleAverageClose([], { from: 0, to: 1 })).toBeNull();
  });

  it('labels background outputs at their labelled bars and keeps them on bar times', () => {
    const background: IndicatorOutput = {
      key: 'sessions', title: 'Trading Sessions', pane: 0, kind: 'background', color: '#123456',
      points: [{ time: bar.start_time, value: 1, color: 'rgba(1, 2, 3, 0.1)', label: 'Tokyo' }, { time: secondBar.start_time, value: 1 }],
    };
    expect(backgroundLabelMarkers(background)).toEqual([
      { time: Date.parse(bar.start_time) / 1000, position: 'aboveBar', shape: 'square', size: 0, color: 'rgb(1, 2, 3)', text: 'Tokyo' },
    ]);
    expect(indicatorOutputOnBars(background, [], 60)).toBe(background);
    expect(opaqueColor('rgba(255, 152, 0, 0.10)')).toBe('rgb(255, 152, 0)');
    expect(opaqueColor('#ff9800')).toBe('#ff9800');
    expect(indicatorLineData(background, [], 100).map((point) => ('value' in point ? point.value : null))).toEqual([1, 1]);
  });
});

describe('pattern markers (TVP-6.3)', () => {
  it('places markers above or below the bar with their text, else at the price of the point', () => {
    const pattern: IndicatorOutput = {
      key: 'tv-all-candlestick-patterns:hammer', title: 'Hammer - Bullish', pane: 0, kind: 'line', render: 'markers', marker: 'arrowUp',
      markerPosition: 'belowBar', markerText: 'H', color: '#089981', points: [{ time: bar.start_time, value: 99.5 }],
    };
    expect(indicatorMarkers(pattern, 100)).toEqual([{ time: Date.parse(bar.start_time) / 1000, position: 'belowBar', shape: 'arrowUp', color: '#089981', size: 1, text: 'H' }]);
    const atPrice: IndicatorOutput = { ...pattern, markerPosition: undefined, markerText: undefined };
    expect(indicatorMarkers(atPrice, 100)[0]).toMatchObject({ position: 'atPriceMiddle', price: 9950 });
  });
});
