import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const tradingApi = vi.hoisted(() => ({
  documents: vi.fn(async () => [{ record_id: 'wl-1', payload: { name: 'Tech' } }]),
  watchlistAlertCapacity: vi.fn(async () => ({ watchlist_id: 'wl-1', symbol_count: 20, provider_cap: 600, default_limit: 100 })),
}));
vi.mock('./tradingApi', () => ({ tradingApi }));

const { useAlertWatchlistTargets, watchlistCapacityNote, watchlistIdOf } = await import('./useAlertWatchlistTargets');

describe('watchlist alert targets (TVP-1.7)', () => {
  it('offers the symbol and every watchlist, and labels list alerts by name', async () => {
    const hook = renderHook(() => useAlertWatchlistTargets('equity:NASDAQ:AAPL', 'AAPL', 'watchlist:wl-1'));
    await waitFor(() => expect(hook.result.current.choices).toHaveLength(2));
    expect(hook.result.current.choices).toEqual([
      { value: 'equity:NASDAQ:AAPL', label: 'This symbol (AAPL)' },
      { value: 'watchlist:wl-1', label: 'Watchlist: Tech' },
    ]);
    expect(hook.result.current.labelFor('watchlist:wl-1')).toBe('List: Tech');
    expect(hook.result.current.labelFor('equity:NASDAQ:AAPL')).toBeNull();
    await waitFor(() => expect(hook.result.current.note).toBe("Runs on 20 of the list's 20 symbols, each firing on its own."));
  });

  it('reads the watchlist id and describes the cap', () => {
    expect(watchlistIdOf('watchlist:abc')).toBe('abc');
    expect(watchlistIdOf('watchlist:')).toBeNull();
    expect(watchlistIdOf('equity:X')).toBeNull();
    expect(watchlistCapacityNote({ symbol_count: 300, provider_cap: 1000, default_limit: 100 })).toBe("Runs on 100 of the list's 300 symbols, each firing on its own.");
  });
});
