import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { TradingSymbolSearch } from './TradingSymbolSearch';
import type { CanonicalInstrument } from './tradingTypes';
import { parseTradingFormula } from './tradingFormula';
import { chartKeyContextActive, noteTradingPointerDown, resetTradingPointerContext } from './commands/chartKeyContext';

const crypto: CanonicalInstrument = {
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  asset_class: 'crypto',
  instrument_type: 'spot',
  venue: 'BINANCE',
  venue_symbol: 'BTC-USDT',
  display_symbol: 'BTCUSDT',
  base_currency: 'BTC',
  quote_currency: 'USDT',
  exchange_timezone: 'UTC',
  session_calendar: '24x7',
  price_scale: 100,
  minimum_tick: '0.01',
  status: 'active',
  name: null,
};

const stock: CanonicalInstrument = {
  instrument_id: 'equity:NASDAQ:AAPL',
  asset_class: 'equity',
  instrument_type: 'equity',
  venue: 'NASDAQ',
  venue_symbol: 'AAPL',
  display_symbol: 'AAPL',
  base_currency: null,
  quote_currency: 'USD',
  exchange_timezone: 'America/New_York',
  session_calendar: 'XNYS',
  price_scale: 100,
  minimum_tick: '0.01',
  status: 'active',
  name: null,
};

const commodity: CanonicalInstrument = {
  instrument_id: 'commodity:YAHOO:USOIL',
  asset_class: 'commodity',
  instrument_type: 'perpetual',
  venue: 'YAHOO',
  venue_symbol: 'CL=F',
  display_symbol: 'USOIL',
  base_currency: null,
  quote_currency: 'USD',
  exchange_timezone: 'America/Chicago',
  session_calendar: '24x7',
  price_scale: 100,
  minimum_tick: '0.01',
  status: 'active',
  name: null,
};

const unemployment: CanonicalInstrument = {
  instrument_id: 'economic:FRED:UNRATE',
  asset_class: 'economic',
  instrument_type: 'index',
  venue: 'FRED',
  venue_symbol: 'UNRATE',
  display_symbol: 'UNRATE',
  base_currency: null,
  quote_currency: null,
  exchange_timezone: 'America/New_York',
  session_calendar: '24x7',
  price_scale: 10_000,
  minimum_tick: '0.0001',
  status: 'active',
  name: 'Unemployment rate',
};

function SearchHarness({ onSelect, onClose }: { onSelect: (instrument: CanonicalInstrument) => void; onClose: () => void }) {
  const [query, setQuery] = useState('');
  return (
    <TradingSymbolSearch
      open
      query={query}
      instruments={[crypto, stock]}
      activeInstrumentId={crypto.instrument_id}
      onQueryChange={setQuery}
      onSelect={onSelect}
      onClose={onClose}
    />
  );
}

describe('TradingSymbolSearch', () => {
  it('opens with TradingView-style categories and filters stock and crypto rows', () => {
    render(<SearchHarness onSelect={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByRole('dialog', { name: 'Symbol search' })).toBeVisible();
    expect(screen.getByRole('button', { name: /BTCUSDT/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /AAPL/ })).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Stocks' }));
    expect(screen.getByRole('button', { name: /AAPL/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /BTCUSDT/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Crypto' }));
    expect(screen.getByRole('button', { name: /BTCUSDT/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /AAPL/ })).not.toBeInTheDocument();
  });

  it('searches by query and selects a result', () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(<SearchHarness onSelect={onSelect} onClose={onClose} />);

    fireEvent.change(screen.getByRole('textbox', { name: 'Search symbols' }), { target: { value: 'AAP' } });
    expect(screen.getByRole('button', { name: /AAPL/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /BTCUSDT/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /AAPL/ }));
    expect(onSelect).toHaveBeenCalledWith(stock);

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search symbols' }), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('finds commodity aliases in the Futures category', () => {
    const onSelect = vi.fn();
    render(
      <TradingSymbolSearch
        open
        query="USOIL"
        instruments={[crypto, stock, commodity]}
        activeInstrumentId={crypto.instrument_id}
        onQueryChange={vi.fn()}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );

    const result = screen.getByRole('button', { name: /USOIL/ });
    expect(result).toHaveTextContent('WTI Crude Oil');
    expect(result).toHaveTextContent('commodity');
    fireEvent.click(result);
    expect(onSelect).toHaveBeenCalledWith(commodity);
  });

  it('lists FRED series under Economy by their title (TVP-10.5)', () => {
    const onSelect = vi.fn();
    render(
      <TradingSymbolSearch
        open
        query="unemployment"
        instruments={[crypto, stock, unemployment]}
        activeInstrumentId={crypto.instrument_id}
        onQueryChange={vi.fn()}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Economy' }));
    const result = screen.getByRole('button', { name: /UNRATE/ });
    expect(result).toHaveTextContent('Unemployment rate');
    expect(result).toHaveTextContent('economic');
    expect(screen.queryByRole('button', { name: /AAPL/ })).not.toBeInTheDocument();
    fireEvent.click(result);
    expect(onSelect).toHaveBeenCalledWith(unemployment);
  });

  it('offers a resolved arithmetic chart and accepts Enter', () => {
    const onSelectFormula = vi.fn();
    const formula = parseTradingFormula('BTCUSDT / ETHUSDT');
    if (!formula) throw new Error('test formula did not parse');
    render(
      <TradingSymbolSearch
        open
        query="BTCUSDT / ETHUSDT"
        instruments={[crypto, stock]}
        activeInstrumentId={crypto.instrument_id}
        formulaPreview={{ formula, unresolvedSymbols: [] }}
        onQueryChange={vi.fn()}
        onSelect={vi.fn()}
        onSelectFormula={onSelectFormula}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Create arithmetic chart BTCUSDT / ETHUSDT' })).toBeEnabled();
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search symbols' }), { key: 'Enter' });
    expect(onSelectFormula).toHaveBeenCalledTimes(1);
  });

  it('keeps an arithmetic chart selectable when a leg needs chart-time resolution', () => {
    const onSelectFormula = vi.fn();
    const formula = parseTradingFormula('(TOTAL3-USDT) / BTC');
    if (!formula) throw new Error('test formula did not parse');
    render(
      <TradingSymbolSearch
        open
        query="(TOTAL3-USDT) / BTC"
        instruments={[crypto, stock]}
        activeInstrumentId={crypto.instrument_id}
        formulaPreview={{ formula, unresolvedSymbols: ['TOTAL3-USDT'] }}
        onQueryChange={vi.fn()}
        onSelect={vi.fn()}
        onSelectFormula={onSelectFormula}
        onClose={vi.fn()}
      />,
    );

    const result = screen.getByRole('button', { name: 'Create arithmetic chart (TOTAL3-USDT) / BTC' });
    expect(result).toBeEnabled();
    fireEvent.click(result);
    expect(onSelectFormula).toHaveBeenCalledTimes(1);
  });
});

describe('TradingSymbolSearch and chart keys', () => {
  it('returns the keyboard to the chart when a symbol is chosen', () => {
    const header = document.createElement('button');
    document.body.append(header);
    noteTradingPointerDown(header);
    header.remove();
    expect(chartKeyContextActive('chart')).toBe(false);
    const props = { query: 'AAPL', instruments: [crypto, stock], activeInstrumentId: crypto.instrument_id, onQueryChange: vi.fn(), onSelect: vi.fn(), onClose: vi.fn() };
    const view = render(<TradingSymbolSearch open {...props} />);
    fireEvent.click(screen.getByRole('option', { name: /AAPL/ }).querySelector('button')!);
    view.rerender(<TradingSymbolSearch open={false} {...props} />);
    expect(chartKeyContextActive('chart')).toBe(true);
    resetTradingPointerContext();
  });
});
