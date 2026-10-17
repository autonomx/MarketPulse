import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TradingDrawing } from './drawings/drawingCommands';
import type { DrawingAlertLevel } from './drawings/tools/types';
import type { TradingAlert } from './tradingTypes';

// levels: what this chart offers; keys: the levels the drawing has at all.
const state = vi.hoisted(() => ({ alerts: [] as unknown[], levels: ['upper', 'lower'], keys: ['upper', 'lower'], bars: 3 }));
const api = vi.hoisted(() => ({ updateAlert: vi.fn() }));
const mutations = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn(async () => undefined) }));
vi.mock('./useTradingAlerts', () => ({ useTradingAlerts: () => ({ data: state.alerts }), useTradingAlertMutations: () => ({ ...mutations }) }));
vi.mock('./tradingApi', () => ({ tradingApi: api }));
vi.mock('./drawings/TradingDrawingOverlay', () => ({
  drawingAlertLevelKeys: () => new Set(state.keys),
  drawingMenuEntries: (drawing: TradingDrawing) => ({
    drawingAlertLevels: [
      { key: 'upper', label: 'Upper', anchors: [drawing.points[0], drawing.points[1]], extend: 'right', interpolation: 'bars' },
      { key: 'lower', label: 'Lower', anchors: [{ ...drawing.points[0], price: drawing.points[0].price - 5 }, { ...drawing.points[1], price: drawing.points[1].price - 5 }], extend: 'right', interpolation: 'bars' },
    ].filter((level) => state.levels.includes(level.key)) as unknown as DrawingAlertLevel[],
  }),
}));
vi.mock('./drawings/drawingFrame', () => ({ chartAccessFor: () => ({}) }));

import { DRAWING_ALERT_SETTLE_MS, DrawingAlertSync, drawingAlertUpdates, sameAlertLine } from './DrawingAlertSync';

const T0 = '2026-10-09T14:00:00.000Z';
const T1 = '2026-10-09T15:00:00.000Z';
const channel = (price: number): TradingDrawing => ({
  drawingId: 'ch', instrumentId: 'btc', toolType: 'parallel-channel', points: [{ time: T0, price }, { time: T1, price: price + 10 }], selected: false, revision: 1,
}) as TradingDrawing;
const alert = (level: string, points: Array<{ time: string; price: string }>) => ({
  alert_id: `a-${level}`, instrument_id: 'btc', condition_type: 'trendline_crossing', threshold: '0', enabled: true, revision: 1,
  parameters: { drawing_id: 'ch', drawing_level: level, trendline_points: points }, evaluation_policy: {},
}) as unknown as TradingAlert;
const adapter = { drawingBarIndexMatchesBars: () => true, drawingBars: () => ({ length: state.bars }) } as never;
type Props = Partial<Parameters<typeof DrawingAlertSync>[0]>;
// A fresh instrument object each render, as the chart passes it: the settle timer must still fire.
const sync = (props: Props) => <DrawingAlertSync adapter={adapter} instrumentId="btc" instrument={{ tickSize: null, pointValue: 1 }} drawings={[]} active replayMode={false} {...props} />;

beforeEach(() => {
  state.levels = ['upper', 'lower'];
  state.keys = ['upper', 'lower'];
  state.bars = 3;
  api.updateAlert.mockImplementation(async (item: TradingAlert) => item);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('drawing alerts follow their drawing (TVP-1.4)', () => {
  it('compares lines as the server evaluates them, not by their anchors', () => {
    // A flat level placed by a 1h chart and by a 5m chart: different second anchors, the same line.
    expect(sameAlertLine([{ time: T0, price: 100 }, { time: T1, price: 100 }], [{ time: T0, price: 100 }, { time: '2026-10-09T14:05:00.000Z', price: 100 }])).toBe(true);
    expect(sameAlertLine([{ time: T0, price: 100 }, { time: T1, price: 110 }], [{ time: T0, price: 100 }, { time: T1, price: 111 }])).toBe(false);
    const levels = (drawing: TradingDrawing): DrawingAlertLevel[] => [{ key: 'upper', label: 'Upper', anchors: [drawing.points[0], drawing.points[1]], extend: 'right', interpolation: 'bars' }];
    const still = alert('upper', [{ time: T0, price: '100' }, { time: T1, price: '110' }]);
    expect(drawingAlertUpdates([still], [channel(100)], levels).moved).toEqual([]);
    expect(drawingAlertUpdates([still], [channel(102)], levels).moved[0].points).toEqual([{ time: T0, price: 102 }, { time: T1, price: 112 }]);
    expect(drawingAlertUpdates([alert('middle', [])], [channel(102)], levels).lost).toHaveLength(1);
    expect(drawingAlertUpdates([still], [], levels)).toEqual({ moved: [], lost: [] });
  });

  it('moves the alert line once the drawing settles, on the active chart only', async () => {
    vi.useFakeTimers();
    state.alerts = [alert('lower', [{ time: T0, price: '95' }, { time: T1, price: '105' }])];
    const view = render(sync({ drawings: [channel(100)] }));
    view.rerender(sync({ drawings: [channel(120)] }));
    view.rerender(sync({ drawings: [channel(120)] }));
    expect(api.updateAlert).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(api.updateAlert).toHaveBeenCalledTimes(1);
    expect(api.updateAlert.mock.calls[0][1].parameters.trendline_points).toEqual([{ time: T0, price: '115' }, { time: T1, price: '125' }]);
    api.updateAlert.mockClear();
    view.rerender(sync({ drawings: [channel(140)], active: false }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS * 2); });
    view.rerender(sync({ drawings: [channel(150)], replayMode: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS * 2); });
    expect(api.updateAlert).not.toHaveBeenCalled();
  });

  it('offers to disable the alerts of a drawing deleted here, and forgets them when it comes back', async () => {
    state.alerts = [alert('upper', [{ time: T0, price: '100' }, { time: T1, price: '110' }])];
    const view = render(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
    view.rerender(sync({ adapter: null, drawings: [channel(100)] }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    view.rerender(sync({ adapter: null, drawings: [] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(api.updateAlert).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 'a-upper' }), expect.objectContaining({ enabled: false })));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  });

  it('keeps the prompt when disabling fails', async () => {
    api.updateAlert.mockRejectedValue(new Error('Trading request failed (409)'));
    state.alerts = [alert('upper', [])];
    const view = render(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('still armed');
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('offers the alerts of a level the drawing no longer has', async () => {
    vi.useFakeTimers();
    state.alerts = [alert('lower', [{ time: T0, price: '95' }, { time: T1, price: '105' }])];
    state.levels = ['upper'];
    state.keys = ['upper'];
    render(sync({ drawings: [channel(100)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(screen.getByRole('alertdialog')).toHaveTextContent('drawing or level that is gone');
  });

  it('leaves alone a level the drawing has but this chart cannot offer, and does not ask again once kept', async () => {
    vi.useFakeTimers();
    state.alerts = [{ ...alert('lower', [{ time: T0, price: '95' }, { time: T1, price: '105' }]), alert_id: 'a-kept' }];
    state.levels = ['upper'];
    const view = render(sync({ drawings: [channel(100)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(api.updateAlert).not.toHaveBeenCalled();
    state.keys = ['upper'];
    view.rerender(sync({ drawings: [channel(101)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    fireEvent.click(screen.getByRole('button', { name: 'Keep' }));
    view.rerender(sync({ drawings: [channel(102)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('decides nothing before the chart has bars, and catches up when they arrive', async () => {
    vi.useFakeTimers();
    state.alerts = [alert('lower', [{ time: T0, price: '95' }, { time: T1, price: '105' }])];
    state.bars = 0;
    // Without bars no level can be placed: the tool offers none.
    state.levels = [];
    state.keys = [];
    render(sync({ drawings: [channel(120)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS * 3); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(api.updateAlert).not.toHaveBeenCalled();
    state.bars = 3;
    state.levels = ['upper', 'lower'];
    state.keys = ['upper', 'lower'];
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(api.updateAlert).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('does not ask on another chart about an alert kept or disabled meanwhile', async () => {
    const kept = { ...alert('upper', []), alert_id: 'a-kept-elsewhere' };
    state.alerts = [kept];
    const view = render(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Keep' }));
    view.rerender(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    state.alerts = [{ ...alert('lower', []), enabled: false }];
    view.rerender(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('asks on the active chart only', async () => {
    state.alerts = [alert('upper', [])];
    const view = render(sync({ adapter: null, drawings: [channel(100)], active: false }));
    view.rerender(sync({ adapter: null, drawings: [], active: false }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    view.rerender(sync({ adapter: null, drawings: [], active: true }));
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
  });

  it('disables the alerts as they are now, and keeps asking only about the ones that failed', async () => {
    const upper = alert('upper', []);
    const lower = alert('lower', []);
    const other = { ...alert('other', []), parameters: { ...lower.parameters, drawing_level: 'upper' } } as TradingAlert;
    state.alerts = [upper, lower, other];
    const view = render(sync({ adapter: null, drawings: [channel(100)] }));
    view.rerender(sync({ adapter: null, drawings: [] }));
    await screen.findByRole('alertdialog');
    // Disabled elsewhere meanwhile: nothing to do for it.
    state.alerts = [upper, { ...lower, enabled: false }, other];
    view.rerender(sync({ adapter: null, drawings: [] }));
    api.updateAlert.mockImplementation(async (item: TradingAlert) => {
      if (item.alert_id === 'a-other') throw new Error('Trading request failed (500)');
      return { ...item, enabled: false };
    });
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('still armed');
    expect(api.updateAlert.mock.calls.map((call) => (call[0] as TradingAlert).alert_id).sort()).toEqual(['a-other', 'a-upper']);
    expect(mutations.replace).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 'a-upper', enabled: false }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('An alert follows');
  });
});

describe('drawing alerts follow their drawing (TVP-1.4)', () => {
  it('tries a failing move again once the drawing moves again', async () => {
    vi.useFakeTimers();
    api.updateAlert.mockRejectedValue(new Error('Trading request failed (500)'));
    state.alerts = [alert('upper', [{ time: T0, price: '100' }, { time: T1, price: '110' }])];
    const view = render(sync({ drawings: [channel(100)] }));
    for (const price of [120, 120, 120]) {
      view.rerender(sync({ drawings: [channel(price)] }));
      await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    }
    const tries = api.updateAlert.mock.calls.length;
    expect(tries).toBeLessThanOrEqual(2);
    view.rerender(sync({ drawings: [channel(130)] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(DRAWING_ALERT_SETTLE_MS); });
    expect(api.updateAlert.mock.calls.length).toBe(tries + 1);
  });

  it('never prompts for a drawing it never saw, such as one on another chart', () => {
    state.alerts = [alert('upper', [])];
    render(sync({ adapter: null, drawings: [] }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});
