import { afterEach, describe, expect, it, vi } from 'vitest';
import { dispatchDrawingActionRequest, hasDrawingActionHandler, onDrawingActionRequest } from './drawingActions';

describe('drawing action bus', () => {
  afterEach(() => vi.restoreAllMocks());

  it('keeps a newer subscriber when an old unsubscribe runs twice', () => {
    const stopFirst = onDrawingActionRequest('order-ticket', vi.fn());
    stopFirst();
    const second = vi.fn();
    const stopSecond = onDrawingActionRequest('order-ticket', second);
    stopFirst();
    expect(hasDrawingActionHandler('order-ticket')).toBe(true);
    expect(dispatchDrawingActionRequest({ type: 'order-ticket', payload: 1 })).toBe(true);
    expect(second).toHaveBeenCalledWith(1, { chartId: null, drawingId: null });
    stopSecond();
    expect(hasDrawingActionHandler('order-ticket')).toBe(false);
  });

  it('runs every handler even when one throws', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const stopBroken = onDrawingActionRequest('measure', () => { throw new Error('broken'); });
    const working = vi.fn();
    const stopWorking = onDrawingActionRequest('measure', working);
    expect(dispatchDrawingActionRequest({ type: 'measure', payload: null }, { chartId: 'chart-1', drawingId: 'd1' })).toBe(true);
    expect(working).toHaveBeenCalledWith(null, { chartId: 'chart-1', drawingId: 'd1' });
    expect(error).toHaveBeenCalledTimes(1);
    stopBroken();
    stopWorking();
  });
});
