import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { tradingApi } from '../tradingApi';
import type { TradingDocument } from '../tradingTypes';
import { tradingDrawingRecordId, useTradingDrawings } from './useTradingDrawings';
import { currentTradingWorkspaceScopeId } from '../persistence/useTradingWorkspacePersistence';

const point = (minute: number, price: number) => ({ time: new Date(Date.UTC(2026, 9, 7, 0, minute)).toISOString(), price });

function storedDocument(instrumentId: string, tab: string, payload: Record<string, unknown>): TradingDocument {
  return {
    record_id: tradingDrawingRecordId(instrumentId, `${currentTradingWorkspaceScopeId()}:${tab}`),
    record_type: 'drawing',
    revision: 3,
    payload,
    status: 'active',
    updated_at: null,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('drawing persistence and document versions', () => {
  it('never saves over a document from a newer schema', async () => {
    const instrumentId = 'equity:TEST:NEWER';
    const drawing = { drawingId: 'a', instrumentId, toolType: 'trend-line' as const, points: [point(0, 1), point(5, 2)], selected: false, revision: 1 };
    vi.spyOn(tradingApi, 'documents').mockResolvedValue([storedDocument(instrumentId, 'tab-newer', { schemaVersion: 3, instrumentId, drawings: [drawing] })]);
    const update = vi.spyOn(tradingApi, 'updateDocument');
    const { result } = renderHook(() => useTradingDrawings(instrumentId, 'tab-newer'));
    await waitFor(() => expect(result.current.status).toBe('read-only'));
    vi.useFakeTimers();
    act(() => result.current.add({ ...drawing, drawingId: 'b' }));
    act(() => vi.advanceTimersByTime(1_000));
    expect(result.current.state.drawings).toHaveLength(2);
    expect(result.current.status).toBe('read-only');
    expect(update).not.toHaveBeenCalled();
  });

  it('saves unreadable drawings back unchanged and counts them', async () => {
    const instrumentId = 'equity:TEST:KEEP';
    const unreadable = { drawingId: 'bad', instrumentId, toolType: 'trend-line', points: [{ time: 'never', price: 1 }], selected: false, revision: 1 };
    const readable = { drawingId: 'ok', instrumentId, toolType: 'trend-line', points: [point(0, 1), point(5, 2)], selected: false, revision: 1 };
    const record = storedDocument(instrumentId, 'tab-keep', { schemaVersion: 2, instrumentId, drawings: [readable, unreadable] });
    vi.spyOn(tradingApi, 'documents').mockResolvedValue([record]);
    const update = vi.spyOn(tradingApi, 'updateDocument').mockResolvedValue({ ...record, revision: 4 });
    const { result } = renderHook(() => useTradingDrawings(instrumentId, 'tab-keep'));
    await waitFor(() => expect(result.current.status).toBe('saved'));
    expect(result.current.preservedCount).toBe(1);
    vi.useFakeTimers();
    act(() => result.current.remove('ok'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(update).toHaveBeenCalledTimes(1);
    expect(update.mock.calls[0][2]).toEqual({ schemaVersion: 2, instrumentId, drawings: [unreadable] });
  });

  it('fetches the stored document before a first save when the load failed, and keeps its entries', async () => {
    const instrumentId = 'equity:TEST:RETRY';
    const kept = { drawingId: 'kept', instrumentId: 'equity:OTHER', toolType: 'trend-line', points: [], selected: false, revision: 1 };
    const record = storedDocument(instrumentId, 'tab-retry', { schemaVersion: 2, instrumentId, drawings: [kept] });
    const documents = vi.spyOn(tradingApi, 'documents').mockRejectedValueOnce(new Error('offline')).mockResolvedValue([record]);
    const update = vi.spyOn(tradingApi, 'updateDocument').mockResolvedValue({ ...record, revision: 4 });
    const create = vi.spyOn(tradingApi, 'createDocument');
    const { result } = renderHook(() => useTradingDrawings(instrumentId, 'tab-retry'));
    await waitFor(() => expect(result.current.status).toBe('error'));
    vi.useFakeTimers();
    act(() => result.current.add({ drawingId: 'new', instrumentId, toolType: 'trend-line', points: [point(0, 1), point(5, 2)], selected: false, revision: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(documents).toHaveBeenCalledTimes(2);
    expect(create).not.toHaveBeenCalled();
    expect(update.mock.calls[0][1]).toBe(record);
    expect((update.mock.calls[0][2] as { drawings: unknown[] }).drawings).toContainEqual(kept);
  });

  it("overwrites after a conflict with the server copy's kept entries included", async () => {
    const instrumentId = 'equity:TEST:CONFLICT';
    const record = storedDocument(instrumentId, 'tab-conflict', { schemaVersion: 2, instrumentId, drawings: [] });
    const serverKept = { drawingId: 'server-kept', instrumentId, toolType: 'trend-line', points: [{ time: 'bad', price: 1 }], selected: false, revision: 1 };
    const latest = { ...record, revision: 5, payload: { schemaVersion: 2, instrumentId, drawings: [serverKept] } };
    vi.spyOn(tradingApi, 'documents').mockResolvedValueOnce([record]).mockResolvedValue([latest]);
    const update = vi.spyOn(tradingApi, 'updateDocument')
      .mockRejectedValueOnce(new Error('Request failed (409)'))
      .mockResolvedValue({ ...latest, revision: 6 });
    const { result } = renderHook(() => useTradingDrawings(instrumentId, 'tab-conflict'));
    await waitFor(() => expect(result.current.status).toBe('saved'));
    vi.useFakeTimers();
    act(() => result.current.add({ drawingId: 'mine', instrumentId, toolType: 'trend-line', points: [point(0, 1), point(5, 2)], selected: false, revision: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.status).toBe('conflict');
    await act(async () => {
      await result.current.resolveConflict('overwrite');
    });
    const saved = update.mock.calls[1][2] as { drawings: { drawingId: string }[] };
    expect(saved.drawings.map((item) => item.drawingId)).toEqual(['mine', 'server-kept']);
  });
});
