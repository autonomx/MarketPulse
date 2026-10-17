import { describe, expect, it } from 'vitest';
import { indicatorLines } from './indicatorLineChoices';

describe('indicator lines for the screener and watchlist columns', () => {
  it('names Volume Delta and CVD by the line the chart and the server key (TVP-6.4)', () => {
    expect(indicatorLines('tv-volume-delta', 14)).toEqual([{ key: 'tv-volume-delta:delta', title: 'Volume Delta' }]);
    expect(indicatorLines('tv-cumulative-volume-delta', 14)).toEqual([{ key: 'tv-cumulative-volume-delta:cvd', title: 'CVD' }]);
    expect(indicatorLines('sma', 20).map((line) => line.key)).toEqual(['sma:20']);
  });
});
