import type { Time } from 'lightweight-charts';

export type TradingTimezoneOption = {
  id: string;
  label: string;
  timeZone: string | null;
};

export const TRADING_TIMEZONE_OPTIONS: readonly TradingTimezoneOption[] = [
  { id: 'utc', label: 'UTC', timeZone: 'UTC' },
  { id: 'exchange', label: 'Exchange', timeZone: null },
  { id: 'honolulu', label: 'Honolulu', timeZone: 'Pacific/Honolulu' },
  { id: 'anchorage', label: 'Anchorage', timeZone: 'America/Anchorage' },
  { id: 'juneau', label: 'Juneau', timeZone: 'America/Juneau' },
  { id: 'los-angeles', label: 'Los Angeles', timeZone: 'America/Los_Angeles' },
  { id: 'phoenix', label: 'Phoenix', timeZone: 'America/Phoenix' },
  { id: 'vancouver', label: 'Vancouver', timeZone: 'America/Vancouver' },
  { id: 'denver', label: 'Denver', timeZone: 'America/Denver' },
  { id: 'mexico-city', label: 'Mexico City', timeZone: 'America/Mexico_City' },
  { id: 'san-salvador', label: 'San Salvador', timeZone: 'America/El_Salvador' },
  { id: 'bogota', label: 'Bogota', timeZone: 'America/Bogota' },
  { id: 'chicago', label: 'Chicago', timeZone: 'America/Chicago' },
  { id: 'lima', label: 'Lima', timeZone: 'America/Lima' },
  { id: 'caracas', label: 'Caracas', timeZone: 'America/Caracas' },
  { id: 'new-york', label: 'New York', timeZone: 'America/New_York' },
  { id: 'santiago', label: 'Santiago', timeZone: 'America/Santiago' },
  { id: 'toronto', label: 'Toronto', timeZone: 'America/Toronto' },
  { id: 'buenos-aires', label: 'Buenos Aires', timeZone: 'America/Argentina/Buenos_Aires' },
  { id: 'halifax', label: 'Halifax', timeZone: 'America/Halifax' },
  { id: 'sao-paulo', label: 'Sao Paulo', timeZone: 'America/Sao_Paulo' },
  { id: 'azores', label: 'Azores', timeZone: 'Atlantic/Azores' },
  { id: 'reykjavik', label: 'Reykjavik', timeZone: 'Atlantic/Reykjavik' },
] as const;

const TIMEZONE_STORAGE_KEY = 'omnix.trading.chart.timezone';

function validTimeZone(value: string | null | undefined): string {
  if (!value) return 'UTC';
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value }).format();
    return value;
  } catch {
    return 'UTC';
  }
}

function localTimeZone(): string {
  try {
    return validTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
  } catch {
    return 'UTC';
  }
}

export function defaultTradingTimezoneId(): string {
  const local = localTimeZone();
  return TRADING_TIMEZONE_OPTIONS.find((option) => option.timeZone === local)?.id ?? 'utc';
}

export function readTradingTimezoneId(): string {
  if (typeof window === 'undefined') return defaultTradingTimezoneId();
  try {
    const stored = window.localStorage.getItem(TIMEZONE_STORAGE_KEY);
    return TRADING_TIMEZONE_OPTIONS.some((option) => option.id === stored)
      ? stored as string
      : defaultTradingTimezoneId();
  } catch {
    return defaultTradingTimezoneId();
  }
}

export function writeTradingTimezoneId(id: string): void {
  try {
    window.localStorage.setItem(TIMEZONE_STORAGE_KEY, id);
  } catch {
    // The selected timezone remains active for this chart when storage is unavailable.
  }
}

export function resolveTradingTimezone(id: string, exchangeTimezone?: string | null): string {
  const option = TRADING_TIMEZONE_OPTIONS.find((item) => item.id === id);
  return validTimeZone(option?.timeZone ?? exchangeTimezone ?? 'UTC');
}

function dateValue(value: Date | number | string): Date {
  const result = value instanceof Date ? value : new Date(value);
  return Number.isFinite(result.getTime()) ? result : new Date(0);
}

export function formatTradingTime(value: Date | number | string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: validTimeZone(timeZone),
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }).format(dateValue(value));
}

export function formatTradingTimezoneOffset(value: Date | number | string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: validTimeZone(timeZone),
    timeZoneName: 'shortOffset',
  }).formatToParts(dateValue(value));
  const offset = parts.find((part) => part.type === 'timeZoneName')?.value ?? 'UTC';
  if (offset === 'GMT' || offset === 'UTC') return 'UTC';
  return offset.replace(/^GMT/u, 'UTC').replace(/:00$/u, '');
}

function timeFromChartValue(value: Time): Date {
  if (typeof value === 'number') return new Date(value * 1_000);
  if (typeof value === 'string') return new Date(`${value}T00:00:00Z`);
  return new Date(Date.UTC(value.year, value.month - 1, value.day));
}

/**
 * The crosshair's time-axis label, as in TradingView: "Fri 09 Oct '26 10:00". Daily and longer bars show
 * the date alone; bars shorter than a minute add seconds.
 */
export function formatTradingChartTime(value: Time, timeZone: string, barSeconds?: number | null): string {
  const businessDay = typeof value !== 'number';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: businessDay ? 'UTC' : validTimeZone(timeZone),
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(timeFromChartValue(value))
    .filter((part) => part.type !== 'literal')
    .map((part) => [part.type, part.value]));
  const date = `${parts.weekday} ${parts.day} ${parts.month} '${parts.year}`;
  if (businessDay || (barSeconds != null && barSeconds >= 86_400)) return date;
  const seconds = barSeconds != null && barSeconds < 60 ? `:${parts.second}` : '';
  return `${date} ${parts.hour}:${parts.minute}${seconds}`;
}

export function formatTradingChartTick(value: Time, timeZone: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: validTimeZone(timeZone),
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(timeFromChartValue(value));
}

function timezoneParts(value: Date, timeZone: string): Record<string, number> {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: validTimeZone(timeZone),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(value);
  return Object.fromEntries(parts
    .filter((part) => part.type !== 'literal')
    .map((part) => [part.type, Number(part.value)]));
}

export function dateInputValue(value: Date | number | string, timeZone: string): string {
  const parts = timezoneParts(dateValue(value), timeZone);
  return `${String(parts.year).padStart(4, '0')}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`;
}

function parsedDateTime(value: string, endOfDay: boolean): { year: number; month: number; day: number; hour: number; minute: number; second: number; millisecond: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?$/u.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match;
  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: hour === undefined ? (endOfDay ? 23 : 0) : Number(hour),
    minute: minute === undefined ? (endOfDay ? 59 : 0) : Number(minute),
    second: second === undefined ? (endOfDay ? 59 : 0) : Number(second),
    millisecond: endOfDay && hour === undefined ? 999 : 0,
  };
}

export function tradingDateRangeWithinLoadedHistory(
  from: string,
  to: string,
  firstLoadedAt: Date | number | string,
  lastLoadedAt: Date | number | string,
  timeZone: string,
): boolean {
  if (!from || !to) return false;
  const firstLoadedDate = dateInputValue(firstLoadedAt, timeZone);
  const lastLoadedDate = dateInputValue(lastLoadedAt, timeZone);
  return from >= firstLoadedDate && to <= lastLoadedDate;
}


export function zonedDateTimeToUtc(value: string, timeZone: string, endOfDay = false): number | null {
  const parsed = parsedDateTime(value, endOfDay);
  if (!parsed) return null;
  const wallTime = Date.UTC(parsed.year, parsed.month - 1, parsed.day, parsed.hour, parsed.minute, parsed.second, parsed.millisecond);
  let result = wallTime;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const local = timezoneParts(new Date(result), timeZone);
    const localAsUtc = Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second, parsed.millisecond);
    result = wallTime - (localAsUtc - result);
  }
  return result;
}
