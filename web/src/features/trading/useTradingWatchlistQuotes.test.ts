import { describe, expect, it } from 'vitest';
import { runWithConcurrency } from './useTradingWatchlistQuotes';

describe('runWithConcurrency', () => {
  it('never runs more tasks at once than the limit and runs each item once', async () => {
    let running = 0;
    let peak = 0;
    const done: number[] = [];
    await runWithConcurrency(Array.from({ length: 10 }, (_, index) => index), 3, async (item) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, 1));
      done.push(item);
      running -= 1;
    });
    expect(peak).toBe(3);
    expect([...done].sort((left, right) => left - right)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it('starts no new task once stopped', async () => {
    const started: number[] = [];
    let stopped = false;
    await runWithConcurrency([1, 2, 3, 4], 1, async (item) => {
      started.push(item);
      if (item === 2) stopped = true;
    }, () => stopped);
    expect(started).toEqual([1, 2]);
  });
});
