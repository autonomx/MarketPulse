import { describe, expect, it } from 'vitest';
import {
  aggregationBaseInterval,
  intervalAvailability,
  intervalCompactLabel,
  intervalMenuLabel,
  isIntervalAvailable,
  isIntradayInterval,
  normalizeFavoriteIntervals,
  parseTradingInterval,
  sortTradingIntervals,
  tradingIntervalDurationMs,
  TRADING_VIEW_INTERVAL_GROUPS,
} from './tradingIntervals';

describe('TradingView interval catalog', () => {
  it('includes every displayed interval category', () => {
    expect(TRADING_VIEW_INTERVAL_GROUPS.map((group) => group.label)).toEqual([
      'Ticks',
      'Seconds',
      'Minutes',
      'Hours',
      'Days',
      'Weeks',
      'Months',
      'Ranges',
    ]);
    expect(TRADING_VIEW_INTERVAL_GROUPS.flatMap((group) => group.options)).toHaveLength(47);
  });

  it('keeps compact toolbar labels distinct from menu labels', () => {
    expect(intervalMenuLabel('1m')).toBe('1 minute');
    expect(intervalMenuLabel('1mo')).toBe('1 month');
    expect(intervalCompactLabel('1m')).toBe('1m');
    expect(intervalCompactLabel('1mo')).toBe('1M');
  });

  it('marks intervals derivable from a supported base as available', () => {
    const supported = ['1m', '5m', '15m', '1h', '1d'];
    expect(aggregationBaseInterval('2h', supported)).toBe('1h');
    expect(aggregationBaseInterval('30m', supported)).toBe('15m');
    expect(isIntervalAvailable('2d', supported)).toBe(true);
    expect(isIntervalAvailable('1s', supported)).toBe(false);
  });
});

describe('custom intervals (TVP-2.5)', () => {
  it.each([
    ['7m', '7m'],
    ['7', '7m'],
    ['3h', '3h'],
    ['3H', '3h'],
    ['2D', '2d'],
    ['D', '1d'],
    ['2W', '2w'],
    ['3M', '3mo'],
    ['3mo', '3mo'],
    ['500t', '500t'],
    ['10R', '10r'],
    ['30s', '30s'],
    [' 45 m ', '45m'],
  ])('parses %s as %s', (input, expected) => {
    const result = parseTradingInterval(input);
    expect(result.ok && result.value).toBe(expected);
  });

  it.each(['', 'abc', '7x', '0m', '-5m', '1.5h', 'm7', '2000m', '400D'])('rejects %s', (input) => {
    const result = parseTradingInterval(input);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/\w/);
  });

  it('labels custom intervals the way the catalog labels its own', () => {
    expect(intervalMenuLabel('7m')).toBe('7 minutes');
    expect(intervalMenuLabel('1h')).toBe('1 hour');
    expect(intervalMenuLabel('3mo')).toBe('3 months');
    expect(intervalCompactLabel('7m')).toBe('7m');
    expect(intervalCompactLabel('3h')).toBe('3H');
    expect(intervalCompactLabel('2d')).toBe('2D');
    expect(intervalCompactLabel('3mo')).toBe('3M');
    expect(intervalCompactLabel('500t')).toBe('500T');
    expect(intervalCompactLabel('10r')).toBe('10R');
  });

  it('serves whole multiples of a feed interval through server aggregation and explains the rest', () => {
    const binance = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1mo'];
    expect(intervalAvailability('7m', binance)).toEqual({ available: true, baseInterval: '1m', reason: null });
    expect(intervalAvailability('3h', binance).baseInterval).toBe('1h');
    expect(intervalAvailability('2d', binance).baseInterval).toBe('1d');
    expect(intervalAvailability('3mo', binance).baseInterval).toBe('1mo');
    const seconds = intervalAvailability('30s', binance, 'Binance');
    expect(seconds.available).toBe(false);
    expect(seconds.reason).toBe('Seconds need trade-level data, which Binance does not provide.');
    expect(intervalAvailability('500t', binance).available).toBe(false);
    expect(intervalAvailability('10r', binance).reason).toMatch(/^Ranges need trade-level data/);
    // Yahoo serves 1d but not 1h multiples finer than its own bars.
    expect(intervalAvailability('90m', ['1d']).reason).toBe('90 minutes is not a whole multiple of an interval the selected feed serves.');
  });

  it('measures durations and orders favourites by length', () => {
    expect(tradingIntervalDurationMs('30s')).toBe(30_000);
    expect(tradingIntervalDurationMs('7m')).toBe(420_000);
    expect(tradingIntervalDurationMs('2d')).toBe(172_800_000);
    expect(tradingIntervalDurationMs('100t')).toBeNull();
    expect(isIntradayInterval('4h')).toBe(true);
    expect(isIntradayInterval('1d')).toBe(false);
    expect(sortTradingIntervals(['1d', '10r', '7m', '1h', '500t', '7m'])).toEqual(['500t', '7m', '1h', '1d', '10r']);
    expect(normalizeFavoriteIntervals(['4h', 'bogus', 7, '7m'])).toEqual(['7m', '4h']);
    expect(normalizeFavoriteIntervals('4h')).toBeNull();
  });
});
