// The Volume Delta and CVD contract (TVP-6.4): the chart's values for fixed chart and lower bars, written as a fixture.
// src/tests/trading/test_trading_intrabar_indicators.py computes the same values with the server's port
// (indicators/intrabar.py), so an alert or a screen on these indicators reads what the chart draws.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import { cumulativeVolumeDeltaPoints, intrabarDeltas, volumeDeltaPoints } from './intrabarIndicators';
import { UTC_SESSION, type TradingSessionSpec } from './tradingSessions';

// Tests run from the web package directory.
const FIXTURE = resolve(process.cwd(), 'src/features/trading/indicators/fixtures/intrabarIndicatorContract.json');

const START = Date.UTC(2026, 1, 28, 18); // a Saturday evening: the 60 hours cross days, a month (1 March) and a week (2 March)
const HOUR = 3_600_000;
const MINUTE = 60_000;

function bar(start: number, length: number, open: number, close: number, volume: number, session = 'regular'): MarketBar {
  return {
    instrument_id: 'fixture', interval: length === HOUR ? '1h' : '1m', start_time: new Date(start).toISOString(), end_time: new Date(start + length).toISOString(),
    open: String(open), high: String(Math.max(open, close) + 1), low: String(Math.min(open, close) - 1), close: String(close), volume: String(volume),
    is_final: true, provider: 'fixture', received_at: '', session,
  } as MarketBar;
}

// 60 one-hour chart bars; 1m lower bars for all but the 4th (a gap) with unchanged closes, unchanged against the
// previous close, and runs, so every direction rule is used.
const chartBars = Array.from({ length: 60 }, (_, index) => bar(START + index * HOUR, HOUR, 100, 101, 0, index % 24 < 14 ? 'regular' : 'extended_post'));
const lowerBars: MarketBar[] = [];
let price = 100;
for (let minute = 0; minute < 60 * 60; minute += 1) {
  if (Math.floor(minute / 60) === 3) continue;
  const step = [1, -2, 0, 0, 3, -1, 0, 2, -3, 0][(minute * 7 + Math.floor(minute / 13)) % 10];
  const open = minute % 11 === 0 ? price + 0.5 : price;
  const close = step === 0 && minute % 3 === 0 ? open : price + step;
  lowerBars.push(bar(START + minute * MINUTE, MINUTE, open, close, 1 + ((minute * 37) % 50)));
  price = close;
}

const US_EQUITY: TradingSessionSpec = { timezone: 'America/New_York', startMinute: 0, regularStartMinute: 570, regularOnly: true };

describe('Volume Delta and CVD contract (TVP-6.4)', () => {
  it("records the chart's values for fixed bars", () => {
    const deltas = intrabarDeltas(chartBars, lowerBars, '1h');
    const values = (points: ReturnType<typeof volumeDeltaPoints>) => points.map((point) => [point.time, point.value]);
    const cases = [
      { indicator: 'tv-volume-delta', params: {}, session: null, values: values(volumeDeltaPoints(chartBars, deltas)) },
      ...(['D', 'W', 'M'] as const).flatMap((anchor) => [
        { indicator: 'tv-cumulative-volume-delta', params: { anchor }, session: null, values: values(cumulativeVolumeDeltaPoints(chartBars, deltas, anchor, UTC_SESSION)) },
        { indicator: 'tv-cumulative-volume-delta', params: { anchor }, session: US_EQUITY, values: values(cumulativeVolumeDeltaPoints(chartBars, deltas, anchor, US_EQUITY)) },
      ]),
    ];
    // Compact rows: chart bars [start, session], lower bars [start, open, close, volume] (1h and 1m long).
    const fixture = {
      note: 'Written by intrabarIndicatorContract.test.ts (UPDATE_INTRABAR_CONTRACT=1); checked by test_trading_intrabar_indicators.py.',
      interval: '1h', lowerInterval: '1m',
      chartBars: chartBars.map((item) => [item.start_time, item.session]),
      lowerBars: lowerBars.map((item) => [item.start_time, Number(item.open), Number(item.close), Number(item.volume)]),
      cases,
    };
    if (process.env.UPDATE_INTRABAR_CONTRACT) writeFileSync(FIXTURE, `${JSON.stringify(fixture)}\n`);
    const stored = JSON.parse(readFileSync(FIXTURE, 'utf-8'));
    expect(stored.cases).toEqual(JSON.parse(JSON.stringify(cases)));
    // The fixture uses every direction rule and the gap: some deltas are negative, one bar has none.
    expect(cases[0].values.length).toBe(59);
    expect(cases[0].values.some(([, value]) => Number(value) < 0)).toBe(true);
  });
});
