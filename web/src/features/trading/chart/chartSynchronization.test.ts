import { describe, expect, it } from 'vitest';
import { TradingChartSynchronization, type SynchronizableChart } from './chartSynchronization';
import type { TradingCrosshairPoint, TradingVisibleRange } from './chartAdapter';

class FakeChart implements SynchronizableChart {
  crosshairListener: ((point: TradingCrosshairPoint | null) => void) | null = null;
  rangeListener: ((range: TradingVisibleRange | null) => void) | null = null;
  crosshairUpdates: Array<TradingCrosshairPoint | null> = [];
  rangeUpdates: TradingVisibleRange[] = [];

  onCrosshair(listener: (point: TradingCrosshairPoint | null) => void) {
    this.crosshairListener = listener;
    return () => { this.crosshairListener = null; };
  }

  setCrosshair(point: TradingCrosshairPoint | null) {
    this.crosshairUpdates.push(point);
    this.crosshairListener?.(point);
  }

  onVisibleRange(listener: (range: TradingVisibleRange | null) => void) {
    this.rangeListener = listener;
    return () => { this.rangeListener = null; };
  }

  setVisibleRange(range: TradingVisibleRange) {
    this.rangeUpdates.push(range);
    this.rangeListener?.(range);
  }

  emitCrosshair(point: TradingCrosshairPoint | null) { this.crosshairListener?.(point); }
  emitRange(range: TradingVisibleRange | null) { this.rangeListener?.(range); }
}

describe('Trading chart synchronization', () => {
  it('propagates timestamp crosshairs once without feedback loops', () => {
    const controller = new TradingChartSynchronization();
    const first = new FakeChart();
    const second = new FakeChart();
    controller.register('one', first);
    controller.register('two', second);
    const point = { time: 1_700_000_000 as never, price: 101 };
    first.emitCrosshair(point);
    expect(first.crosshairUpdates).toEqual([]);
    expect(second.crosshairUpdates).toEqual([point]);
    controller.dispose();
    expect(controller.registeredChartCount).toBe(0);
  });

  it('links ranges independently from crosshairs', () => {
    const controller = new TradingChartSynchronization();
    const first = new FakeChart();
    const second = new FakeChart();
    controller.register('one', first);
    controller.register('two', second);
    controller.setLinks({ crosshair: false, visibleRange: true });
    first.emitCrosshair({ time: 1 as never, price: 5 });
    const range = { from: 1 as never, to: 10 as never };
    first.emitRange(range);
    expect(second.crosshairUpdates).toEqual([]);
    expect(second.rangeUpdates).toEqual([range]);
  });

  it('keeps visible ranges independent by default', () => {
    const controller = new TradingChartSynchronization();
    const first = new FakeChart();
    const second = new FakeChart();
    controller.register('one', first);
    controller.register('two', second);

    first.emitRange({ from: 1 as never, to: 10 as never });

    expect(second.rangeUpdates).toEqual([]);
  });
});
