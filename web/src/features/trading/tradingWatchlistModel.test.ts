import { describe, expect, it } from 'vitest';
import {
  addWatchlistSection,
  addWatchlistSymbols,
  flagListColor,
  flagListId,
  flaggedInstrumentIds,
  isNewerWatchlistPayload,
  moveWatchlistEntry,
  readWatchlistFlags,
  removeWatchlistSection,
  serializeWatchlist,
  setWatchlistFlags,
  upgradeWatchlistPayload,
  watchlistRows,
  watchlistSymbolIds,
  watchlistSymbolsNeedRewrite,
  type WatchlistPayload,
} from './tradingWatchlistModel';

const AAPL = 'equity:NASDAQ:AAPL';
const GME = 'equity:NYSE:GME';
const TSLA = 'equity:NASDAQ:TSLA';

const sectioned: WatchlistPayload = {
  schemaVersion: 2,
  name: 'List',
  items: [
    { type: 'symbol', instrumentId: AAPL },
    { type: 'section', id: 's1', name: 'Movers', collapsed: false },
    { type: 'symbol', instrumentId: GME },
    { type: 'symbol', instrumentId: TSLA },
  ],
};

describe('watchlist payload upgrade', () => {
  it('reads a version 1 payload as ordered symbol items', () => {
    expect(upgradeWatchlistPayload({ name: 'Old', instrumentIds: [AAPL, GME, AAPL, 7] })).toEqual({
      schemaVersion: 2,
      name: 'Old',
      items: [
        { type: 'symbol', instrumentId: AAPL },
        { type: 'symbol', instrumentId: GME },
      ],
    });
  });

  it('normalises legacy crypto ids and drops malformed items', () => {
    const upgraded = upgradeWatchlistPayload({
      schemaVersion: 2,
      name: 'Crypto',
      items: [
        { type: 'symbol', instrumentId: 'crypto:COINBASE:spot:BTC-USD' },
        { type: 'section', id: 's1', name: '  ', collapsed: 'yes' },
        { type: 'chart' },
        null,
      ],
    });
    expect(upgraded.items).toEqual([
      { type: 'symbol', instrumentId: 'crypto:BINANCE:spot:BTC-USDT' },
      { type: 'section', id: 's1', name: 'Section', collapsed: false },
    ]);
  });

  it('reports when reading changed the stored symbols', () => {
    expect(watchlistSymbolsNeedRewrite({ instrumentIds: [AAPL] })).toBe(false);
    expect(watchlistSymbolsNeedRewrite({ instrumentIds: [AAPL, AAPL] })).toBe(true);
    expect(watchlistSymbolsNeedRewrite({ instrumentIds: ['crypto:KRAKEN:spot:ETH-USD'] })).toBe(true);
  });

  it('falls back to an empty, named list for unreadable payloads', () => {
    expect(upgradeWatchlistPayload(undefined)).toEqual({ schemaVersion: 2, name: 'Watchlist', items: [] });
  });
});

describe('watchlist compatibility with other client versions', () => {
  it('writes the instrumentIds mirror so a version 1 client still sees every symbol', () => {
    expect(serializeWatchlist(sectioned)).toEqual({
      schemaVersion: 2,
      name: 'List',
      items: sectioned.items,
      instrumentIds: [AAPL, GME, TSLA],
    });
  });

  it('keeps the symbols of a list last saved by a version 1 client', () => {
    // A version 1 tab showed the mirror, added F, and saved its own shape.
    const savedByOldClient = { name: 'List', instrumentIds: [...(serializeWatchlist(sectioned).instrumentIds as string[]), 'equity:NYSE:F'] };
    expect(upgradeWatchlistPayload(savedByOldClient).items).toEqual([AAPL, GME, TSLA, 'equity:NYSE:F'].map((instrumentId) => ({ type: 'symbol', instrumentId })));
  });

  it('reads items only from a version 2 payload with an items array', () => {
    expect(upgradeWatchlistPayload({ schemaVersion: 2, name: 'Odd', items: 'nope', instrumentIds: [AAPL] }).items)
      .toEqual([{ type: 'symbol', instrumentId: AAPL }]);
    expect(upgradeWatchlistPayload({ name: 'Old', items: [{ type: 'symbol', instrumentId: GME }], instrumentIds: [AAPL] }).items)
      .toEqual([{ type: 'symbol', instrumentId: AAPL }]);
  });

  it('shows a newer version from its mirror and marks it read-only', () => {
    const newer = { schemaVersion: 3, name: 'Future', items: [{ type: 'widget' }], instrumentIds: [GME] };
    expect(isNewerWatchlistPayload(newer)).toBe(true);
    expect(isNewerWatchlistPayload(serializeWatchlist(sectioned))).toBe(false);
    expect(isNewerWatchlistPayload({ instrumentIds: [] })).toBe(false);
    expect(upgradeWatchlistPayload(newer)).toEqual({ schemaVersion: 2, name: 'Future', items: [{ type: 'symbol', instrumentId: GME }] });
  });
});

describe('watchlist items', () => {
  it('does not add a symbol twice', () => {
    expect(watchlistSymbolIds(addWatchlistSymbols(sectioned, [GME, 'equity:NYSE:F']))).toEqual([AAPL, GME, TSLA, 'equity:NYSE:F']);
  });

  it('inserts a section before a symbol or at the end', () => {
    expect(addWatchlistSection(sectioned, 'Watch', TSLA, 'new').items.map((item) => item.type === 'section' ? item.name : item.instrumentId))
      .toEqual([AAPL, 'Movers', GME, 'Watch', TSLA]);
    expect(addWatchlistSection(sectioned, 'Last', null, 'new').items.at(-1)).toEqual({ type: 'section', id: 'new', name: 'Last', collapsed: false });
  });

  const layout = (payload: WatchlistPayload) => payload.items.map((item) => item.type === 'section' ? `#${item.id}` : item.instrumentId);
  const symbol = (instrumentId: string) => ({ type: 'symbol' as const, instrumentId });
  const section = (id: string, collapsed = false) => ({ type: 'section' as const, id, name: id, collapsed });
  const list = (...items: WatchlistPayload['items']): WatchlistPayload => ({ schemaVersion: 2, name: 'L', items });

  it('moves a symbol across a header into the neighbouring section', () => {
    const moved = moveWatchlistEntry(sectioned, { type: 'symbol', instrumentId: GME }, -1);
    expect(watchlistRows(moved.items).map((row) => row.kind === 'symbol' ? `${row.instrumentId}@${row.sectionId}` : row.section.name))
      .toEqual([`${AAPL}@null`, `${GME}@null`, 'Movers', `${TSLA}@s1`]);
    expect(layout(moveWatchlistEntry(sectioned, { type: 'symbol', instrumentId: AAPL }, 1))).toEqual(['#s1', AAPL, GME, TSLA]);
    expect(moveWatchlistEntry(sectioned, { type: 'symbol', instrumentId: AAPL }, -1)).toBe(sectioned);
    expect(moveWatchlistEntry(sectioned, { type: 'symbol', instrumentId: 'equity:NYSE:F' }, 1)).toBe(sectioned);
  });

  it('moves a symbol past a collapsed section instead of into it', () => {
    const payload = list(symbol(AAPL), section('a', true), symbol(GME), section('b'), symbol(TSLA));
    expect(layout(moveWatchlistEntry(payload, { type: 'symbol', instrumentId: AAPL }, 1))).toEqual(['#a', GME, '#b', AAPL, TSLA]);
    expect(layout(moveWatchlistEntry(payload, { type: 'symbol', instrumentId: TSLA }, -1))).toEqual([AAPL, TSLA, '#a', GME, '#b']);
    const lastCollapsed = list(symbol(AAPL), section('a', true), symbol(GME));
    expect(moveWatchlistEntry(lastCollapsed, { type: 'symbol', instrumentId: AAPL }, 1)).toBe(lastCollapsed);
  });

  it('moves a section together with its symbols', () => {
    const payload = list(symbol(AAPL), section('a'), symbol(GME), section('b'), symbol(TSLA), symbol('equity:NYSE:F'));
    expect(layout(moveWatchlistEntry(payload, { type: 'section', id: 'b' }, -1))).toEqual([AAPL, '#b', TSLA, 'equity:NYSE:F', '#a', GME]);
    expect(layout(moveWatchlistEntry(payload, { type: 'section', id: 'a' }, 1))).toEqual([AAPL, '#b', TSLA, 'equity:NYSE:F', '#a', GME]);
    // Above the first section would pull the unsectioned symbols into it; past the last there is nowhere to go.
    expect(moveWatchlistEntry(payload, { type: 'section', id: 'a' }, -1)).toBe(payload);
    expect(moveWatchlistEntry(payload, { type: 'section', id: 'b' }, 1)).toBe(payload);
  });

  it('keeps the symbols of a removed section', () => {
    expect(watchlistSymbolIds(removeWatchlistSection(sectioned, 's1'))).toEqual([AAPL, GME, TSLA]);
  });
});

describe('watchlist rows', () => {
  it('hides the symbols of a collapsed section and counts them', () => {
    const collapsed = { ...sectioned, items: sectioned.items.map((item) => item.type === 'section' ? { ...item, collapsed: true } : item) };
    expect(watchlistRows(collapsed.items)).toEqual([
      { kind: 'symbol', instrumentId: AAPL, itemIndex: 0, sectionId: null },
      { kind: 'section', section: { type: 'section', id: 's1', name: 'Movers', collapsed: true }, itemIndex: 1, symbolCount: 2 },
    ]);
  });

  it('sorts within each section only', () => {
    const order = [TSLA, GME, AAPL];
    const rows = watchlistRows(sectioned.items, (left, right) => order.indexOf(left) - order.indexOf(right));
    expect(rows.map((row) => row.kind === 'symbol' ? row.instrumentId : row.section.id)).toEqual([AAPL, 's1', TSLA, GME]);
  });
});

describe('watchlist flags', () => {
  it('keeps one flag per symbol and preserves flag order when recoloured', () => {
    let flags = readWatchlistFlags({ schemaVersion: 1, flags: [{ instrumentId: AAPL, color: 'red' }, { instrumentId: AAPL, color: 'blue' }, { instrumentId: GME, color: 'pink' }] });
    expect(flags.flags).toEqual([{ instrumentId: AAPL, color: 'red' }]);
    flags = setWatchlistFlags(flags, [GME, TSLA], 'red');
    flags = setWatchlistFlags(flags, [AAPL], 'green');
    expect(flaggedInstrumentIds(flags, 'red')).toEqual([GME, TSLA]);
    expect(flaggedInstrumentIds(flags, 'green')).toEqual([AAPL]);
    expect(setWatchlistFlags(flags, [GME], null).flags.map((flag) => flag.instrumentId)).toEqual([AAPL, TSLA]);
  });

  it('round-trips generated flag list ids', () => {
    expect(flagListColor(flagListId('purple'))).toBe('purple');
    expect(flagListColor('flag:pink')).toBeNull();
    expect(flagListColor('default')).toBeNull();
  });
});
