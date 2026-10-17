import { describe, expect, it, vi } from 'vitest';
import {
  copyChartSnapshotLink,
  barCloseTime,
  barCountdownEnabled,
  barCountdownRemainingMs,
  barIndexAtTime,
  centeredLogicalRange,
  chartTemplatePayload,
  copyImageDataUrlToClipboard,
  dataDelayLabel,
  dataUrlToBlob,
  extendedSessionPriceLine,
  filterExtendedHours,
  formatBarCountdown,
  hasExtendedHoursBars,
  hasSessionTaggedBars,
  marketStatusLabel,
  paneDoubleClickAction,
  parseChartTemplate,
  parseGoToDate,
  planGoToDate,
} from './tradingChartWorkflow';
import type { CoreIndicatorInstance } from './indicators/coreIndicators';
import type { MarketBar } from './tradingTypes';

function bar(start: string, end: string, overrides: Partial<MarketBar> = {}): MarketBar {
  return {
    instrument_id: 'equity:NASDAQ:AAPL',
    interval: '1h',
    start_time: start,
    end_time: end,
    open: '100',
    high: '101',
    low: '99',
    close: '100.5',
    volume: '10',
    is_final: false,
    adjustment_mode: 'raw',
    session: 'regular',
    provider: 'ibkr',
    provider_event_id: start,
    provider_sequence: null,
    ingestion_revision: 1,
    received_at: start,
    ...overrides,
  } as MarketBar;
}

describe('bar countdown', () => {
  const latest = bar('2026-10-08T14:00:00Z', '2026-10-08T15:00:00Z');

  it('counts down to the bar end and stops at the boundary', () => {
    expect(barCountdownRemainingMs(latest, '1h', Date.parse('2026-10-08T14:59:30Z'))).toBe(30_000);
    expect(barCountdownRemainingMs(latest, '1h', Date.parse('2026-10-08T14:00:00Z'))).toBe(3_600_000);
    expect(barCountdownRemainingMs(latest, '1h', Date.parse('2026-10-08T15:00:00Z'))).toBeNull();
    expect(barCountdownRemainingMs(latest, '1h', Date.parse('2026-10-08T15:00:01Z'))).toBeNull();
    expect(barCountdownRemainingMs(undefined, '1h', 0)).toBeNull();
  });

  it('falls back to start plus interval when a bar has no usable end', () => {
    const custom = bar('2026-10-08T14:00:00Z', 'not-a-time', { interval: '7m' });
    expect(barCloseTime(custom, '7m')).toBe(Date.parse('2026-10-08T14:07:00Z'));
    expect(barCloseTime(bar('2026-10-08T14:00:00Z', '2026-10-08T14:00:00Z'), '7m')).toBe(Date.parse('2026-10-08T14:07:00Z'));
  });

  it('formats remaining time, rounding up so it never reads 00:00 early', () => {
    expect(formatBarCountdown(59_001)).toBe('01:00');
    expect(formatBarCountdown(500)).toBe('00:01');
    expect(formatBarCountdown(61_000)).toBe('01:01');
    expect(formatBarCountdown(3_600_000)).toBe('01:00:00');
    expect(formatBarCountdown(3 * 3_600_000 + 5 * 60_000 + 9_000)).toBe('03:05:09');
    expect(formatBarCountdown(2 * 86_400_000 + 4 * 3_600_000 + 30 * 60_000)).toBe('2d 04:30');
  });

  it('is independent of the chart timezone: an equity daily bar closes at 16:00 New York across DST', () => {
    // 16:00 EDT is 20:00 UTC; 16:00 EST is 21:00 UTC. The bar carries its own end time.
    const summer = bar('2026-10-08T13:30:00Z', '2026-10-08T20:00:00Z', { interval: '1d' });
    const winter = bar('2026-11-09T14:30:00Z', '2026-11-09T21:00:00Z', { interval: '1d' });
    expect(formatBarCountdown(barCountdownRemainingMs(summer, '1d', Date.parse('2026-10-08T19:00:00Z')) ?? 0)).toBe('01:00:00');
    expect(formatBarCountdown(barCountdownRemainingMs(winter, '1d', Date.parse('2026-11-09T20:00:00Z')) ?? 0)).toBe('01:00:00');
  });

  it('defaults on for intraday intervals and never shows for ticks or ranges', () => {
    expect(barCountdownEnabled(undefined, '5m')).toBe(true);
    expect(barCountdownEnabled(undefined, '1d')).toBe(false);
    expect(barCountdownEnabled({ barCountdown: true }, '1d')).toBe(true);
    expect(barCountdownEnabled({ barCountdown: false }, '5m')).toBe(false);
    expect(barCountdownEnabled({ barCountdown: true }, '100t')).toBe(false);
  });
});

describe('extended hours', () => {
  const bars = [
    bar('2026-10-08T12:00:00Z', '2026-10-08T12:01:00Z', { session: 'extended_pre' }),
    bar('2026-10-08T13:30:00Z', '2026-10-08T13:31:00Z', { session: 'regular' }),
    bar('2026-10-08T20:00:00Z', '2026-10-08T20:01:00Z', { session: 'extended_post', close: '99.25' }),
  ];

  it('hides pre- and post-market bars only when asked', () => {
    expect(filterExtendedHours(bars, true)).toHaveLength(3);
    expect(filterExtendedHours(bars, false).map((item) => item.session)).toEqual(['regular']);
    expect(hasExtendedHoursBars(bars)).toBe(true);
    expect(hasExtendedHoursBars([bar('2026-10-08T00:00:00Z', '2026-10-08T01:00:00Z', { session: '24x7' })])).toBe(false);
    expect(hasSessionTaggedBars(bars.slice(1, 2))).toBe(true);
    expect(hasSessionTaggedBars([bar('2026-10-08T00:00:00Z', '2026-10-08T01:00:00Z', { session: '24x7' })])).toBe(false);
  });

  it('draws a price line at the latest pre/post-market price', () => {
    expect(extendedSessionPriceLine(bars)).toEqual({ price: 99.25, session: 'post', time: '2026-10-08T20:00:00Z' });
    expect(extendedSessionPriceLine(bars.slice(0, 1))?.session).toBe('pre');
    expect(extendedSessionPriceLine(bars.slice(0, 2))).toBeNull();
    expect(extendedSessionPriceLine([])).toBeNull();
  });
});

describe('go to date', () => {
  const hourly = Array.from({ length: 100 }, (_, index) => {
    const start = Date.parse('2026-10-01T00:00:00Z') + index * 3_600_000;
    return bar(new Date(start).toISOString(), new Date(start + 3_600_000).toISOString());
  });

  const at = (iso: string) => ({ kind: 'instant' as const, time: Date.parse(iso) });
  const day = (value: string, timeZone: string) => {
    const parsed = parseGoToDate(value, timeZone);
    if (parsed?.kind !== 'day') throw new Error('expected a day');
    return parsed;
  };
  const daily = (firstIso: string, count: number, stepDays = 1) => Array.from({ length: count }, (_, index) => {
    const start = Date.parse(firstIso) + index * stepDays * 86_400_000;
    return bar(new Date(start).toISOString(), new Date(start + 3_600_000).toISOString());
  });

  it('parses a date as a local day and a date-time as an instant, in the chart timezone', () => {
    expect(parseGoToDate('2026-10-03', 'UTC')).toEqual({
      kind: 'day', dayStart: Date.parse('2026-10-03T00:00:00Z'), dayEnd: Date.parse('2026-10-03T23:59:59.999Z'),
    });
    expect(parseGoToDate('2026-10-03', 'Asia/Tokyo')).toMatchObject({ kind: 'day', dayStart: Date.parse('2026-10-02T15:00:00Z') });
    expect(parseGoToDate('2026-10-03T09:30', 'America/New_York')).toEqual(at('2026-10-03T13:30:00Z'));
    expect(parseGoToDate('03/10/2026', 'UTC')).toBeNull();
  });

  it('finds the bar that holds a time', () => {
    expect(barIndexAtTime(hourly, Date.parse('2026-10-01T05:30:00Z'))).toBe(5);
    expect(barIndexAtTime(hourly, Date.parse('2026-10-01T00:00:00Z'))).toBe(0);
    expect(barIndexAtTime(hourly, Date.parse('2026-09-30T23:00:00Z'))).toBe(-1);
    expect(barIndexAtTime(hourly, Date.parse('2027-01-01T00:00:00Z'))).toBe(99);
  });

  it('goes to the first bar of an equity day, not the bar before it', () => {
    // Weekday daily bars open at 09:30 New York (13:30 UTC); 2026-10-02 is a Friday.
    const equityDaily = ['2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06']
      .map((date) => bar(`${date}T13:30:00Z`, `${date}T20:00:00Z`, { interval: '1d' }));
    expect(planGoToDate(equityDaily, day('2026-10-05', 'America/New_York'), 1_000)).toEqual({ kind: 'scroll', index: 2 });
    // A weekend day goes to the next session.
    expect(planGoToDate(equityDaily, day('2026-10-04', 'America/New_York'), 1_000)).toEqual({ kind: 'scroll', index: 2 });
    // A date and time keeps the bar at or before it.
    expect(planGoToDate(equityDaily, at('2026-10-05T12:00:00Z'), 1_000)).toEqual({ kind: 'scroll', index: 1 });
  });

  it('goes to the first intraday bar of the local day', () => {
    // 5-minute bars from 2026-10-05 09:30 to 2026-10-06 16:00 New York, regular hours only.
    const sessions = ['2026-10-05', '2026-10-06'].flatMap((date) => Array.from({ length: 78 }, (_, index) => {
      const start = Date.parse(`${date}T13:30:00Z`) + index * 300_000;
      return bar(new Date(start).toISOString(), new Date(start + 300_000).toISOString(), { interval: '5m' });
    }));
    expect(planGoToDate(sessions, day('2026-10-06', 'America/New_York'), 1_000)).toEqual({ kind: 'scroll', index: 78 });
    expect(sessions[78].start_time).toBe('2026-10-06T13:30:00.000Z');
  });

  it('goes to the crypto daily bar that opens in the local day, east and west of UTC', () => {
    const cryptoDaily = daily('2026-10-01T00:00:00Z', 6);
    // Tokyo: the 2026-10-03 00:00 UTC bar opens at 09:00 on the 3rd.
    expect(planGoToDate(cryptoDaily, day('2026-10-03', 'Asia/Tokyo'), 1_000)).toEqual({ kind: 'scroll', index: 2 });
    // Los Angeles: the bar opening on local 2026-10-03 is 2026-10-04 00:00 UTC (17:00 on the 3rd).
    expect(planGoToDate(cryptoDaily, day('2026-10-03', 'America/Los_Angeles'), 1_000)).toEqual({ kind: 'scroll', index: 3 });
    expect(planGoToDate(cryptoDaily, day('2026-10-03', 'UTC'), 1_000)).toEqual({ kind: 'scroll', index: 2 });
  });

  it('asks for more history when the day may start before the loaded bars', () => {
    const cryptoDaily = daily('2026-10-01T00:00:00Z', 6);
    expect(planGoToDate(cryptoDaily, day('2026-10-01', 'Asia/Tokyo'), 1_000)).toMatchObject({ kind: 'load-history' });
    // With nothing older to load, the first loaded bar of that day is the answer.
    expect(planGoToDate(cryptoDaily, day('2026-10-01', 'Asia/Tokyo'), 1_000, 5_000, true)).toEqual({ kind: 'scroll', index: 0 });
  });

  it('scrolls when the bar is loaded and asks for more history when it is not', () => {
    expect(planGoToDate(hourly, at('2026-10-02T10:00:00Z'), 1_000)).toEqual({ kind: 'scroll', index: 34 });
    // 10 days before the first bar at one bar an hour: ~339 bars back, with margin.
    const plan = planGoToDate(hourly, at('2026-09-21T00:00:00Z'), 100);
    expect(plan.kind).toBe('load-history');
    if (plan.kind === 'load-history') {
      expect(plan.limit).toBeGreaterThan(100 + 240);
      expect(plan.limit).toBeLessThanOrEqual(5_000);
    }
    expect(planGoToDate(hourly, at('2020-01-01T00:00:00Z'), 1_000)).toEqual({ kind: 'load-history', limit: 5_000 });
  });

  it('says why it cannot go further back', () => {
    expect(planGoToDate(hourly, at('2020-01-01T00:00:00Z'), 5_000)).toEqual({ kind: 'unavailable', reason: 'max-limit', earliest: hourly[0].start_time });
    expect(planGoToDate(hourly, at('2020-01-01T00:00:00Z'), 1_000, 5_000, true)).toEqual({ kind: 'unavailable', reason: 'no-earlier', earliest: hourly[0].start_time });
    expect(planGoToDate([], at('2020-01-01T00:00:00Z'), 1_000)).toEqual({ kind: 'unavailable', reason: 'no-data', earliest: null });
  });

  it('keeps the zoom and centres the target bar', () => {
    expect(centeredLogicalRange(50, { from: 10, to: 70 })).toEqual({ from: 20, to: 80 });
    expect(centeredLogicalRange(50, null)).toEqual({ from: -10, to: 110 });
  });
});

describe('pane maximise and collapse', () => {
  const state = { indicatorPaneCount: 2, mainPaneFullscreen: false, fullscreenIndicator: null };

  it('double-click maximises an indicator pane and Ctrl+double-click collapses it', () => {
    expect(paneDoubleClickAction({ kind: 'indicator', id: 'rsi' }, false, state)).toEqual({ type: 'toggle-indicator-fullscreen', id: 'rsi' });
    expect(paneDoubleClickAction({ kind: 'indicator', id: 'rsi' }, true, state)).toEqual({ type: 'toggle-indicator-minimized', id: 'rsi' });
    expect(paneDoubleClickAction({ kind: 'indicator', id: 'rsi' }, true, { ...state, fullscreenIndicator: 'rsi' })).toBeNull();
  });

  it('maximises the main pane only when there are panes to hide, and never collapses it', () => {
    expect(paneDoubleClickAction({ kind: 'main' }, false, state)).toEqual({ type: 'toggle-main-fullscreen' });
    expect(paneDoubleClickAction({ kind: 'main' }, false, { ...state, indicatorPaneCount: 0 })).toBeNull();
    expect(paneDoubleClickAction({ kind: 'main' }, false, { ...state, indicatorPaneCount: 0, mainPaneFullscreen: true })).toEqual({ type: 'toggle-main-fullscreen' });
    expect(paneDoubleClickAction({ kind: 'main' }, true, state)).toBeNull();
    expect(paneDoubleClickAction(null, false, state)).toBeNull();
  });
});

describe('copy chart image', () => {
  const png = 'data:image/png;base64,iVBORw0KGgo=';

  it('decodes a data URL into a typed blob', async () => {
    const blob = dataUrlToBlob(png);
    expect(blob.type).toBe('image/png');
    expect(blob.size).toBe(8);
    expect(() => dataUrlToBlob('https://example.com/a.png')).toThrow();
  });

  it('writes the PNG to the clipboard as a ClipboardItem', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    class FakeClipboardItem {
      constructor(public readonly items: Record<string, Blob>) {}
    }
    await copyImageDataUrlToClipboard(png, { write }, FakeClipboardItem as unknown as typeof ClipboardItem);
    expect(write).toHaveBeenCalledTimes(1);
    const [items] = write.mock.calls[0] as [FakeClipboardItem[]];
    expect(Object.keys(items[0].items)).toEqual(['image/png']);
    expect(items[0].items['image/png'].type).toBe('image/png');
  });

  it('fails clearly when the browser has no image clipboard', async () => {
    await expect(copyImageDataUrlToClipboard(png, undefined, undefined)).rejects.toThrow(/cannot copy images/);
  });
});

describe('market status and data delay', () => {
  it('labels each session', () => {
    expect(marketStatusLabel('open', true)).toBe('Market open 24/7');
    expect(marketStatusLabel('open', false)).toBe('Market open');
    expect(marketStatusLabel('pre_market', false)).toBe('Pre-market');
    expect(marketStatusLabel('post_market', false)).toBe('Post-market');
    expect(marketStatusLabel('closed', false)).toBe('Market closed');
    expect(marketStatusLabel('unknown', false)).toBeNull();
  });

  it('shows a delayed-data badge from the binding or the dataset', () => {
    expect(dataDelayLabel({ delay_seconds: 900 }, null)).toBe('Delayed 15 min');
    expect(dataDelayLabel({ delay_seconds: 0 }, { delay_seconds: 30 })).toBe('Delayed 30 s');
    expect(dataDelayLabel({ delay_seconds: 0 }, { delay_seconds: 0, freshness_mode: 'delayed' })).toBe('Delayed');
    expect(dataDelayLabel({ delay_seconds: 0 }, { delay_seconds: 0, freshness_mode: 'live' })).toBeNull();
    expect(dataDelayLabel(undefined, undefined)).toBeNull();
  });
});

describe('chart templates', () => {
  const indicators: CoreIndicatorInstance[] = [
    { id: 'rsi', period: 14, enabled: true },
    { id: 'sma', period: 50, enabled: true, style: { lineWidth: 2 } },
  ];

  it('stores style and indicators without the symbol or interval', () => {
    const payload = chartTemplatePayload({ name: ' Momentum ', chartType: 'heikin-ashi', settings: { extendedHours: false }, indicators });
    expect(payload).toMatchObject({ name: 'Momentum', templateKind: 'chart-template', chartType: 'heikin-ashi', settings: { extendedHours: false } });
    expect(payload).not.toHaveProperty('instrumentId');
    expect(payload).not.toHaveProperty('interval');
    const template = parseChartTemplate({ record_id: 'template-momentum-1', status: 'active', payload });
    expect(template).toEqual({
      recordId: 'template-momentum-1',
      name: 'Momentum',
      chartType: 'heikin-ashi',
      settings: { extendedHours: false },
      indicators,
    });
  });

  it('ignores indicator presets, archived records and malformed templates', () => {
    const payload = chartTemplatePayload({ name: 'A', chartType: 'line', indicators });
    expect(parseChartTemplate({ record_id: 'preset', status: 'active', payload: { name: 'Preset', indicators } })).toBeNull();
    expect(parseChartTemplate({ record_id: 'old', status: 'archived', payload })).toBeNull();
    expect(parseChartTemplate({ record_id: 'bad-type', status: 'active', payload: { ...payload, chartType: 'nope' } })).toBeNull();
    expect(parseChartTemplate({ record_id: 'bad-indicator', status: 'active', payload: { ...payload, indicators: [{ id: 'rsi', period: 0, enabled: true }] } })).toBeNull();
    expect(parseChartTemplate({ record_id: 'bad-settings', status: 'active', payload: { ...payload, settings: 'yes' } })).toBeNull();
    // A malformed setting inside a template falls back to its default.
    expect(parseChartTemplate({ record_id: 'odd-setting', status: 'active', payload: { ...payload, settings: { extendedHours: 'yes', barCountdown: false } } })?.settings).toEqual({ barCountdown: false });
  });
});

describe('chart snapshot links (TVP-2.1/2.5)', () => {
  it('uploads the image and copies an absolute link to it', async () => {
    const upload = vi.fn(async () => ({ url: '/api/trading/snapshots/abc.png' }));
    const writeText = vi.fn(async () => undefined);
    await expect(copyChartSnapshotLink('data:image/png;base64,AA', upload, { writeText }, 'https://omnix.example')).resolves.toBe('https://omnix.example/api/trading/snapshots/abc.png');
    expect(upload).toHaveBeenCalledWith('data:image/png;base64,AA');
    expect(writeText).toHaveBeenCalledWith('https://omnix.example/api/trading/snapshots/abc.png');
    await expect(copyChartSnapshotLink('data:image/png;base64,AA', upload, undefined, 'https://omnix.example')).rejects.toThrow('clipboard');
  });
});
