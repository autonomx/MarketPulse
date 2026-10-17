/**
 * Trading sessions for session-aware indicators (TWAP, pivots, Relative Volume at Time).
 *
 * A bar's session is a calendar date in the instrument's exchange timezone. Markets whose trading day starts the evening
 * before (futures and forex, e.g. 18:00 ET) use `startMinute`: the session starting at that local time belongs to the next
 * calendar date. 24x7 instruments, and any chart without a session, use UTC dates. The server mirrors this module in
 * `server_indicators/_sessions.py` (zoneinfo); both read the same IANA timezone rules.
 */
import type { MarketBar } from '../tradingTypes';

export type TradingSessionSpec = {
  /** IANA timezone of the session calendar; an unknown zone falls back to UTC. */
  timezone: string;
  /** Local minute the trading day starts: 0 is midnight, 1080 is 18:00 of the previous calendar day. */
  startMinute: number;
  /** Local minute the regular session opens; intraday pivot periods (1h, 4h, 8h) are anchored there. */
  regularStartMinute?: number | null;
  /** Session pivots and periods use only regular-hours bars (labelled `extended_pre`, `extended_post` or `closed` are skipped). */
  regularOnly?: boolean;
};

export const UTC_SESSION: TradingSessionSpec = { timezone: 'UTC', startMinute: 0 };

const DAY_MS = 86_400_000;
const MINUTE_MS = 60_000;
const OUTSIDE_REGULAR_HOURS = new Set(['extended_pre', 'extended_post', 'closed']);

type SessionInstrument = { asset_class?: string; session_calendar?: string; exchange_timezone?: string; instrument_type?: string };

/** The session calendar of an instrument: UTC for 24x7 markets, the regular session in New York for US equities, 17:00/18:00 ET roll for forex and commodities. */
export function sessionForInstrument(instrument: SessionInstrument | null | undefined): TradingSessionSpec {
  // Asset class decides before the calendar: catalog futures are tagged `24x7` but trade from the 18:00 ET roll.
  if (!instrument || instrument.asset_class === 'crypto') return UTC_SESSION;
  if (instrument.asset_class === 'forex') return { timezone: 'America/New_York', startMinute: 1020 };
  if (instrument.asset_class === 'commodity') return { timezone: 'America/New_York', startMinute: 1080 };
  if (instrument.session_calendar === '24x7') return UTC_SESSION;
  if (instrument.asset_class === 'equity' || instrument.instrument_type === 'equity') {
    return { timezone: instrument.exchange_timezone || 'America/New_York', startMinute: 0, regularStartMinute: 570, regularOnly: true };
  }
  return UTC_SESSION;
}

const formatters = new Map<string, Intl.DateTimeFormat | null>();

function formatter(timezone: string): Intl.DateTimeFormat | null {
  if (!formatters.has(timezone)) {
    try {
      formatters.set(timezone, new Intl.DateTimeFormat('en-US', {
        timeZone: timezone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
      }));
    } catch {
      formatters.set(timezone, null);
    }
  }
  return formatters.get(timezone) ?? null;
}

/** Local wall-clock time of an epoch-millisecond instant, as epoch milliseconds of the same wall clock in UTC. */
export function wallClockMs(utcMs: number, timezone: string): number {
  const format = timezone === 'UTC' ? null : formatter(timezone);
  if (!format || !Number.isFinite(utcMs)) return utcMs;
  const parts: Record<string, number> = {};
  for (const part of format.formatToParts(new Date(utcMs))) {
    if (part.type !== 'literal') parts[part.type] = Number(part.value);
  }
  const milliseconds = ((utcMs % 1000) + 1000) % 1000;
  return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second, milliseconds);
}

export type SessionClock = {
  /** Session date as a day number (days since 1970-01-01). */
  day: number[];
  /** Milliseconds since the session started. */
  offset: number[];
  /** Whether the bar's high, low and close count towards its session's levels. */
  counts: boolean[];
};

/** Each bar's session day, time into the session, and whether it counts (regular hours when the session asks for that). */
export function sessionClock(bars: readonly MarketBar[], times: readonly number[], spec: TradingSessionSpec = UTC_SESSION): SessionClock {
  const shift = spec.startMinute > 0 ? (1440 - spec.startMinute) * MINUTE_MS : 0;
  const day: number[] = []; const offset: number[] = []; const counts: boolean[] = [];
  times.forEach((time, i) => {
    const shifted = wallClockMs(time, spec.timezone) + shift;
    const sessionDay = Math.floor(shifted / DAY_MS);
    day.push(sessionDay);
    offset.push(shifted - sessionDay * DAY_MS);
    counts.push(!(spec.regularOnly && OUTSIDE_REGULAR_HOURS.has(String(bars[i]?.session ?? ''))));
  });
  return { day, offset, counts };
}

/** A period length: whole hours (anchored at the regular open, else at the session start), or a session day, week (Monday) or month. */
export type SessionPeriod = number | 'D' | 'W' | 'M';

/** Day number of the Monday of a session day's week (1970-01-01 was a Thursday). */
function weekStart(day: number): number { return day - (((day + 3) % 7) + 7) % 7; }

function monthStart(day: number): [number, number] {
  const date = new Date(day * DAY_MS);
  return [date.getUTCFullYear() * 12 + date.getUTCMonth(), day - (date.getUTCDate() - 1)];
}

/** Each bar's period key and its time since that period started. Hour periods have keys unique across sessions. */
export function sessionPeriods(clock: SessionClock, period: SessionPeriod, spec: TradingSessionSpec = UTC_SESSION): { key: number[]; since: number[] } {
  const key: number[] = []; const since: number[] = [];
  const anchor = spec.regularStartMinute === undefined || spec.regularStartMinute === null
    ? 0
    : ((((spec.regularStartMinute - spec.startMinute) % 1440) + 1440) % 1440) * MINUTE_MS;
  clock.day.forEach((day, i) => {
    const offset = clock.offset[i];
    if (period === 'D') { key.push(day); since.push(offset); return; }
    if (period === 'W') { const start = weekStart(day); key.push(start); since.push((day - start) * DAY_MS + offset); return; }
    if (period === 'M') { const [month, start] = monthStart(day); key.push(month); since.push((day - start) * DAY_MS + offset); return; }
    const length = period * 3_600_000;
    const block = Math.floor((offset - anchor) / length);
    key.push(day * 100 + block + 50);
    since.push(offset - anchor - block * length);
  });
  return { key, since };
}
