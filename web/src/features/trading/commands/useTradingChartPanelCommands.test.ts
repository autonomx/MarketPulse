import { act, fireEvent, renderHook } from '@testing-library/react';
import { PriceScaleMode } from 'lightweight-charts';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { defaultTradingPriceScaleMenuState, type TradingPriceScaleMenuState } from '../TradingPriceScaleMenu';
import { useTradingChartPanelCommands } from './useTradingChartPanelCommands';
import { useTradingCommandDispatcher } from './useTradingCommands';

const download = vi.hoisted(() => ({ downloadUrl: vi.fn() }));
vi.mock('../../../shared/download', () => download);

function fakeAdapter() {
  let range = { from: 100, to: 140 };
  const timeScale = {
    getVisibleLogicalRange: vi.fn(() => range),
    setVisibleLogicalRange: vi.fn((next: typeof range) => { range = next; }),
    width: vi.fn(() => 800),
  };
  const adapter = {
    api: () => ({ timeScale: () => timeScale }),
    zoomAtCoordinate: vi.fn(),
    fitContent: vi.fn(),
    setPriceScaleAutoScale: vi.fn(),
    setPriceScaleInvert: vi.fn(),
    setPriceScaleMode: vi.fn(),
    snapshotDataUrl: vi.fn(() => 'data:image/png;base64,AA'),
    projectDrawingPoint: vi.fn(() => ({ x: 640, y: 120 })),
  };
  return { adapter, timeScale, range: () => range };
}

function mount(active: boolean, replayMode = false) {
  const fake = fakeAdapter();
  const openGoToDate = vi.fn();
  const setAlertPlacement = vi.fn();
  const selectedRangeRef = { current: 30 as number | undefined | null };
  const setSelectedRangeLabel = vi.fn();
  const copySnapshotLink = vi.fn(async () => 'http://localhost/api/trading/snapshots/x.png');
  const hook = renderHook(() => {
    useTradingCommandDispatcher();
    const [priceScaleSettings, setPriceScaleSettings] = useState<TradingPriceScaleMenuState>(defaultTradingPriceScaleMenuState);
    useTradingChartPanelCommands({
      active,
      adapter: fake.adapter as unknown as TradingChartAdapter,
      chartId: 'chart-2',
      priceScaleSettings,
      setPriceScaleSettings,
      selectedRangeRef: selectedRangeRef as never,
      setSelectedRangeLabel,
      openGoToDate,
      replayMode,
      setAlertPlacement,
      latest: { start_time: '2026-10-08T14:00:00.000Z', close: '101.5' } as never,
      copySnapshotLink,
    });
    return priceScaleSettings;
  });
  return { ...fake, hook, openGoToDate, setAlertPlacement, selectedRangeRef, setSelectedRangeLabel, copySnapshotLink };
}

const press = (init: KeyboardEventInit) => act(() => { fireEvent.keyDown(document.body, init); });

afterEach(() => {
  download.downloadUrl.mockClear();
});

describe('chart panel shortcuts (TVP-2.1)', () => {
  it('moves the chart by one bar and further with Ctrl', () => {
    const chart = mount(true);
    press({ key: 'ArrowLeft' });
    expect(chart.range()).toEqual({ from: 99, to: 139 });
    press({ key: 'ArrowRight' });
    press({ key: 'ArrowRight' });
    expect(chart.range()).toEqual({ from: 101, to: 141 });
    press({ key: 'ArrowLeft', ctrlKey: true });
    expect(chart.range()).toEqual({ from: 91, to: 131 });
    press({ key: 'ArrowRight', ctrlKey: true });
    expect(chart.range()).toEqual({ from: 101, to: 141 });
    chart.hook.unmount();
  });

  it('zooms around the right edge with Ctrl+↑/↓', () => {
    const chart = mount(true);
    press({ key: 'ArrowUp', ctrlKey: true });
    press({ key: 'ArrowDown', ctrlKey: true });
    expect(chart.adapter.zoomAtCoordinate.mock.calls).toEqual([[799, -100], [799, 100]]);
    chart.hook.unmount();
  });

  it('resets the view and toggles the price scale with Alt keys', () => {
    const chart = mount(true);
    press({ key: 'r', code: 'KeyR', altKey: true });
    expect(chart.adapter.fitContent).toHaveBeenCalled();
    expect(chart.selectedRangeRef.current).toBeUndefined();
    expect(chart.setSelectedRangeLabel).toHaveBeenCalledWith('All');

    press({ key: 'i', code: 'KeyI', altKey: true });
    expect(chart.adapter.setPriceScaleInvert).toHaveBeenLastCalledWith(true);
    expect(chart.hook.result.current.invertScale).toBe(true);

    press({ key: 'l', code: 'KeyL', altKey: true });
    expect(chart.adapter.setPriceScaleMode).toHaveBeenLastCalledWith(PriceScaleMode.Logarithmic);
    press({ key: 'p', code: 'KeyP', altKey: true });
    expect(chart.adapter.setPriceScaleMode).toHaveBeenLastCalledWith(PriceScaleMode.Percentage);
    expect(chart.hook.result.current.mode).toBe('percentage');
    press({ key: 'p', code: 'KeyP', altKey: true });
    expect(chart.hook.result.current.mode).toBe('normal');

    // Alt+S copies a snapshot link (TVP-2.1); Ctrl+Alt+S saves the image.
    press({ key: 's', code: 'KeyS', altKey: true });
    expect(chart.copySnapshotLink).toHaveBeenCalledTimes(1);
    expect(download.downloadUrl).not.toHaveBeenCalled();
    press({ key: 's', code: 'KeyS', altKey: true, ctrlKey: true });
    expect(download.downloadUrl).toHaveBeenCalledWith('data:image/png;base64,AA', 'chart-2.png');
    chart.hook.unmount();
  });

  it('opens go to date with Alt+G, except during replay (TVP-2.5)', () => {
    const chart = mount(true);
    press({ key: 'g', code: 'KeyG', altKey: true });
    expect(chart.openGoToDate).toHaveBeenCalledTimes(1);
    chart.hook.unmount();
    const replaying = mount(true, true);
    press({ key: 'g', code: 'KeyG', altKey: true });
    expect(replaying.openGoToDate).not.toHaveBeenCalled();
    replaying.hook.unmount();
  });

  it('opens the alert dialog at the last price with Alt+A', () => {
    const chart = mount(true);
    press({ key: 'a', code: 'KeyA', altKey: true });
    expect(chart.setAlertPlacement).toHaveBeenCalledWith(expect.objectContaining({ time: '2026-10-08T14:00:00.000Z', price: 101.5, source: 'context-menu' }));
    chart.hook.unmount();
  });

  it('adds no live alert at a replay price', () => {
    const chart = mount(true, true);
    press({ key: 'a', code: 'KeyA', altKey: true });
    expect(chart.setAlertPlacement).not.toHaveBeenCalled();
    chart.hook.unmount();
  });

  it('leaves inactive charts alone', () => {
    const chart = mount(false);
    press({ key: 'ArrowLeft' });
    press({ key: 'r', code: 'KeyR', altKey: true });
    press({ key: 's', code: 'KeyS', altKey: true });
    press({ key: 'g', code: 'KeyG', altKey: true });
    expect(chart.timeScale.setVisibleLogicalRange).not.toHaveBeenCalled();
    expect(chart.adapter.fitContent).not.toHaveBeenCalled();
    expect(download.downloadUrl).not.toHaveBeenCalled();
    expect(chart.openGoToDate).not.toHaveBeenCalled();
    chart.hook.unmount();
  });
});
