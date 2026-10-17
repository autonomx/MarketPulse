// Flags from the chart and the screener (TVP-5.1): the same flags as the watchlist's, through one store, so a flag set
// anywhere shows everywhere.
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScreenerResultsTable } from './ScreenerResultsTable';
import { TradingChartContextMenu } from './TradingChartContextMenu';
import { tradingApi } from './tradingApi';
import type { TradingScannerResult } from './scannerTypes';
import type { TradingDocument } from './tradingTypes';
import { resetTradingWatchlistFlags, useTradingWatchlistFlags } from './useTradingWatchlistFlags';

const AAPL = 'equity:NASDAQ:AAPL';

function record(payload: Record<string, unknown>, revision = 1): TradingDocument {
  return { record_type: 'watchlist-flags', record_id: 'default', revision, payload } as unknown as TradingDocument;
}

beforeEach(() => {
  vi.spyOn(tradingApi, 'documents').mockResolvedValue([]);
  vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (_type, _id, payload) => record(payload as Record<string, unknown>));
});

afterEach(() => {
  cleanup();
  resetTradingWatchlistFlags();
  vi.restoreAllMocks();
});

const menu = (instrumentId: string | null) => (
  <TradingChartContextMenu
    point={{ x: 0, y: 0, price: 100 } as never} instrumentId={instrumentId} symbol="AAPL" indicatorContext={false} drawingCount={0} indicatorCount={0}
    cursorLocked={false} tableVisible={false} onClose={vi.fn()} onReset={vi.fn()} onCopyPrice={vi.fn()} onPastePrice={vi.fn()} onAddAlert={null}
    onToggleCursor={vi.fn()} onToggleTable={vi.fn()} onObjectTree={vi.fn()} onApplyTemplate={vi.fn()} onRemoveDrawings={vi.fn()}
    onRemoveIndicators={vi.fn()} onSettings={vi.fn()}
  />
);

describe('flags from the chart and the screener (TVP-5.1)', () => {
  it('flags the chart symbol from its context menu, and the watchlist sees it', async () => {
    const shared = renderHook(() => useTradingWatchlistFlags());
    render(menu(AAPL));
    fireEvent.click(screen.getByRole('menuitem', { name: /Flag AAPL/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Red/ }));
    await waitFor(() => expect(tradingApi.createDocument).toHaveBeenCalledWith('watchlist-flags', 'default', expect.objectContaining({ flags: [{ instrumentId: AAPL, color: 'red' }] })));
    await waitFor(() => expect(shared.result.current.flags.get(AAPL)).toBe('red'));
  });

  it('has no Flag item on a pane or drawing menu', () => {
    render(menu(null));
    expect(screen.queryByRole('menuitem', { name: /Flag/ })).toBeNull();
  });

  it('flags a screener result from its row', async () => {
    const result = { run_id: 'r', instrument_id: AAPL, rank: 1, score: '1', metrics: {} } as unknown as TradingScannerResult;
    render(<ScreenerResultsTable results={[result]} rules={[]} added={new Set()} symbolOf={() => 'AAPL'} onShow={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Flag AAPL' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Green flag' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Flag AAPL (flagged)' })).toBeTruthy());
    await act(async () => {});
    expect(tradingApi.createDocument).toHaveBeenCalledWith('watchlist-flags', 'default', expect.objectContaining({ flags: [{ instrumentId: AAPL, color: 'green' }] }));
  });
});
