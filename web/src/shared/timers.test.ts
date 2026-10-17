import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startPolling, startTicker } from './timers';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('startPolling', () => {
  it('waits for a slow run to finish before scheduling the next one', async () => {
    let finish: () => void = () => undefined;
    const task = vi.fn(() => new Promise<void>((resolve) => {
      finish = resolve;
    }));
    const stop = startPolling(task, 1_000);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(task).toHaveBeenCalledTimes(1);

    finish();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(task).toHaveBeenCalledTimes(2);
    stop();
  });

  it('skips its turns while the page is hidden and keeps going after a failure', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const task = vi.fn(async () => {
      throw new Error('gateway down');
    });
    const stop = startPolling(task, 1_000);

    await vi.advanceTimersByTimeAsync(3_000);
    expect(task).not.toHaveBeenCalled();

    visibility.mockReturnValue('visible');
    await vi.advanceTimersByTimeAsync(2_000);
    expect(task).toHaveBeenCalledTimes(2);
    stop();
  });

  it('stops scheduling when stopped', async () => {
    const task = vi.fn();
    const stop = startPolling(task, 1_000);
    stop();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(task).not.toHaveBeenCalled();
  });
});

describe('startTicker', () => {
  it('ticks at a fixed rate until stopped', () => {
    const tick = vi.fn();
    const stop = startTicker(tick, 100);
    vi.advanceTimersByTime(350);
    expect(tick).toHaveBeenCalledTimes(3);
    stop();
    vi.advanceTimersByTime(1_000);
    expect(tick).toHaveBeenCalledTimes(3);
  });
});
