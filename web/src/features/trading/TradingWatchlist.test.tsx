import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { fixture } from '../../test/fixture';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { tradingApi } from './tradingApi';
import type { BarsResponse, CanonicalInstrument, ProviderBinding, TradingDocument } from './tradingTypes';
import { TradingWatchlist } from './TradingWatchlist';
import { resetTradingWatchlistFlags } from './useTradingWatchlistFlags';
import { useTradingCommandDispatcher } from './commands/useTradingCommands';
import { onOmnixEvent } from '../../events/bus';
import './advancedView';

// The column chooser's indicator picker (TVP-5.2) reads the server's indicator list through react-query.
vi.mock('./useTradingAlerts', async (original) => ({ ...(await original<typeof import('./useTradingAlerts')>()), useAlertIndicatorIds: () => new Set(['rsi']) }));

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

const record = {
  record_id: 'default',
  revision: 1,
  payload: {
    name: 'Default Watchlist',
    instrumentIds: [apple.instrument_id],
  },
} as unknown as TradingDocument;

const gameStop: CanonicalInstrument = {
  ...apple,
  instrument_id: 'equity:NYSE:GME',
  venue: 'NYSE',
  venue_symbol: 'GME',
  display_symbol: 'GME',
};

const otcEquity: CanonicalInstrument = {
  ...apple,
  instrument_id: 'equity:PNK:GMETF',
  venue: 'PNK',
  venue_symbol: 'GMETF',
  display_symbol: 'GMETF',
};

function mockDocuments(watchlists: TradingDocument[], flagSets: TradingDocument[] = []) {
  return vi.spyOn(tradingApi, 'documents').mockImplementation(async (kind) => (
    kind === 'watchlist-flags' ? flagSets : watchlists
  ));
}

function watchlistCalls(documents: ReturnType<typeof mockDocuments>) {
  return documents.mock.calls.filter(([kind]) => kind === 'watchlists');
}

afterEach(() => {
  // The flags are one store for the page (TVP-5.1): each test starts without them.
  resetTradingWatchlistFlags();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('TradingWatchlist add symbol', () => {
  it('does not restore deleted default symbols after reload', async () => {
    const persisted = {
      ...record,
      payload: {
        name: 'Default Watchlist',
        instrumentIds: [apple.instrument_id],
      },
    } as TradingDocument;
    mockDocuments([persisted]);
    const update = vi.spyOn(tradingApi, 'updateDocument');
    vi.spyOn(tradingApi, 'quote').mockResolvedValue(fixture({ price: '100' }));
    vi.spyOn(tradingApi, 'bars').mockResolvedValue({
      bars: [],
      binding: { supported_intervals: ['1m'] },
    } as unknown as BarsResponse);

    render(
      <TradingWatchlist
        instruments={[apple, gameStop]}
        activeInstrumentId={apple.instrument_id}
        interval="1m"
        onSelect={vi.fn()}
      />,
    );

    await screen.findByRole('button', { name: 'Select AAPL' });
    expect(screen.queryByRole('button', { name: 'Select GME' })).not.toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it('keeps the toolbar plus enabled when the active symbol is already listed and opens the symbol picker', async () => {
    mockDocuments([record]);
    vi.spyOn(tradingApi, 'quote').mockResolvedValue(fixture({ price: '100' }));
    vi.spyOn(tradingApi, 'bars').mockResolvedValue({
      bars: [],
      binding: { supported_intervals: ['1m'] },
    } as unknown as BarsResponse);

    render(
      <TradingWatchlist
        instruments={[apple]}
        activeInstrumentId={apple.instrument_id}
        interval="1m"
        onSelect={vi.fn()}
      />,
    );

    const addSymbol = await screen.findByRole('button', { name: 'Add symbol to watchlist' });
    await waitFor(() => expect(addSymbol).toBeEnabled());

    fireEvent.click(addSymbol);

    expect(screen.getByRole('dialog', { name: 'Add symbol' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'AAPL already in watchlist' })).toBeDisabled();
  });

  it('does not request unsupported intraday bars for OTC symbols', async () => {
    const otcRecord = {
      ...record,
      payload: {
        name: 'Default Watchlist',
        instrumentIds: [otcEquity.instrument_id],
      },
    } as TradingDocument;
    mockDocuments([otcRecord]);
    const quote = vi.spyOn(tradingApi, 'quote').mockResolvedValue(fixture({ price: '0.008' }));
    const bars = vi.spyOn(tradingApi, 'bars');

    render(
      <TradingWatchlist
        instruments={[otcEquity]}
        providerBindings={[{
          instrument_id: otcEquity.instrument_id,
          supported_intervals: ['1d', '1w', '1mo'],
        } as unknown as ProviderBinding]}
        activeInstrumentId={otcEquity.instrument_id}
        interval="2h"
        onSelect={vi.fn()}
      />,
    );

    await screen.findByRole('button', { name: 'Select GMETF' });
    await waitFor(() => expect(quote).toHaveBeenCalledWith(otcEquity.instrument_id));
    expect(bars).not.toHaveBeenCalled();
  });

  it('keeps the last known values visible when returning to the watchlist', async () => {
    const instruments = [apple];
    let resolveRefresh: (value: { price: string }) => void = () => undefined;
    const refreshQuote = new Promise<{ price: string }>((resolve) => {
      resolveRefresh = resolve;
    });
    mockDocuments([record]);
    vi.spyOn(tradingApi, 'quote')
      .mockResolvedValueOnce(fixture({ price: '100' }))
      .mockImplementationOnce(() => refreshQuote.then((quote) => fixture(quote)));
    vi.spyOn(tradingApi, 'bars').mockResolvedValue({
      bars: [{ open: '100', close: '100', start_time: '2026-01-01T00:00:00Z' }],
      binding: { supported_intervals: ['1m', '5m'] },
    } as unknown as BarsResponse);

    const view = render(
      <TradingWatchlist
        instruments={instruments}
        activeInstrumentId={apple.instrument_id}
        interval="1m"
        onSelect={vi.fn()}
      />,
    );

    expect(await screen.findByText('100.00')).toBeInTheDocument();

    view.unmount();
    render(
      <TradingWatchlist
        instruments={instruments}
        activeInstrumentId={apple.instrument_id}
        interval="5m"
        onSelect={vi.fn()}
      />,
    );

    expect(await screen.findByText('100.00')).toBeInTheDocument();
    resolveRefresh({ price: '105' });
    await waitFor(() => expect(screen.getByText('105.00')).toBeInTheDocument());
  });

  it('reorders symbols immediately with the row arrows and persists the new order', async () => {
    const orderedRecord = {
      ...record,
      payload: {
        name: 'Default Watchlist',
        instrumentIds: [apple.instrument_id, gameStop.instrument_id],
      },
    } as TradingDocument;
    mockDocuments([orderedRecord]);
    vi.spyOn(tradingApi, 'quote').mockResolvedValue(fixture({ price: '100' }));
    vi.spyOn(tradingApi, 'bars').mockResolvedValue({
      bars: [],
      binding: { supported_intervals: ['1m'] },
    } as unknown as BarsResponse);
    const update = vi.spyOn(tradingApi, 'updateDocument').mockImplementation(async (_kind, currentRecord, nextPayload) => ({
      ...currentRecord,
      revision: currentRecord.revision + 1,
      payload: nextPayload,
    }));

    render(
      <TradingWatchlist
        instruments={[apple, gameStop]}
        activeInstrumentId={apple.instrument_id}
        interval="1m"
        onSelect={vi.fn()}
      />,
    );

    await screen.findByRole('button', { name: 'Move GME up' });
    const selectedSymbols = () => screen.getAllByRole('button', { name: /^Select / }).map((button) => button.querySelector('strong')?.textContent);

    fireEvent.click(screen.getByRole('button', { name: 'Move GME up' }));
    await waitFor(() => expect(selectedSymbols()).toEqual(['GME', 'AAPL']));
    expect(update).toHaveBeenCalledWith(
      'watchlists',
      orderedRecord,
      {
        schemaVersion: 2,
        name: 'Default Watchlist',
        items: [
          { type: 'symbol', instrumentId: gameStop.instrument_id },
          { type: 'symbol', instrumentId: apple.instrument_id },
        ],
        instrumentIds: [gameStop.instrument_id, apple.instrument_id],
      },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Move GME down' }));
    await waitFor(() => expect(selectedSymbols()).toEqual(['AAPL', 'GME']));
  });

  it('sorts symbols by percentage change without changing the saved order', async () => {
    const sortableRecord = {
      ...record,
      payload: {
        name: 'Default Watchlist',
        instrumentIds: [gameStop.instrument_id, apple.instrument_id],
      },
    } as TradingDocument;
    mockDocuments([sortableRecord]);
    vi.spyOn(tradingApi, 'quote').mockImplementation(async (instrumentId) => fixture(({
      price: instrumentId === gameStop.instrument_id ? '90' : '110',
    })));
    vi.spyOn(tradingApi, 'bars').mockResolvedValue({
      bars: [{ open: '100', close: '100', start_time: '2026-01-01T00:00:00Z' }],
      binding: { supported_intervals: ['1m'] },
    } as unknown as BarsResponse);

    render(
      <TradingWatchlist
        instruments={[apple, gameStop]}
        activeInstrumentId={apple.instrument_id}
        interval="1m"
        onSelect={vi.fn()}
      />,
    );

    await screen.findByRole('button', { name: 'Select GME' });
    const selectedSymbols = () => screen.getAllByRole('button', { name: /^Select / }).map((button) => button.querySelector('strong')?.textContent);
    const sortByChange = () => screen.getByRole('button', { name: /watchlist.*change percentage/ });

    await waitFor(() => expect(selectedSymbols()).toEqual(['GME', 'AAPL']));
    fireEvent.click(sortByChange());
    await waitFor(() => expect(selectedSymbols()).toEqual(['AAPL', 'GME']));
    fireEvent.click(sortByChange());
    await waitFor(() => expect(selectedSymbols()).toEqual(['GME', 'AAPL']));
    fireEvent.click(sortByChange());
    await waitFor(() => expect(selectedSymbols()).toEqual(['GME', 'AAPL']));
  });

  it('re-applies a removal to the latest revision after a conflict, keeping the other change', async () => {
    const teslaId = 'equity:NASDAQ:TSLA';
    const twoSymbolRecord = {
      ...record,
      payload: { name: 'Default Watchlist', instrumentIds: [apple.instrument_id, gameStop.instrument_id] },
    } as TradingDocument;
    // Meanwhile another tab added TSLA.
    const editedElsewhere = {
      ...twoSymbolRecord,
      revision: 2,
      payload: {
        schemaVersion: 2,
        name: 'Default Watchlist',
        items: [apple.instrument_id, gameStop.instrument_id, teslaId].map((instrumentId) => ({ type: 'symbol', instrumentId })),
        instrumentIds: [apple.instrument_id, gameStop.instrument_id, teslaId],
      },
    } as unknown as TradingDocument;
    let watchlistLoads = 0;
    const documents = vi.spyOn(tradingApi, 'documents').mockImplementation(async (kind) => {
      if (kind === 'watchlist-flags') return [];
      watchlistLoads += 1;
      return [watchlistLoads === 1 ? twoSymbolRecord : editedElsewhere];
    });
    mockMarketData();
    const update = vi.spyOn(tradingApi, 'updateDocument')
      .mockRejectedValueOnce(new Error('Trading request failed (409): revision conflict'))
      .mockImplementationOnce(async (_kind, currentRecord, nextPayload) => ({ ...currentRecord, revision: 3, payload: nextPayload }));

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Remove AAPL' }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[1][1]).toBe(editedElsewhere);
    expect(update.mock.calls[1][2]).toEqual({
      schemaVersion: 2,
      name: 'Default Watchlist',
      items: [{ type: 'symbol', instrumentId: gameStop.instrument_id }, { type: 'symbol', instrumentId: teslaId }],
      instrumentIds: [gameStop.instrument_id, teslaId],
    });
    await waitFor(() => expect(symbolNames()).toEqual(['GME', 'TSLA']));
    expect(watchlistCalls(documents)).toHaveLength(2);
    expect(screen.getByText('saved')).toBeInTheDocument();
  });

  it('shows a list saved by a newer Omnix from its mirror and never saves over it', async () => {
    const newer = {
      ...record,
      payload: { schemaVersion: 3, name: 'Future', items: [{ type: 'board' }], instrumentIds: [gameStop.instrument_id] },
    } as unknown as TradingDocument;
    mockDocuments([newer]);
    mockMarketData();
    const update = vi.spyOn(tradingApi, 'updateDocument');

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    expect(await screen.findByRole('button', { name: 'Select GME' })).toBeInTheDocument();
    expect(screen.getByText(/saved by a newer version of Omnix/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove GME' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Move GME up' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add symbol to watchlist' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist options' }));
    expect(screen.getByRole('menuitem', { name: 'Rename watchlist' })).toBeDisabled();
    expect(update).not.toHaveBeenCalled();
  });
});

describe('advanced view from the watchlist (TVP-5.4)', () => {
  it('opens the advanced view on the shown list', async () => {
    mockDocuments([record]);
    mockMarketData();
    const opened = vi.fn();
    const stop = onOmnixEvent('omnix:trading-advanced-view', opened);
    render(<TradingWatchlist instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />);
    await screen.findByRole('button', { name: 'Select AAPL' });
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Advanced view' }));
    expect(opened).toHaveBeenCalledWith({ watchlistId: 'default' });
    expect(screen.queryByRole('menu')).toBeNull();
    stop();
  });
});

function mockMarketData() {
  vi.spyOn(tradingApi, 'quote').mockResolvedValue(fixture({ price: '100' }));
  vi.spyOn(tradingApi, 'bars').mockResolvedValue({
    bars: [],
    binding: { supported_intervals: ['1m'] },
  } as unknown as BarsResponse);
}

function echoUpdates() {
  return vi.spyOn(tradingApi, 'updateDocument').mockImplementation(async (_kind, currentRecord, nextPayload) => ({
    ...currentRecord,
    revision: currentRecord.revision + 1,
    payload: nextPayload,
  }));
}

const sectionedRecord = {
  ...record,
  payload: {
    schemaVersion: 2,
    name: 'Sectioned',
    items: [
      { type: 'symbol', instrumentId: apple.instrument_id },
      { type: 'section', id: 'tech', name: 'Meme stocks', collapsed: false },
      { type: 'symbol', instrumentId: gameStop.instrument_id },
    ],
  },
} as unknown as TradingDocument;

const symbolNames = () => screen.getAllByRole('button', { name: /^Select / }).map((button) => button.querySelector('strong')?.textContent);

describe('TradingWatchlist save queue', () => {
  const teslaId = 'equity:NASDAQ:TSLA';
  const threeRecord = {
    ...record,
    payload: { name: 'Default Watchlist', instrumentIds: [apple.instrument_id, gameStop.instrument_id, teslaId] },
  } as unknown as TradingDocument;
  const itemsOf = (...ids: string[]) => ids.map((instrumentId) => ({ type: 'symbol', instrumentId }));

  function deferred<T>() {
    let resolve: (value: T) => void = () => undefined;
    let reject: (error: unknown) => void = () => undefined;
    const promise = new Promise<T>((onResolve, onReject) => {
      resolve = onResolve;
      reject = onReject;
    });
    return { promise, resolve, reject };
  }

  it('saves overlapping edits one at a time, each applied once to the last server revision', async () => {
    mockDocuments([threeRecord]);
    mockMarketData();
    const first = deferred<TradingDocument>();
    const update = vi.spyOn(tradingApi, 'updateDocument')
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async (_kind, currentRecord, nextPayload) => ({ ...currentRecord, revision: currentRecord.revision + 1, payload: nextPayload }));

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Move TSLA up' }));
    fireEvent.click(screen.getByRole('button', { name: 'Move TSLA up' }));
    expect(symbolNames()).toEqual(['TSLA', 'AAPL', 'GME']);
    // The second save waits for the first instead of racing it.
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(update).toHaveBeenCalledTimes(1);
    expect(update.mock.calls[0][2]).toMatchObject({ items: itemsOf(apple.instrument_id, teslaId, gameStop.instrument_id) });

    const firstSaved = { ...threeRecord, revision: 2, payload: update.mock.calls[0][2] } as TradingDocument;
    first.resolve(firstSaved);

    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[1][1]).toBe(firstSaved);
    expect(update.mock.calls[1][2]).toMatchObject({ items: itemsOf(teslaId, apple.instrument_id, gameStop.instrument_id) });
    await waitFor(() => expect(screen.getByText('saved')).toBeInTheDocument());
    expect(symbolNames()).toEqual(['TSLA', 'AAPL', 'GME']);
  });

  it('drops a failed edit so a later save does not persist it', async () => {
    mockDocuments([threeRecord]);
    mockMarketData();
    const first = deferred<TradingDocument>();
    const update = vi.spyOn(tradingApi, 'updateDocument')
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(async (_kind, currentRecord, nextPayload) => ({ ...currentRecord, revision: currentRecord.revision + 1, payload: nextPayload }));

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Remove AAPL' }));
    fireEvent.click(screen.getByRole('button', { name: 'Move TSLA up' }));
    expect(symbolNames()).toEqual(['TSLA', 'GME']);

    first.reject(new Error('Trading request failed (500): unavailable'));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[1][1]).toBe(threeRecord);
    expect(update.mock.calls[1][2]).toMatchObject({ items: itemsOf(apple.instrument_id, teslaId, gameStop.instrument_id) });
    await waitFor(() => expect(symbolNames()).toEqual(['AAPL', 'TSLA', 'GME']));
    expect(screen.getByText('error')).toBeInTheDocument();
  });
});

describe('TradingWatchlist sections and flags', () => {
  it('collapses a section, hides its symbols and keeps the state after a reload', async () => {
    mockDocuments([sectionedRecord]);
    mockMarketData();
    const update = echoUpdates();

    const view = render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select GME' });
    expect(screen.getByRole('button', { name: 'Collapse section Meme stocks' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Collapse section Meme stocks' }));

    await waitFor(() => expect(symbolNames()).toEqual(['AAPL']));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const saved = update.mock.calls[0][2] as { items: Array<Record<string, unknown>> };
    expect(saved.items[1]).toEqual({ type: 'section', id: 'tech', name: 'Meme stocks', collapsed: true });

    view.unmount();
    mockDocuments([{ ...sectionedRecord, revision: 2, payload: saved } as unknown as TradingDocument]);
    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );
    expect(await screen.findByRole('button', { name: 'Expand section Meme stocks' })).toHaveAttribute('aria-expanded', 'false');
    expect(symbolNames()).toEqual(['AAPL']);
  });

  it('adds a named section from the options menu and upgrades a version 1 list on save', async () => {
    mockDocuments([record]);
    mockMarketData();
    const update = echoUpdates();
    vi.spyOn(window, 'prompt').mockReturnValue('Earnings');

    render(
      <TradingWatchlist instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select AAPL' });
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add section' }));

    expect(await screen.findByRole('button', { name: 'Collapse section Earnings' })).toBeInTheDocument();
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][2]).toEqual({
      schemaVersion: 2,
      name: 'Default Watchlist',
      items: [
        { type: 'symbol', instrumentId: apple.instrument_id },
        { type: 'section', id: expect.any(String), name: 'Earnings', collapsed: false },
      ],
      instrumentIds: [apple.instrument_id],
    });
  });

  it('flags a symbol from its row and lists it under the generated flag list', async () => {
    const twoSymbols = { ...record, payload: { name: 'Default Watchlist', instrumentIds: [apple.instrument_id, gameStop.instrument_id] } } as unknown as TradingDocument;
    mockDocuments([twoSymbols]);
    mockMarketData();
    const create = vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (kind, recordId, payload) => fixture({
      record_id: recordId,
      record_type: kind,
      revision: 1,
      payload,
      status: 'active',
    }));

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select GME' });
    expect(screen.queryByRole('option', { name: 'Red flags' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Flag GME' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'Flag GME' })).getByRole('menuitemradio', { name: 'Red flag' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith('watchlist-flags', 'default', {
      schemaVersion: 1,
      flags: [{ instrumentId: gameStop.instrument_id, color: 'red' }],
    }));
    expect(screen.getByRole('img', { name: 'Red flag' })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox', { name: 'Watchlist' }), { target: { value: 'flag:red' } });
    await waitFor(() => expect(symbolNames()).toEqual(['GME']));
    expect(screen.queryByRole('button', { name: 'Move GME up' })).not.toBeInTheDocument();
  });

  it('clears a flag when a symbol is removed from its flag list', async () => {
    const flagSet = fixture<TradingDocument>({
      record_id: 'default',
      record_type: 'watchlist_flag_set',
      revision: 4,
      payload: { schemaVersion: 1, flags: [{ instrumentId: apple.instrument_id, color: 'green' }] },
      status: 'active',
    });
    mockDocuments([record], [flagSet]);
    mockMarketData();
    const update = echoUpdates();

    render(
      <TradingWatchlist instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('option', { name: 'Green flags' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Watchlist' }), { target: { value: 'flag:green' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Remove AAPL' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith('watchlist-flags', flagSet, { schemaVersion: 1, flags: [] }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Select AAPL' })).not.toBeInTheDocument());
  });
});

describe('TradingWatchlist columns', () => {
  it('adds a chosen column, sorts by it and remembers the view', async () => {
    const twoSymbols = { ...record, payload: { name: 'Default Watchlist', instrumentIds: [apple.instrument_id, gameStop.instrument_id] } } as unknown as TradingDocument;
    mockDocuments([twoSymbols]);
    vi.spyOn(tradingApi, 'quote').mockImplementation(async (instrumentId) => fixture({ price: instrumentId === apple.instrument_id ? '101' : '20' }));
    vi.spyOn(tradingApi, 'bars').mockImplementation(async (instrumentId) => ({
      bars: [{
        open: '100',
        close: '100',
        high: instrumentId === apple.instrument_id ? '102' : '25',
        low: '1',
        volume: instrumentId === apple.instrument_id ? '1000' : '5000000',
        session: 'regular',
        start_time: '2026-01-01T00:00:00Z',
      }],
      binding: { supported_intervals: ['1m'] },
    } as unknown as BarsResponse));

    const view = render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select GME' });
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist options' }));
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /^Vol volume/ }));
    expect(screen.getByRole('menuitemcheckbox', { name: /^Vol volume/ })).toHaveAttribute('aria-checked', 'true');

    expect(await screen.findByText('5M')).toBeInTheDocument();
    expect(screen.getByText('1K')).toBeInTheDocument();
    await waitFor(() => expect(symbolNames()).toEqual(['AAPL', 'GME']));
    fireEvent.click(screen.getByRole('button', { name: 'Sort watchlist by volume descending' }));
    await waitFor(() => expect(symbolNames()).toEqual(['GME', 'AAPL']));
    fireEvent.click(screen.getByRole('button', { name: 'Sort watchlist by volume ascending' }));
    await waitFor(() => expect(symbolNames()).toEqual(['AAPL', 'GME']));

    view.unmount();
    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );
    expect((await screen.findByRole('button', { name: 'Clear watchlist volume sort' })).closest('[role="columnheader"]')).toHaveAttribute('aria-sort', 'ascending');
    fireEvent.click(screen.getByRole('button', { name: 'Sort watchlist by symbol ascending' }));
    await waitFor(() => expect(symbolNames()).toEqual(['AAPL', 'GME']));
    expect(screen.getByRole('button', { name: 'Sort watchlist by symbol descending' }).closest('[role="columnheader"]')).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByRole('button', { name: 'Sort watchlist by volume descending' }).closest('[role="columnheader"]')).toHaveAttribute('aria-sort', 'none');
  });

  it('hides a column and drops its sort', async () => {
    mockDocuments([record]);
    mockMarketData();
    window.localStorage.setItem('omnix.trading.watchlist-view', JSON.stringify({ columns: ['last', 'changePercent'], sort: { key: 'changePercent', direction: 'desc' } }));

    render(
      <TradingWatchlist instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select AAPL' });
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist options' }));
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /^Chg% change percentage/ }));
    expect(screen.queryByRole('button', { name: /change percentage/ })).not.toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem('omnix.trading.watchlist-view') ?? '{}')).toEqual({ columns: ['last'], sort: null });
  });
});

describe('TradingWatchlist tree grid', () => {
  it('lines up header and cells, and moves through rows, sections and cells with the arrow keys', async () => {
    mockDocuments([sectionedRecord]);
    mockMarketData();
    const update = echoUpdates();
    const onSelect = vi.fn();

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={onSelect} />,
    );

    const grid = await screen.findByRole('treegrid', { name: 'Watchlist symbols' });
    await screen.findByRole('button', { name: 'Select GME' });
    const width = (row: Element) => [...row.querySelectorAll('[role="gridcell"], [role="columnheader"]')]
      .reduce((total, cell) => total + Number(cell.getAttribute('aria-colspan') ?? 1), 0);
    const [header, ...rows] = screen.getAllByRole('row');
    expect(rows.map(width)).toEqual(rows.map(() => width(header)));
    // Row buttons are out of the Tab order; only the tab stop is tabbable.
    expect(grid.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Remove GME' })).toHaveAttribute('tabindex', '-1');

    const sectionRow = screen.getByRole('button', { name: 'Collapse section Meme stocks' }).closest('[role="row"]') as HTMLElement;
    expect(sectionRow).toHaveAttribute('aria-level', '1');
    expect(sectionRow).toHaveAttribute('aria-expanded', 'true');
    const gmeRow = screen.getByRole('button', { name: 'Select GME' }).closest('[role="row"]') as HTMLElement;
    expect(gmeRow).toHaveAttribute('aria-level', '2');

    fireEvent.keyDown(grid, { key: 'ArrowDown' });
    expect(sectionRow).toHaveFocus();
    fireEvent.keyDown(sectionRow, { key: 'ArrowDown' });
    expect(gmeRow).toHaveFocus();
    expect(onSelect).toHaveBeenLastCalledWith(gameStop.instrument_id);

    fireEvent.keyDown(gmeRow, { key: 'ArrowRight' });
    expect(screen.getByRole('button', { name: 'Select GME' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Select GME' }), { key: 'End' });
    expect(screen.getByRole('button', { name: 'Remove GME' })).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Remove GME' })).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(screen.getByRole('button', { name: 'Remove GME' }), { key: 'Home' });
    expect(screen.getByRole('button', { name: 'Select GME' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Select GME' }), { key: 'ArrowLeft' });
    expect(gmeRow).toHaveFocus();
    fireEvent.keyDown(gmeRow, { key: 'ArrowLeft' });
    expect(sectionRow).toHaveFocus();

    fireEvent.keyDown(sectionRow, { key: 'ArrowLeft' });
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][2]).toMatchObject({ items: expect.arrayContaining([{ type: 'section', id: 'tech', name: 'Meme stocks', collapsed: true }]) });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Select GME' })).not.toBeInTheDocument());
    fireEvent.keyDown(sectionRow, { key: 'ArrowRight' });
    expect(await screen.findByRole('button', { name: 'Select GME' })).toBeInTheDocument();

    fireEvent.keyDown(sectionRow, { key: 'Home', ctrlKey: true });
    expect(screen.getByRole('button', { name: 'Select AAPL' }).closest('[role="row"]')).toHaveFocus();
  });
});

describe('TradingWatchlist keyboard and import', () => {
  const tesla: CanonicalInstrument = { ...apple, instrument_id: 'equity:NASDAQ:TSLA', venue_symbol: 'TSLA', display_symbol: 'TSLA' };
  const threeSymbols = {
    ...record,
    payload: { name: 'Default Watchlist', instrumentIds: [apple.instrument_id, gameStop.instrument_id, tesla.instrument_id] },
  } as unknown as TradingDocument;
  const selectedRows = () => [...document.querySelectorAll('li.selected')].map((row) => row.querySelector('strong')?.textContent);

  it('moves through the list with the arrow keys and Space, showing each symbol on the chart', async () => {
    mockDocuments([threeSymbols]);
    mockMarketData();
    const onSelect = vi.fn();

    render(
      <TradingWatchlist instruments={[apple, gameStop, tesla]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={onSelect} />,
    );

    const list = await screen.findByRole('treegrid', { name: 'Watchlist symbols' });
    await screen.findByRole('button', { name: 'Select TSLA' });
    list.focus();
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenLastCalledWith(gameStop.instrument_id);
    expect(selectedRows()).toEqual(['GME']);
    const gmeRow = screen.getByRole('row', { selected: true });
    expect(gmeRow).toHaveAttribute('data-instrument-id', gameStop.instrument_id);
    // Roving tabindex: the focused row is the only tab stop.
    expect(gmeRow).toHaveFocus();
    expect(gmeRow).toHaveAttribute('tabindex', '0');
    expect(list.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    fireEvent.keyDown(list, { key: ' ' });
    expect(onSelect).toHaveBeenLastCalledWith(tesla.instrument_id);
    fireEvent.keyDown(list, { key: ' ', shiftKey: true });
    fireEvent.keyDown(list, { key: 'ArrowUp' });
    expect(onSelect).toHaveBeenLastCalledWith(apple.instrument_id);
    expect(onSelect).toHaveBeenCalledTimes(4);
  });

  it('extends the selection with Shift+arrows, selects all with Ctrl+A and flags the selection', async () => {
    mockDocuments([threeSymbols]);
    mockMarketData();
    const create = vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (kind, recordId, payload) => fixture({
      record_id: recordId, record_type: kind, revision: 1, payload, status: 'active',
    }));
    const onSelect = vi.fn();

    render(
      <TradingWatchlist instruments={[apple, gameStop, tesla]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={onSelect} />,
    );

    await screen.findByRole('button', { name: 'Select TSLA' });
    fireEvent.click(screen.getByRole('button', { name: 'Select AAPL' }));
    expect(onSelect).toHaveBeenCalledWith(apple.instrument_id);
    const list = screen.getByRole('treegrid', { name: 'Watchlist symbols' });
    fireEvent.keyDown(screen.getByRole('button', { name: 'Select AAPL' }), { key: 'ArrowDown', shiftKey: true });
    expect(selectedRows()).toEqual(['AAPL', 'GME']);
    expect(onSelect).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(list, { key: 'a', ctrlKey: true });
    expect(selectedRows()).toEqual(['AAPL', 'GME', 'TSLA']);

    fireEvent.click(screen.getByRole('button', { name: 'Flag TSLA' }));
    fireEvent.keyDown(screen.getByRole('menuitemradio', { name: 'Blue flag' }), { key: 'ArrowDown' });
    expect(selectedRows()).toEqual(['AAPL', 'GME', 'TSLA']);
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Blue flag' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith('watchlist-flags', 'default', {
      schemaVersion: 1,
      flags: [
        { instrumentId: apple.instrument_id, color: 'blue' },
        { instrumentId: gameStop.instrument_id, color: 'blue' },
        { instrumentId: tesla.instrument_id, color: 'blue' },
      ],
    }));
  });

  it('imports a text file as a new watchlist with sections', async () => {
    mockDocuments([record]);
    mockMarketData();
    vi.spyOn(tradingApi, 'instruments').mockResolvedValue([]);
    const create = vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (kind, recordId, payload) => fixture({
      record_id: recordId, record_type: kind, revision: 1, payload, status: 'active',
    }));

    render(
      <TradingWatchlist instruments={[apple, gameStop]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select AAPL' });
    const content = 'NYSE:GME,###Later,NASDAQ:AAPL,OTC:NOPE';
    const file = new File([content], 'Swing ideas.txt', { type: 'text/plain' });
    // jsdom's File has no text(); browsers do.
    Object.defineProperty(file, 'text', { value: async () => content });
    fireEvent.change(screen.getByLabelText('Import watchlist file'), { target: { files: [file] } });

    expect(await screen.findByText('Imported 2 symbols; not found: OTC:NOPE.')).toBeInTheDocument();
    expect(create).toHaveBeenCalledWith('watchlists', expect.stringMatching(/^watchlist-/), {
      schemaVersion: 2,
      name: 'Swing ideas',
      items: [
        { type: 'symbol', instrumentId: gameStop.instrument_id },
        { type: 'section', id: expect.any(String), name: 'Later', collapsed: false },
        { type: 'symbol', instrumentId: apple.instrument_id },
      ],
      instrumentIds: [gameStop.instrument_id, apple.instrument_id],
    });
    expect(screen.getByRole('combobox', { name: 'Watchlist' })).toHaveDisplayValue('Swing ideas');
    expect(screen.getByRole('button', { name: 'Collapse section Later' })).toBeInTheDocument();
  });

  it('creates nothing when no symbol in the file is found, and refuses a file over 1 MB', async () => {
    mockDocuments([record]);
    mockMarketData();
    vi.spyOn(tradingApi, 'instruments').mockResolvedValue([]);
    const create = vi.spyOn(tradingApi, 'createDocument');

    render(
      <TradingWatchlist instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />,
    );

    await screen.findByRole('button', { name: 'Select AAPL' });
    const content = '###Empty,LSE:NOPE';
    const nothing = new File([content], 'nothing.txt', { type: 'text/plain' });
    Object.defineProperty(nothing, 'text', { value: async () => content });
    fireEvent.change(screen.getByLabelText('Import watchlist file'), { target: { files: [nothing] } });
    expect(await screen.findByText('No symbols in nothing.txt could be found, so no watchlist was created; not found: LSE:NOPE.')).toBeInTheDocument();

    const huge = new File(['x'], 'huge.txt', { type: 'text/plain' });
    Object.defineProperty(huge, 'size', { value: 2_000_000 });
    fireEvent.change(screen.getByLabelText('Import watchlist file'), { target: { files: [huge] } });
    expect(await screen.findByText('huge.txt is too large to import (the limit is 1 MB).')).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });
});

function WatchlistWithShortcuts(props: Parameters<typeof TradingWatchlist>[0]) {
  useTradingCommandDispatcher();
  return <TradingWatchlist {...props} />;
}

describe('TradingWatchlist Alt+W (TVP-2.3)', () => {
  it('adds the chart symbol to the open list once', async () => {
    mockDocuments([record]);
    mockMarketData();
    const update = echoUpdates();
    render(<WatchlistWithShortcuts instruments={[apple, gameStop]} activeInstrumentId={gameStop.instrument_id} interval="1m" onSelect={vi.fn()} />);
    await screen.findByRole('button', { name: 'Select AAPL' });

    fireEvent.keyDown(document.body, { key: 'w', code: 'KeyW', altKey: true });
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][2]).toMatchObject({ instrumentIds: [apple.instrument_id, gameStop.instrument_id] });
    await screen.findByRole('button', { name: 'Select GME' });

    fireEvent.keyDown(document.body, { key: 'w', code: 'KeyW', altKey: true });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(update).toHaveBeenCalledTimes(1);
  });

  it('leaves the grid keys to the watchlist', async () => {
    mockDocuments([record]);
    mockMarketData();
    render(<WatchlistWithShortcuts instruments={[apple]} activeInstrumentId={apple.instrument_id} interval="1m" onSelect={vi.fn()} />);
    const grid = await screen.findByRole('treegrid', { name: 'Watchlist symbols' });
    const event = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
    grid.querySelector<HTMLElement>('[data-row-key]')!.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
