/**
 * Seasonals (TVP-10.3): how a symbol moved through each calendar year, from its daily bars. Each year's curve is the
 * change since its first close, by day of the year, for the whole year (the chart's Seasonality indicator draws them
 * only up to today's date); monthly returns are each month's last close against the month before's.
 */
import type { MarketBar } from './tradingTypes';

export type YearCurve = { year: number; points: Array<{ day: number; change: number }>; complete: boolean };

const DAY_MS = 86_400_000;

/** Day of the year from 0, on a 366-day axis so 1 March is the same day in every year. */
export function dayOfYear(time: number): number {
  const date = new Date(time);
  const month = date.getUTCMonth();
  const startOfMonth = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335][month];
  return startOfMonth + date.getUTCDate() - 1;
}

function closes(bars: readonly MarketBar[]): Array<{ time: number; close: number }> {
  return bars
    .map((bar) => ({ time: Date.parse(bar.start_time), close: Number(bar.close) }))
    .filter((item) => Number.isFinite(item.time) && Number.isFinite(item.close) && item.close > 0)
    .sort((left, right) => left.time - right.time);
}

/** Whether the bars are daily or coarser (a median gap of at least 20 hours). */
export function isDailyHistory(bars: readonly MarketBar[]): boolean {
  const items = closes(bars);
  const gaps = items.slice(1).map((item, index) => item.time - items[index].time).sort((left, right) => left - right);
  return gaps.length > 0 && gaps[Math.floor(gaps.length / 2)] >= 20 * 3_600_000;
}

/**
 * Each of the latest `years` calendar years as the change (%) since its first close. A year whose first bar is after
 * 15 January (history starting mid-year) is left out; the current year is incomplete.
 */
export function yearlyCurves(bars: readonly MarketBar[], years: number): YearCurve[] {
  const items = closes(bars);
  if (items.length < 2) return [];
  const last = items[items.length - 1];
  const current = new Date(last.time).getUTCFullYear();
  const byYear = new Map<number, Array<{ time: number; close: number }>>();
  for (const item of items) {
    const year = new Date(item.time).getUTCFullYear();
    if (current - year >= years) continue;
    const list = byYear.get(year) ?? [];
    list.push(item);
    byYear.set(year, list);
  }
  const curves: YearCurve[] = [];
  for (const [year, list] of [...byYear].sort((left, right) => right[0] - left[0])) {
    if (dayOfYear(list[0].time) > 14) continue;
    const base = list[0].close;
    curves.push({
      year,
      points: list.map((item) => ({ day: dayOfYear(item.time), change: (item.close / base - 1) * 100 })),
      complete: year !== current || last.time + 7 * DAY_MS >= Date.UTC(year + 1, 0, 1),
    });
  }
  return curves;
}

/** The average of complete years' curves, by day of the year (each year carried to the day from its last close). */
export function averageCurve(curves: readonly YearCurve[]): Array<{ day: number; change: number }> {
  const complete = curves.filter((curve) => curve.complete && curve.points.length > 0);
  if (complete.length === 0) return [];
  const average: Array<{ day: number; change: number }> = [];
  const cursors = complete.map(() => 0);
  for (let day = 0; day < 366; day += 1) {
    let total = 0;
    complete.forEach((curve, index) => {
      while (cursors[index] + 1 < curve.points.length && curve.points[cursors[index] + 1].day <= day) cursors[index] += 1;
      total += curve.points[cursors[index]].change;
    });
    average.push({ day, change: total / complete.length });
  }
  return average;
}

/** Each year's monthly returns (%): a month's last close against the previous month's; null where history is missing. */
export function monthlyReturns(bars: readonly MarketBar[], years: number): Array<{ year: number; months: Array<number | null>; total: number | null }> {
  const items = closes(bars);
  if (items.length === 0) return [];
  const monthEnd = new Map<number, number>();
  for (const item of items) {
    const date = new Date(item.time);
    monthEnd.set(date.getUTCFullYear() * 12 + date.getUTCMonth(), item.close);
  }
  const current = new Date(items[items.length - 1].time).getUTCFullYear();
  const rows: Array<{ year: number; months: Array<number | null>; total: number | null }> = [];
  for (let year = current; year > current - years; year -= 1) {
    const months = Array.from({ length: 12 }, (_, month) => {
      const close = monthEnd.get(year * 12 + month);
      const previous = monthEnd.get(year * 12 + month - 1);
      return close !== undefined && previous !== undefined ? (close / previous - 1) * 100 : null;
    });
    if (months.every((value) => value === null)) continue;
    const yearEnd = [...Array(12).keys()].reverse().map((month) => monthEnd.get(year * 12 + month)).find((value) => value !== undefined);
    const previousYearEnd = monthEnd.get(year * 12 - 1);
    rows.push({ year, months, total: yearEnd !== undefined && previousYearEnd !== undefined ? (yearEnd / previousYearEnd - 1) * 100 : null });
  }
  return rows;
}

/** The average of each month across the years that have it, and the share of years it rose. */
export function monthlySummary(rows: ReadonlyArray<{ months: Array<number | null> }>): Array<{ average: number | null; up: number | null }> {
  return Array.from({ length: 12 }, (_, month) => {
    const values = rows.map((row) => row.months[month]).filter((value): value is number => value !== null);
    if (values.length === 0) return { average: null, up: null };
    return { average: values.reduce((sum, value) => sum + value, 0) / values.length, up: values.filter((value) => value > 0).length / values.length };
  });
}
