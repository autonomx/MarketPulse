import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { CanonicalInstrument } from './tradingTypes';
import { TradingWatchlistSymbolPicker } from './TradingWatchlistSymbolPicker';

const apple: CanonicalInstrument = {
  instrument_id: 'equity:NASDAQ:AAPL',
  asset_class: 'equity',
  instrument_type: 'equity',
  venue: 'NASDAQ',
  venue_symbol: 'AAPL',
  display_symbol: 'AAPL',
  base_currency: null,
  quote_currency: 'USD',
  exchange_timezone: 'America/New_York',
  session_calendar: 'XNAS',
  price_scale: 100,
  minimum_tick: '0.01',
  status: 'active',
  name: null,
};

const nvidia: CanonicalInstrument = {
  ...apple,
  instrument_id: 'equity:NASDAQ:NVDA',
  venue_symbol: 'NVDA',
  display_symbol: 'NVDA',
};

describe('TradingWatchlistSymbolPicker', () => {
  it('shows a TradingView-style add dialog and disables only symbols already in the watchlist', () => {
    const onAdd = vi.fn();
    render(
      <TradingWatchlistSymbolPicker
        open
        instruments={[apple, nvidia]}
        selectedInstrumentIds={[apple.instrument_id]}
        onAdd={onAdd}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog', { name: 'Add symbol' })).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Search symbols to add' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Stocks' })).toBeVisible();

    expect(screen.getByRole('button', { name: 'AAPL already in watchlist' })).toBeDisabled();
    const addNvidia = screen.getByRole('button', { name: 'Add NVDA to watchlist' });
    expect(addNvidia).toBeEnabled();

    fireEvent.click(addNvidia);
    expect(onAdd).toHaveBeenCalledWith(nvidia);
    expect(screen.getByRole('dialog', { name: 'Add symbol' })).toBeVisible();
  });

  it('supports Enter to add the first available symbol and Escape to close', () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();
    render(
      <TradingWatchlistSymbolPicker
        open
        instruments={[apple, nvidia]}
        selectedInstrumentIds={[apple.instrument_id]}
        onAdd={onAdd}
        onClose={onClose}
      />,
    );

    const search = screen.getByRole('textbox', { name: 'Search symbols to add' });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith(nvidia);

    fireEvent.keyDown(search, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('supports Shift+Enter and Shift+Click to add and close', async () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(
      <TradingWatchlistSymbolPicker
        open
        instruments={[apple, nvidia]}
        selectedInstrumentIds={[apple.instrument_id]}
        onAdd={onAdd}
        onClose={onClose}
      />,
    );

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search symbols to add' }), { key: 'Enter', shiftKey: true });
    expect(onAdd).toHaveBeenCalledWith(nvidia);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));

    rerender(
      <TradingWatchlistSymbolPicker
        open
        instruments={[apple, nvidia]}
        selectedInstrumentIds={[apple.instrument_id]}
        onAdd={onAdd}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add NVDA to watchlist' }), { shiftKey: true });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
  });
});
