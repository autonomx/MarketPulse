// Time <-> bar index on a bar sequence: the one mapping drawings and drawing
// alerts use (TVP-0.4; the server's drawing alerts, TVP-1.4, implement the
// same spec and check it against resources/trading/drawing_alert_levels/).
//
// SPEC
//
// A timeline is the ascending list of distinct bar start times t[0..n-1] (UTC
// milliseconds) of one bar sequence, plus the step: the interval's duration in
// milliseconds (1m = 60 000, 1h = 3 600 000, 1D = 86 400 000, 1W = 604 800 000,
// 1M = 30 days = 2 592 000 000). A step is null only for intervals without a
// duration (tick, range, Renko bars); then the median gap between consecutive
// bars is used: the lower median, gaps[floor((m - 1) / 2)] of the m sorted
// gaps (gaps 1, 2, 3, 7 give 2). With fewer than two bars there is no step.
//
// - On the chart the sequence is every time point the chart plots. Comparison
//   series and indicator data on its own clock are plotted on the main series'
//   bars, so on time-based chart types this is the loaded bars' own times (the
//   chart checks this before offering a sloped alert level); brick-type charts
//   (Renko, range, line break, Kagi, P&F) plot their own times and offer only
//   flat alert levels. A sloped level is offered only when it starts within
//   the loaded bars.
//   For an alert it is the alert's own interval's bars, including the forming
//   (not yet closed) bar as the last index. Those bars must start at or before
//   the earliest anchor time (load enough history): an anchor before the first
//   bar falls under rule 4 and would step across gaps by the calendar, unlike
//   the chart that drew it.
//
// index(time):
//   1. n = 0: no index (null).
//   2. time = t[i] exactly: i.
//   3. t[i] < time < t[i+1]: i + (time - t[i]) / (t[i+1] - t[i]), interpolated
//      between the two surrounding bars, so gaps (overnight, weekends) never
//      shift later bars.
//   4. time < t[0]: (time - t[0]) / step (negative).
//   5. time > t[n-1]: (n - 1) + (time - t[n-1]) / step.
//   Rules 4 and 5 need a step; without one they give no index.
//
// time(index) is the exact inverse:
//   1. integer i in [0, n-1]: t[i].
//   2. 0 < i < n-1, fractional: t[k] + (i - k) * (t[k+1] - t[k]), k = floor(i).
//   3. i < 0: t[0] + i * step;  i > n-1: t[n-1] + (i - (n-1)) * step.
//   Times are rounded to whole milliseconds as floor(x + 0.5) (halves round
//   up, also below zero; not to even), so index(time(i)) equals i to within
//   1 / gap-in-milliseconds.
//
// An alert line (DrawingAlertLevel) is straight in this index space: with
// anchors (time a, price pa) and (time b, price pb), a < b, its price at index
// x is pa + (pb - pa) * (x - index(a)) / (index(b) - index(a)). `extend`
// says whether it continues left of index(a) and right of index(b); outside
// those it has no price.

export type BarTimeline = {
  /** Ascending, distinct bar start times in UTC milliseconds. */
  readonly times: ArrayLike<number>;
  /** The step past the data: the interval's duration, else the lower median gap (computed once); null without either. */
  readonly step: number | null;
};

const UNIT_MS: Readonly<Record<string, number>> = {
  s: 1_000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
  mo: 2_592_000_000,
};

/** The step of an Omnix interval id (`30s`, `5m`, `4h`, `1d`, `1w`, `1mo`); null for tick (`100t`) and range (`10r`) bars. */
export function intervalStepMs(interval: string): number | null {
  const match = /^(\d+)(mo|s|m|h|d|w)$/.exec(interval.trim().toLowerCase());
  if (!match) return null;
  const amount = Number(match[1]);
  return amount > 0 ? amount * UNIT_MS[match[2]] : null;
}

/**
 * Builds a timeline from bar start times in any order (duplicates and invalid
 * times dropped). `step` is the interval's duration; without one the median
 * gap between bars is used.
 */
export function createBarTimeline(times: Iterable<number>, step: number | null): BarTimeline {
  const sorted = Float64Array.from(new Set([...times].filter(Number.isFinite))).sort();
  const duration = step !== null && Number.isFinite(step) && step > 0 ? step : null;
  return { times: sorted, step: duration ?? medianGap(sorted) };
}

function medianGap(times: Float64Array): number | null {
  if (times.length < 2) return null;
  const gaps = new Float64Array(times.length - 1);
  for (let index = 1; index < times.length; index += 1) gaps[index - 1] = times[index] - times[index - 1];
  gaps.sort();
  return gaps[Math.floor((gaps.length - 1) / 2)];
}

/** Index of the last bar at or before `milliseconds`; -1 before the first bar (or with no bars). */
export function barIndexAtOrBefore(timeline: BarTimeline, milliseconds: number): number {
  const { times } = timeline;
  let low = 0;
  let high = times.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (times[middle] <= milliseconds) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found;
}

/** index(time) of the spec; null without bars, for invalid times, or past the data without a step. */
export function barIndexForTime(timeline: BarTimeline, milliseconds: number): number | null {
  const { times, step } = timeline;
  if (times.length === 0 || !Number.isFinite(milliseconds)) return null;
  const last = times.length - 1;
  const lower = barIndexAtOrBefore(timeline, milliseconds);
  if (lower >= 0 && times[lower] === milliseconds) return lower;
  if (lower >= 0 && lower < last) return lower + (milliseconds - times[lower]) / (times[lower + 1] - times[lower]);
  if (step === null) return null;
  return lower < 0 ? (milliseconds - times[0]) / step : last + (milliseconds - times[last]) / step;
}

/** floor(x + 0.5), as the spec states it (equal to Math.round, written out for the server's port). */
function roundHalfUp(value: number): number {
  return Math.floor(value + 0.5);
}

/** time(index) of the spec, in UTC milliseconds; null without bars or past the data without a step. */
export function timeForBarIndex(timeline: BarTimeline, index: number): number | null {
  const { times, step } = timeline;
  if (times.length === 0 || !Number.isFinite(index)) return null;
  const last = times.length - 1;
  if (Number.isInteger(index) && index >= 0 && index <= last) return times[index];
  if (index > 0 && index < last) {
    const lower = Math.floor(index);
    return roundHalfUp(times[lower] + (times[lower + 1] - times[lower]) * (index - lower));
  }
  if (step === null) return null;
  return index < 0 ? roundHalfUp(times[0] + index * step) : roundHalfUp(times[last] + (index - last) * step);
}
