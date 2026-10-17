import { describe, expect, it, vi } from 'vitest';
import type { CanonicalInstrument } from './tradingTypes';
import * as watchlistModel from './tradingWatchlistModel';
import {
  MAX_WATCHLIST_IMPORT_SYMBOLS,
  WatchlistImportError,
  exchangeSymbolFor,
  formatWatchlistText,
  importHasSymbols,
  importWatchlistText,
  parseWatchlistText,
} from './tradingWatchlistTransfer';

// Wrap the list builders so a test can count how often the import rebuilds a list.
vi.mock('./tradingWatchlistModel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./tradingWatchlistModel')>();
  return {
    ...actual,
    watchlistPayloadFromItems: vi.fn(actual.watchlistPayloadFromItems),
    addWatchlistSymbols: vi.fn(actual.addWatchlistSymbols),
    addWatchlistSection: vi.fn(actual.addWatchlistSection),
  };
});

function instrument(instrumentId: string, venue: string, displaySymbol: string, venueSymbol = displaySymbol, type = 'equity'): CanonicalInstrument {
  return {
    instrument_id: instrumentId,
    asset_class: type === 'equity' ? 'equity' : 'crypto',
    instrument_type: type,
    venue,
    venue_symbol: venueSymbol,
    display_symbol: displaySymbol,
    base_currency: null,
    quote_currency: 'USD',
    exchange_timezone: 'UTC',
    session_calendar: '24x7',
    price_scale: 100,
    minimum_tick: '0.01',
    status: 'active',
  } as CanonicalInstrument;
}

const apple = instrument('equity:NASDAQ:AAPL', 'NASDAQ', 'AAPL');
const btcSpot = instrument('crypto:BINANCE:spot:BTC-USDT', 'BINANCE', 'BTCUSDT', 'BTC-USDT', 'spot');
const btcPerp = instrument('crypto:BINANCE:perpetual:BTC-USDT', 'BINANCE', 'BTCUSDT', 'BTC-USDT', 'perpetual');

describe('watchlist text files', () => {
  it('parses symbols and section markers separated by commas or lines', () => {
    expect(parseWatchlistText('###Tech, NASDAQ:aapl,\nTSLA\r\n###  ,BINANCE:BTCUSDT,,')).toEqual([
      { type: 'section', name: 'Tech' },
      { type: 'symbol', token: 'NASDAQ:aapl', exchange: 'NASDAQ', symbol: 'AAPL' },
      { type: 'symbol', token: 'TSLA', exchange: null, symbol: 'TSLA' },
      { type: 'section', name: 'Section' },
      { type: 'symbol', token: 'BINANCE:BTCUSDT', exchange: 'BINANCE', symbol: 'BTCUSDT' },
    ]);
  });

  it('exports sections and EXCHANGE:SYMBOL tokens in list order', () => {
    const text = formatWatchlistText([
      { type: 'symbol', instrumentId: apple.instrument_id },
      { type: 'section', id: 's', name: 'Crypto, majors', collapsed: true },
      { type: 'symbol', instrumentId: btcSpot.instrument_id },
      { type: 'symbol', instrumentId: 'equity:NYSE:BRK-B' },
    ], (instrumentId) => exchangeSymbolFor(instrumentId, [apple, btcSpot].find((item) => item.instrument_id === instrumentId)));
    expect(text).toBe('NASDAQ:AAPL,###Crypto  majors,BINANCE:BTCUSDT,NYSE:BRK.B');
  });

  it('round-trips an exported list through import', async () => {
    const search = vi.fn(async () => []);
    const result = await importWatchlistText('Copy', 'NASDAQ:AAPL,###Crypto,BINANCE:BTCUSDT', [apple, btcPerp, btcSpot], search);
    expect(result.payload).toEqual({
      schemaVersion: 2,
      name: 'Copy',
      items: [
        { type: 'symbol', instrumentId: apple.instrument_id },
        { type: 'section', id: expect.any(String), name: 'Crypto', collapsed: false },
        { type: 'symbol', instrumentId: btcSpot.instrument_id },
      ],
    });
    expect(result.notFound).toEqual([]);
    expect(result.ambiguous).toEqual([]);
    expect(search).not.toHaveBeenCalled();
  });

  it('searches for unknown symbols once each and reports the ones it cannot find', async () => {
    const msft = instrument('equity:NASDAQ:MSFT', 'NASDAQ', 'MSFT');
    const search = vi.fn(async (query: string) => (query === 'MSFT' ? [msft] : []));
    const result = await importWatchlistText('Mixed', 'NASDAQ:MSFT,NASDAQ:MSFT,NYSE:MSFT,LSE:NOPE,equity:NASDAQ:AAPL', [apple], search);
    expect(search.mock.calls.map(([query]) => query)).toEqual(['MSFT', 'NOPE']);
    expect(result.payload.items).toEqual([
      { type: 'symbol', instrumentId: msft.instrument_id },
      { type: 'symbol', instrumentId: apple.instrument_id },
    ]);
    expect(result.instruments).toEqual([msft]);
    expect(result.notFound).toEqual(['NYSE:MSFT', 'LSE:NOPE']);
  });

  it('matches share classes written with a dot, a dash or nothing', async () => {
    const berkshire = instrument('equity:NYSE:BRK-B', 'NYSE', 'BRK-B');
    expect(exchangeSymbolFor(berkshire.instrument_id, berkshire)).toBe('NYSE:BRK.B');
    const result = await importWatchlistText('Classes', 'NYSE:BRK.B,NYSE:BRK-B,BRKB', [berkshire], vi.fn(async () => []));
    expect(result.payload.items).toEqual([{ type: 'symbol', instrumentId: berkshire.instrument_id }]);
    expect(result.notFound).toEqual([]);
  });

  it('prefers an exact spelling and reports a same-venue collision as ambiguous', async () => {
    const classB = instrument('equity:NYSE:BRK-B', 'NYSE', 'BRK-B');
    const otherTicker = instrument('equity:NYSE:BRKB', 'NYSE', 'BRKB');
    const result = await importWatchlistText('Exact', 'NYSE:BRKB,NYSE:BRK-B,NYSE:BRK.B', [classB, otherTicker], vi.fn(async () => []));
    expect(result.payload.items).toEqual([
      { type: 'symbol', instrumentId: otherTicker.instrument_id },
      { type: 'symbol', instrumentId: classB.instrument_id },
    ]);
    expect(result.ambiguous).toEqual(['NYSE:BRK.B']);
  });

  it('reports symbols beyond the search limit as not searched', async () => {
    const search = vi.fn(async () => []);
    const result = await importWatchlistText('Limit', 'LSE:ONE,LSE:TWO,LSE:THREE', [], search, { maxSearches: 1 });
    expect(search).toHaveBeenCalledTimes(1);
    expect(result.notFound).toEqual(['LSE:ONE']);
    expect(result.notSearched).toEqual(['LSE:TWO', 'LSE:THREE']);
  });

  it('searches for a missing share class with the Omnix dash spelling', async () => {
    const search = vi.fn(async () => []);
    await importWatchlistText('Classes', 'NYSE:BF.B', [], search);
    expect(search).toHaveBeenCalledWith('BF-B');
  });

  it('resolves a bare symbol on several venues only through the user\'s lists', async () => {
    const nasdaqShop = instrument('equity:NASDAQ:SHOP', 'NASDAQ', 'SHOP');
    const torontoShop = instrument('equity:TSX:SHOP', 'TSX', 'SHOP');
    const catalog = [nasdaqShop, torontoShop];
    const ambiguous = await importWatchlistText('Shop', 'SHOP,TSX:SHOP', catalog, vi.fn(async () => []));
    expect(ambiguous.ambiguous).toEqual(['SHOP']);
    expect(ambiguous.payload.items).toEqual([{ type: 'symbol', instrumentId: torontoShop.instrument_id }]);
    const preferred = await importWatchlistText('Shop', 'SHOP', catalog, vi.fn(async () => []), {
      preferredInstrumentIds: new Set([nasdaqShop.instrument_id]),
    });
    expect(preferred.payload.items).toEqual([{ type: 'symbol', instrumentId: nasdaqShop.instrument_id }]);
  });

  it('refuses a file over the symbol limit and reports a file with nothing found', async () => {
    const tooMany = Array.from({ length: MAX_WATCHLIST_IMPORT_SYMBOLS + 1 }, (_, index) => `NASDAQ:S${index}`).join(',');
    await expect(importWatchlistText('Big', tooMany, [], vi.fn(async () => []))).rejects.toBeInstanceOf(WatchlistImportError);
    const empty = await importWatchlistText('Nothing', '###Only a section,LSE:NOPE', [], vi.fn(async () => []));
    expect(importHasSymbols(empty)).toBe(false);
  });

  it('builds a long list in one pass', async () => {
    const catalog = Array.from({ length: MAX_WATCHLIST_IMPORT_SYMBOLS }, (_, index) => instrument(`equity:NASDAQ:S${index}`, 'NASDAQ', `S${index}`));
    const text = catalog.map((item) => `NASDAQ:${item.display_symbol}`).join(',');
    vi.mocked(watchlistModel.watchlistPayloadFromItems).mockClear();
    vi.mocked(watchlistModel.addWatchlistSymbols).mockClear();
    vi.mocked(watchlistModel.addWatchlistSection).mockClear();
    const search = vi.fn(async () => []);
    const result = await importWatchlistText('Long', `###Top,${text}`, catalog, search);
    expect(result.payload.items).toHaveLength(MAX_WATCHLIST_IMPORT_SYMBOLS + 1);
    // One build of the whole list, never a rebuild per symbol or section.
    expect(watchlistModel.watchlistPayloadFromItems).toHaveBeenCalledTimes(1);
    expect(watchlistModel.addWatchlistSymbols).not.toHaveBeenCalled();
    expect(watchlistModel.addWatchlistSection).not.toHaveBeenCalled();
    expect(search).not.toHaveBeenCalled();
  });
});
