import { downloadBlob } from '../../shared/download';
import { binanceInstrumentIdFor } from './cryptoInstrumentDefaults';
import type { CanonicalInstrument } from './tradingTypes';
import {
  newWatchlistSectionId,
  watchlistPayloadFromItems,
  watchlistSymbolIds,
  type WatchlistItem,
  type WatchlistPayload,
} from './tradingWatchlistModel';

/**
 * Watchlist text files (TVP-5.3): comma-separated `EXCHANGE:SYMBOL` tokens,
 * with `###Name` tokens starting a section. Line breaks are accepted as
 * separators on import.
 */
export type WatchlistTextEntry =
  | { type: 'section'; name: string }
  | { type: 'symbol'; token: string; exchange: string | null; symbol: string };

const SECTION_MARKER = '###';

export function parseWatchlistText(text: string): WatchlistTextEntry[] {
  return text
    .split(/[,\r\n]+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token): WatchlistTextEntry => {
      if (token.startsWith(SECTION_MARKER)) {
        return { type: 'section', name: token.slice(SECTION_MARKER.length).trim() || 'Section' };
      }
      const colon = token.indexOf(':');
      return {
        type: 'symbol',
        token,
        exchange: colon > 0 ? token.slice(0, colon).trim().toUpperCase() : null,
        symbol: (colon > 0 ? token.slice(colon + 1) : token).trim().toUpperCase(),
      };
    });
}

/**
 * `VENUE:SYMBOL` for a listed instrument, from the catalog or, failing that,
 * from its id. Share classes are written with a dot (`NYSE:BRK.B`), the
 * common form in watchlist files; Omnix ids use a dash (`BRK-B`).
 */
export function exchangeSymbolFor(instrumentId: string, instrument?: CanonicalInstrument): string {
  const isEquity = instrument ? instrument.asset_class === 'equity' : instrumentId.startsWith('equity:');
  if (instrument) return `${instrument.venue}:${isEquity ? instrument.display_symbol.replace(/-/g, '.') : instrument.display_symbol}`;
  const parts = instrumentId.split(':');
  if (parts.length < 3) return instrumentId;
  const symbol = parts.at(-1) ?? '';
  return `${parts[1]}:${isEquity ? symbol.replace(/-/g, '.') : symbol.replace(/[-_/]/g, '')}`;
}

export function formatWatchlistText(items: readonly WatchlistItem[], symbolFor: (instrumentId: string) => string): string {
  return items
    .map((item) => item.type === 'section'
      ? `${SECTION_MARKER}${item.name.replace(/[,\r\n]+/g, ' ')}`
      : symbolFor(item.instrumentId))
    .join(',');
}

export function downloadWatchlistText(name: string, text: string): void {
  const fileName = name.replace(/[\\/:*?"<>|]+/g, '-').trim() || 'watchlist';
  downloadBlob(new Blob([text], { type: 'text/plain' }), `${fileName}.txt`);
}

/** Largest file and longest list an import accepts. */
export const MAX_WATCHLIST_IMPORT_BYTES = 1_000_000;
export const MAX_WATCHLIST_IMPORT_SYMBOLS = 2_000;

/** A file the importer refuses; the message is shown to the user. */
export class WatchlistImportError extends Error {}

/** `BRK.B`, `BRK-B` and `BRKB` all name the same share class. */
function symbolKey(symbol: string): string {
  return symbol.toUpperCase().replace(/[.\-_/]/g, '');
}

type SymbolEntry = Extract<WatchlistTextEntry, { type: 'symbol' }>;

/** Instruments by id and by every symbol spelling, for constant-time lookups. */
class InstrumentIndex {
  private readonly byId = new Map<string, CanonicalInstrument>();
  private readonly bySymbol = new Map<string, CanonicalInstrument[]>();

  constructor(instruments: readonly CanonicalInstrument[]) {
    this.add(instruments);
  }

  add(instruments: readonly CanonicalInstrument[]): void {
    for (const instrument of instruments) {
      if (this.byId.has(instrument.instrument_id)) continue;
      this.byId.set(instrument.instrument_id, instrument);
      for (const key of new Set([symbolKey(instrument.display_symbol), symbolKey(instrument.venue_symbol)])) {
        this.bySymbol.set(key, [...(this.bySymbol.get(key) ?? []), instrument]);
      }
    }
  }

  get(instrumentId: string): CanonicalInstrument | undefined {
    return this.byId.get(instrumentId);
  }

  matches(entry: SymbolEntry): CanonicalInstrument[] {
    return (this.bySymbol.get(symbolKey(entry.symbol)) ?? [])
      .filter((instrument) => !entry.exchange || instrument.venue.toUpperCase() === entry.exchange);
  }
}

type Resolution = { instrument: CanonicalInstrument } | 'ambiguous' | null;

function spotFirst(instruments: readonly CanonicalInstrument[]): CanonicalInstrument {
  // `BINANCE:BTCUSDT` names spot and perpetual alike; spot is the default.
  return instruments.find((instrument) => instrument.instrument_type !== 'perpetual') ?? instruments[0];
}

function spelledExactly(instrument: CanonicalInstrument, symbol: string): boolean {
  return instrument.display_symbol.toUpperCase() === symbol || instrument.venue_symbol.toUpperCase() === symbol;
}

/**
 * A full instrument id, else a symbol (on its venue when one is given).
 * An exact spelling wins over a match that only agrees once `.`, `-`, `_`
 * and `/` are ignored. Several candidates resolve only when one is already
 * in the user's watchlists, or when they are the spot and perpetual of one
 * venue (spot wins); anything else is ambiguous.
 */
function resolveEntry(index: InstrumentIndex, entry: SymbolEntry, preferredIds: ReadonlySet<string>): Resolution {
  const exact = index.get(entry.token);
  if (exact) return { instrument: exact };
  const matches = index.matches(entry);
  const spelled = matches.filter((instrument) => spelledExactly(instrument, entry.symbol));
  const candidates = spelled.length ? spelled : matches;
  if (candidates.length === 0) return null;
  if (candidates.length === 1) return { instrument: candidates[0] };
  const preferred = candidates.filter((instrument) => preferredIds.has(binanceInstrumentIdFor(instrument.instrument_id)));
  if (preferred.length) return { instrument: spotFirst(preferred) };
  const oneVenue = new Set(candidates.map((instrument) => instrument.venue)).size === 1;
  const nonPerpetual = candidates.filter((instrument) => instrument.instrument_type !== 'perpetual');
  return oneVenue && nonPerpetual.length === 1 ? { instrument: nonPerpetual[0] } : 'ambiguous';
}

export type WatchlistImportResult = {
  payload: WatchlistPayload;
  /** Instruments found through search, for the caller's catalog. */
  instruments: CanonicalInstrument[];
  notFound: string[];
  /** Symbols matching several instruments, none of them in the user's lists. */
  ambiguous: string[];
  /** Symbols beyond the search limit, never looked up. */
  notSearched: string[];
};

const SEARCH_BATCH = 5;

/**
 * Build a new watchlist from a text file in one pass. Symbols missing from
 * `catalog` are looked up with `search`, a few at a time and at most
 * `maxSearches` symbols. Throws `WatchlistImportError` for a file over
 * `MAX_WATCHLIST_IMPORT_SYMBOLS` symbols.
 */
export async function importWatchlistText(
  name: string,
  text: string,
  catalog: readonly CanonicalInstrument[],
  search: (query: string) => Promise<CanonicalInstrument[]>,
  options: { preferredInstrumentIds?: ReadonlySet<string>; maxSearches?: number } = {},
): Promise<WatchlistImportResult> {
  const preferredIds = options.preferredInstrumentIds ?? new Set<string>();
  const entries = parseWatchlistText(text);
  const symbols = entries.filter((entry): entry is SymbolEntry => entry.type === 'symbol');
  if (symbols.length > MAX_WATCHLIST_IMPORT_SYMBOLS) {
    throw new WatchlistImportError(
      `The file lists ${symbols.length.toLocaleString('en-US')} symbols; an import takes at most ${MAX_WATCHLIST_IMPORT_SYMBOLS.toLocaleString('en-US')}.`,
    );
  }
  const index = new InstrumentIndex(catalog);
  const found = new Set<CanonicalInstrument>();
  const unresolved = [...new Map(symbols
    .filter((entry) => resolveEntry(index, entry, preferredIds) === null)
    .map((entry) => [symbolKey(entry.symbol), entry])).values()];
  const maxSearches = options.maxSearches ?? 50;
  const missing = unresolved.slice(0, maxSearches);
  const skippedKeys = new Set(unresolved.slice(maxSearches).map((entry) => symbolKey(entry.symbol)));
  for (let start = 0; start < missing.length; start += SEARCH_BATCH) {
    const results = await Promise.all(missing.slice(start, start + SEARCH_BATCH)
      // Omnix symbols spell share classes with a dash.
      .map((entry) => search(entry.symbol.replace(/\./g, '-')).catch(() => [])));
    for (const result of results) {
      index.add(result);
      result.forEach((instrument) => found.add(instrument));
    }
  }

  const items: WatchlistItem[] = [];
  const used = new Set<CanonicalInstrument>();
  const notFound: string[] = [];
  const ambiguous: string[] = [];
  const notSearched: string[] = [];
  for (const entry of entries) {
    if (entry.type === 'section') {
      items.push({ type: 'section', id: newWatchlistSectionId(), name: entry.name, collapsed: false });
      continue;
    }
    const resolution = resolveEntry(index, entry, preferredIds);
    if (resolution === 'ambiguous') ambiguous.push(entry.token);
    else if (!resolution) (skippedKeys.has(symbolKey(entry.symbol)) ? notSearched : notFound).push(entry.token);
    else {
      if (found.has(resolution.instrument)) used.add(resolution.instrument);
      items.push({ type: 'symbol', instrumentId: binanceInstrumentIdFor(resolution.instrument.instrument_id) });
    }
  }
  return { payload: watchlistPayloadFromItems(name, items), instruments: [...used], notFound, ambiguous, notSearched };
}

/** True when the import found at least one symbol worth a new watchlist. */
export function importHasSymbols(result: WatchlistImportResult): boolean {
  return watchlistSymbolIds(result.payload).length > 0;
}
