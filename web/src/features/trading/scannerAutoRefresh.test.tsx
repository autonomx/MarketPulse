import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ definitions: vi.fn(), runs: vi.fn(), results: vi.fn(), start: vi.fn(), create: vi.fn(), update: vi.fn(), cancel: vi.fn() }));
vi.mock('./tradingScannerApi', () => ({ tradingScannerApi: api }));
vi.mock('./useTradingAlerts', () => ({ useAlertIndicatorIds: () => new Set(['rsi']) }));

import { resultChanges, useScannerAutoRefresh } from './scannerAutoRefresh';
import type { TradingScannerResult } from './scannerTypes';
import { TradingScannerPanel } from './TradingScannerPanel';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

const result = (instrumentId: string, runId = 'run-1') => ({ run_id: runId, instrument_id: instrumentId, rank: 1, provider: 'p', score: '1', dataset_fingerprint: 'abcdef1234' }) as unknown as TradingScannerResult;

describe('screener auto-refresh (TVP-9.2)', () => {
  it('says what changed since the previous run', () => {
    expect(resultChanges(null, [result('a')])).toEqual({ added: new Set(), removed: [] });
    const changes = resultChanges([result('a'), result('b')], [result('b'), result('c')]);
    expect([...changes.added]).toEqual(['c']);
    expect(changes.removed).toEqual(['a']);
  });

  it('starts a run on its cadence, never while one is still working', async () => {
    vi.useFakeTimers();
    let busy = false;
    const start = vi.fn(async () => undefined);
    const hook = renderHook(() => useScannerAutoRefresh({ scannerId: 's', everyMs: 10_000 }, () => busy, start));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(start).toHaveBeenCalledTimes(1);
    busy = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(start).toHaveBeenCalledTimes(1);
    hook.unmount();
    busy = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(start).toHaveBeenCalledTimes(1);
  });

  it('treats a refused second run as busy, and gives up after repeated failures', async () => {
    vi.useFakeTimers();
    const onGiveUp = vi.fn();
    const start = vi.fn(async () => { throw new Error('Scanner request failed (409): scanner_run_active'); });
    const hook = renderHook(() => useScannerAutoRefresh({ scannerId: 's', everyMs: 10_000 }, () => false, start, onGiveUp));
    await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });
    expect(onGiveUp).not.toHaveBeenCalled();
    start.mockImplementation(async () => { throw new Error('Scanner request failed (404): scanner_not_found'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(onGiveUp).toHaveBeenCalledTimes(1);
    hook.unmount();
  });

  it('turns auto-refresh on from a saved screen and highlights new results', async () => {
    api.definitions.mockResolvedValue([{ scanner_id: 's1', name: 'Movers', instrument_ids: ['a'], interval: '1d', revision: 1 }]);
    api.runs.mockResolvedValue([{ run_id: 'run-1', scanner_id: 's1', status: 'completed', completed_count: 2, universe_count: 2, matched_count: 2 }]);
    api.results.mockResolvedValueOnce([result('a'), result('b')]);
    api.start.mockResolvedValue({});
    render(<TradingScannerPanel instruments={[]} />);
    const auto = await screen.findByLabelText('Auto-refresh Movers');
    api.runs.mockResolvedValue([{ run_id: 'run-2', scanner_id: 's1', status: 'completed', completed_count: 2, universe_count: 2, matched_count: 2 }]);
    api.results.mockResolvedValueOnce([result('b', 'run-2'), result('c', 'run-2')]);
    fireEvent.change(auto, { target: { value: '10000' } });
    await waitFor(() => expect(api.start).toHaveBeenCalledWith('s1'));
    expect(await screen.findByRole('status')).toHaveTextContent('1 new, 1 dropped (a)');
    expect(screen.getByText('c').closest('tr')).toHaveClass('is-new');
  });

  it('edits a saved screen and saves it at its revision (TVP-9.1)', async () => {
    const rules = [{ rule_id: 'up', metric: 'percent_change', operator: 'gte', threshold: '1', period: 14, lookback_bars: 5, role: 'filter', source: null }];
    const saved = { scanner_id: 's1', name: 'Movers', instrument_ids: ['crypto:X:spot:AAA-USD'], interval: '1h', revision: 4, rules };
    api.definitions.mockResolvedValue([saved]);
    api.runs.mockResolvedValue([]);
    api.update.mockResolvedValue({ ...saved, revision: 5 });
    api.start.mockResolvedValue({});
    render(<TradingScannerPanel instruments={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Movers' }));
    fireEvent.change(screen.getByLabelText('Value of up'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Movers and run' }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(expect.objectContaining({
      scanner_id: 's1', revision: 4, interval: '1h', instrument_ids: ['crypto:X:spot:AAA-USD'], rules: [expect.objectContaining({ rule_id: 'up', threshold: '3' })],
    })));
    expect(api.create).not.toHaveBeenCalled();
  });

  it('keeps the screen settings it does not edit, and shows a run with its own columns (TVP-9.1 review)', async () => {
    const rules = [{ rule_id: 'up', metric: 'percent_change', operator: 'gte', threshold: '1', period: 14, lookback_bars: 5, role: 'filter', source: null }];
    const saved = { scanner_id: 's1', name: 'Movers', instrument_ids: ['crypto:X:spot:AAA-USD'], interval: '1h', revision: 4, rules,
      binding_ids: { 'crypto:X:spot:AAA-USD': 'feed' }, history_limit: 300, max_concurrency: 2, request_timeout_seconds: 7, run_timeout_seconds: 90, enabled: false };
    api.definitions.mockResolvedValue([{ ...saved, rules: [{ ...rules[0], metric: 'rsi' }] }]);
    // The run on screen was made by the earlier version: change % columns, not RSI.
    api.runs.mockResolvedValue([{ run_id: 'run-1', scanner_id: 's1', status: 'completed', completed_count: 1, universe_count: 1, matched_count: 1, definition_snapshot: { rules } }]);
    api.results.mockResolvedValueOnce([result('crypto:X:spot:AAA-USD')]);
    api.update.mockResolvedValue({ ...saved, revision: 5 });
    api.start.mockResolvedValue({});
    render(<TradingScannerPanel instruments={[]} />);
    expect(await screen.findByRole('button', { name: 'change % 5' })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Movers' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Movers and run' }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(expect.objectContaining({
      binding_ids: { 'crypto:X:spot:AAA-USD': 'feed' }, history_limit: 300, max_concurrency: 2, request_timeout_seconds: 7, run_timeout_seconds: 90, enabled: false,
    })));
  });

  it('keeps the edits when the screen changed elsewhere, and saves over the new version', async () => {
    const rules = [{ rule_id: 'up', metric: 'percent_change', operator: 'gte', threshold: '1', period: 14, lookback_bars: 5, role: 'filter', source: null }];
    const saved = { scanner_id: 's1', name: 'Movers', instrument_ids: ['crypto:X:spot:AAA-USD'], interval: '1h', revision: 4, rules, history_limit: 100 };
    api.definitions.mockResolvedValue([saved]);
    api.runs.mockResolvedValue([]);
    api.update.mockRejectedValueOnce(new Error('Trading scanner request failed (409): revision'));
    render(<TradingScannerPanel instruments={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Movers' }));
    fireEvent.change(screen.getByLabelText('Value of up'), { target: { value: '7' } });
    api.definitions.mockResolvedValue([{ ...saved, revision: 9 }]);
    fireEvent.click(screen.getByRole('button', { name: 'Save Movers and run' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
    api.update.mockResolvedValue({ ...saved, revision: 10 });
    fireEvent.click(screen.getByRole('button', { name: 'Save Movers and run' }));
    await waitFor(() => expect(api.update).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 9, rules: [expect.objectContaining({ threshold: '7' })] })));
  });

  it('never compares results of different screens', async () => {
    api.definitions.mockResolvedValue([{ scanner_id: 's1', name: 'Movers', instrument_ids: ['a'], interval: '1d', revision: 1 }, { scanner_id: 's2', name: 'Gaps', instrument_ids: ['x'], interval: '1d', revision: 1 }]);
    api.runs.mockResolvedValue([{ run_id: 'run-1', scanner_id: 's1', status: 'completed', completed_count: 2, universe_count: 2, matched_count: 2 }]);
    api.results.mockResolvedValueOnce([result('a'), result('b')]);
    api.start.mockResolvedValue({});
    render(<TradingScannerPanel instruments={[]} />);
    await screen.findByLabelText('Auto-refresh Gaps');
    api.runs.mockResolvedValue([{ run_id: 'run-9', scanner_id: 's2', status: 'completed', completed_count: 1, universe_count: 1, matched_count: 1 }]);
    api.results.mockResolvedValueOnce([result('x', 'run-9')]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Run' })[1]);
    expect(await screen.findByText('x')).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByText('x').closest('tr')).not.toHaveClass('is-new');
  });
});
