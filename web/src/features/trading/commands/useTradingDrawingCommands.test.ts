import { act, fireEvent, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { clearDrawingClipboard, drawingClipboard } from '../drawings/drawingClipboard';
import type { TradingDrawing } from '../drawings/drawingCommands';
import { useTradingCommandDispatcher } from './useTradingCommands';
import { useTradingDrawingCommands } from './useTradingDrawingCommands';

const at = (minute: number, price: number) => ({ time: new Date(Date.UTC(2026, 9, 8, 14, minute)).toISOString(), price });

function trend(id: string, locked = false): TradingDrawing {
  return { drawingId: id, instrumentId: 'crypto:BTC', toolType: 'trend-line', points: [at(10, 100), at(20, 110)], selected: true, revision: 1, locked };
}

/** A chart where one bar is one minute and one pixel is 0.5 in price (y = 1000 - 2 * price). */
function fakeAdapter() {
  return {
    drawingTimeAfterBars: (time: string, count: number) => new Date(Date.parse(time) + count * 60_000).toISOString(),
    projectDrawingPoint: (point: { time: string; price: number }) => ({ x: 0, y: 1000 - 2 * point.price }),
    drawingPointFromCoordinate: (_x: number, y: number) => ({ time: at(0, 0).time, price: (1000 - y) / 2 }),
  } as unknown as TradingChartAdapter;
}

function mount(selected: TradingDrawing[] = [], lockAll = false) {
  const drawings = { selected: vi.fn(() => selected), translate: vi.fn(), paste: vi.fn() };
  const setDrawingTool = vi.fn();
  const toggleDrawingsHidden = vi.fn();
  const hook = renderHook(() => {
    useTradingCommandDispatcher();
    useTradingDrawingCommands({ active: true, adapter: fakeAdapter(), drawings, setDrawingTool, toggleDrawingsHidden, drawingToolSettings: { lockAll } });
  });
  return { hook, drawings, setDrawingTool, toggleDrawingsHidden };
}

const press = (init: KeyboardEventInit) => act(() => { fireEvent.keyDown(document.body, init); });

afterEach(() => clearDrawingClipboard());

describe('drawing commands (TVP-2.2)', () => {
  it('selects tools with TradingView\'s Alt keys', () => {
    const chart = mount();
    press({ key: 't', code: 'KeyT', altKey: true });
    press({ key: 'h', code: 'KeyH', altKey: true });
    press({ key: 'v', code: 'KeyV', altKey: true });
    press({ key: 'c', code: 'KeyC', altKey: true });
    press({ key: 'f', code: 'KeyF', altKey: true });
    press({ key: 'R', code: 'KeyR', altKey: true, shiftKey: true });
    expect(chart.setDrawingTool.mock.calls.map(([tool]) => tool)).toEqual(['trend-line', 'horizontal-line', 'vertical-line', 'crossline', 'fibonacci', 'rectangle']);
    chart.hook.unmount();
  });

  it('copies the selection and pastes it', () => {
    const source = mount([trend('a')]);
    press({ key: 'v', code: 'KeyV', ctrlKey: true });
    expect(source.drawings.paste).not.toHaveBeenCalled();
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(drawingClipboard().map((drawing) => drawing.drawingId)).toEqual(['a']);
    source.hook.unmount();
    // Another chart (or symbol) pastes what was copied.
    const target = mount();
    press({ key: 'v', code: 'KeyV', ctrlKey: true });
    expect(target.drawings.paste).toHaveBeenCalledWith([expect.objectContaining({ drawingId: 'a' })]);
    target.hook.unmount();
  });

  it('nudges the selection a bar sideways and a pixel up or down', () => {
    const chart = mount([trend('a')]);
    press({ key: 'ArrowRight' });
    expect(chart.drawings.translate).toHaveBeenLastCalledWith('a', at(10, 100), at(11, 100));
    press({ key: 'ArrowLeft' });
    expect(chart.drawings.translate).toHaveBeenLastCalledWith('a', at(10, 100), at(9, 100));
    press({ key: 'ArrowUp' });
    expect(chart.drawings.translate).toHaveBeenLastCalledWith('a', at(10, 100), at(10, 100.5));
    press({ key: 'ArrowDown' });
    expect(chart.drawings.translate).toHaveBeenLastCalledWith('a', at(10, 100), at(10, 99.5));
    chart.hook.unmount();
  });

  it('leaves the arrow keys to the chart when nothing movable is selected', () => {
    const chart = mount([trend('locked', true)]);
    press({ key: 'ArrowRight' });
    expect(chart.drawings.translate).not.toHaveBeenCalled();
    chart.hook.unmount();
  });

  it('lock all stops the arrow-key nudge too (TVP-3.8)', () => {
    const chart = mount([trend('a')], true);
    press({ key: 'ArrowRight' });
    expect(chart.drawings.translate).not.toHaveBeenCalled();
    chart.hook.unmount();
  });

  it('hides and shows all drawings with Ctrl+Alt+H', () => {
    const chart = mount();
    press({ key: 'h', code: 'KeyH', ctrlKey: true, altKey: true });
    expect(chart.toggleDrawingsHidden).toHaveBeenCalledTimes(1);
    chart.hook.unmount();
  });
});
