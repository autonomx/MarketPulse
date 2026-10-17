// @vitest-environment node
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { alertLevelPriceAt } from './alertLevels';
import { barIndexAtOrBefore, barIndexForTime, createBarTimeline, intervalStepMs, timeForBarIndex } from './barTimeline';
import type { DrawingAlertLevel } from './types';

// Shared with the server's drawing alerts (TVP-1.4), which must give the same values.
const CASES_PATH = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../../resources/trading/drawing_alert_levels/bar_index_cases.json');

type Cases = {
  tolerance: { index: number; price: number };
  timelines: {
    timeline: string;
    bar_times: string[];
    step_ms: number | null;
    index_for_time: { time: string; index: number | null }[];
    time_for_index: { index: number; time: string | null }[];
  }[];
  alert_levels: { name: string; timeline: string; level: DrawingAlertLevel; price_at_index: { index: number; price: number | null }[] }[];
};

const cases = JSON.parse(readFileSync(CASES_PATH, 'utf8')) as Cases;
const timelineOf = (name: string) => {
  const entry = cases.timelines.find((item) => item.timeline === name)!;
  return createBarTimeline(entry.bar_times.map((time) => Date.parse(time)), entry.step_ms);
};

describe('bar timeline spec (shared fixtures)', () => {
  for (const entry of cases.timelines) {
    it(`maps times and indices on ${entry.timeline}`, () => {
      const timeline = timelineOf(entry.timeline);
      for (const query of entry.index_for_time) {
        const index = barIndexForTime(timeline, Date.parse(query.time));
        if (query.index === null) expect(index, query.time).toBeNull();
        else expect(index, query.time).toBeCloseTo(query.index, 9);
      }
      for (const query of entry.time_for_index) {
        const time = timeForBarIndex(timeline, query.index);
        expect(time === null ? null : new Date(time).toISOString(), String(query.index)).toBe(query.time);
      }
    });
  }

  for (const entry of cases.alert_levels) {
    it(`evaluates the ${entry.name} alert line by bar index`, () => {
      const timeline = timelineOf(entry.timeline);
      const indexFor = (time: string) => barIndexForTime(timeline, Date.parse(time));
      for (const query of entry.price_at_index) {
        const price = alertLevelPriceAt(entry.level, query.index, indexFor);
        if (query.price === null) expect(price, String(query.index)).toBeNull();
        else expect(price, String(query.index)).toBeCloseTo(query.price, 9);
      }
    });
  }
});

describe('bar timeline', () => {
  it('is an exact inverse on irregular gaps', () => {
    let time = Date.parse('2026-10-05T13:30:00Z');
    const times = Array.from({ length: 500 }, (_, index) => {
      time += (index % 7 === 0 ? 3_600_000 : 60_000) + (index % 3) * 17;
      return time;
    });
    const timeline = createBarTimeline(times, 60_000);
    for (let index = -50; index < 560; index += 0.731) {
      const at = timeForBarIndex(timeline, index)!;
      const back = barIndexForTime(timeline, at)!;
      expect(Math.abs(back - index)).toBeLessThan(1e-4);
      expect(timeForBarIndex(timeline, back)).toBe(at);
    }
  });

  it('finds the bar at or before a time, -1 before the first', () => {
    const timeline = createBarTimeline([3_000, 1_000, 2_000, 2_000], 1_000);
    expect(Array.from(timeline.times)).toEqual([1_000, 2_000, 3_000]);
    expect(barIndexAtOrBefore(timeline, 500)).toBe(-1);
    expect(barIndexAtOrBefore(timeline, 2_500)).toBe(1);
    expect(barIndexAtOrBefore(timeline, 9_000)).toBe(2);
    expect(barIndexAtOrBefore(createBarTimeline([1_000], 1_000), 9_000)).toBe(0);
    expect(barIndexAtOrBefore(createBarTimeline([], 1_000), 9_000)).toBe(-1);
  });

  it('knows the duration of every Omnix interval with one', () => {
    expect(intervalStepMs('30s')).toBe(30_000);
    expect(intervalStepMs('5m')).toBe(300_000);
    expect(intervalStepMs('4h')).toBe(14_400_000);
    expect(intervalStepMs('1d')).toBe(86_400_000);
    expect(intervalStepMs('2w')).toBe(1_209_600_000);
    expect(intervalStepMs('3mo')).toBe(7_776_000_000);
    expect(intervalStepMs('100t')).toBeNull();
    expect(intervalStepMs('10r')).toBeNull();
  });
});
