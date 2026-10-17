import { intervalAvailability, parseTradingInterval, type TradingIntervalParseResult } from './tradingIntervals';

export type IntervalInputResult =
  | { ok: true; interval: string }
  | { ok: false; error: string };

/**
 * Reads what was typed in the interval box with the interval menu's parser (TVP-2.5): a count and a
 * unit (`5`, `7m`, `3h`, `1D`, `3s`, `100t`, `1M`, `10R`). A plain number is minutes, and whole hours
 * of minutes become hours (`60` is `1h`, `240` is `4h`), as in TradingView's box.
 */
function readTradingIntervalInput(text: string): TradingIntervalParseResult {
  const parsed = parseTradingInterval(text);
  if (!parsed.ok || parsed.unit !== 'm' || parsed.count < 60 || parsed.count % 60 !== 0) return parsed;
  const hours = parsed.count / 60;
  return { ok: true, value: `${hours}h`, count: hours, unit: 'h' };
}

/** The canonical interval typed, or null when the text is not an interval. */
export function parseTradingIntervalInput(text: string): string | null {
  const parsed = readTradingIntervalInput(text);
  return parsed.ok ? parsed.value : null;
}

/** Parses typed text and checks the chart's feed can show that interval, directly or by aggregation. */
export function resolveTradingIntervalInput(
  text: string,
  supportedIntervals: readonly string[],
  feedName?: string,
): IntervalInputResult {
  const parsed = readTradingIntervalInput(text);
  if (!parsed.ok) return { ok: false, error: parsed.error };
  if (supportedIntervals.length > 0) {
    const availability = intervalAvailability(parsed.value, supportedIntervals, feedName);
    if (!availability.available) return { ok: false, error: availability.reason ?? `${parsed.value} is not available.` };
  }
  return { ok: true, interval: parsed.value };
}
