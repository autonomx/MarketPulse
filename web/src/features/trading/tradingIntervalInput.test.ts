import { describe, expect, it } from 'vitest';
import { parseTradingIntervalInput, resolveTradingIntervalInput } from './tradingIntervalInput';

describe('interval box parsing (TVP-2.1, custom intervals TVP-2.5)', () => {
  it.each([
    ['5', '5m'],
    ['15', '15m'],
    ['60', '1h'],
    ['240', '4h'],
    ['1h', '1h'],
    ['1H', '1h'],
    ['1D', '1d'],
    ['1d', '1d'],
    ['1W', '1w'],
    ['1M', '1mo'],
    ['3mo', '3mo'],
    ['5s', '5s'],
    ['100t', '100t'],
    ['10R', '10r'],
    [' 30 ', '30m'],
    ['7', '7m'],
    ['3h', '3h'],
    ['90', '90m'],
    ['D', '1d'],
  ])('reads %s as %s', (text, interval) => {
    expect(parseTradingIntervalInput(text)).toBe(interval);
  });

  it.each(['', ',', 'abc', '0', '5x', '1.5h', '-5', '99999999999999999999', '5000m'])('rejects %s', (text) => {
    expect(parseTradingIntervalInput(text)).toBeNull();
  });

  it('rejects intervals the chart feed cannot show', () => {
    expect(resolveTradingIntervalInput('15', ['1m', '1h'])).toEqual({ ok: true, interval: '15m' });
    expect(resolveTradingIntervalInput('7', ['1m', '1h'])).toEqual({ ok: true, interval: '7m' });
    expect(resolveTradingIntervalInput('3h', ['1m', '1h'])).toEqual({ ok: true, interval: '3h' });
    expect(resolveTradingIntervalInput('5s', ['1m', '1h'])).toMatchObject({ ok: false });
    expect(resolveTradingIntervalInput('nope', ['1m'])).toMatchObject({ ok: false, error: expect.stringContaining('nope') });
    expect(resolveTradingIntervalInput('7', ['5m'])).toMatchObject({ ok: false, error: expect.stringContaining('multiple') });
  });
});
