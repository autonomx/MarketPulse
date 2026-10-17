import { useRef, useState, type ChangeEvent } from 'react';
import { tradingApi } from './tradingApi';
import type { CanonicalInstrument } from './tradingTypes';
import { watchlistSymbolIds, type WatchlistPayload } from './tradingWatchlistModel';
import {
  MAX_WATCHLIST_IMPORT_BYTES,
  WatchlistImportError,
  importHasSymbols,
  importWatchlistText,
  type WatchlistImportResult,
} from './tradingWatchlistTransfer';

function listed(label: string, tokens: readonly string[]): string {
  if (tokens.length === 0) return '';
  const shown = tokens.slice(0, 10).join(', ');
  return `; ${label}: ${shown}${tokens.length > 10 ? ` and ${tokens.length - 10} more` : ''}`;
}

function importNotice(result: WatchlistImportResult): string {
  const count = watchlistSymbolIds(result.payload).length;
  const imported = `Imported ${count} ${count === 1 ? 'symbol' : 'symbols'}`;
  return `${imported}${listed('not found', result.notFound)}${listed('ambiguous, add the exchange', result.ambiguous)}${listed('not searched (search limit reached)', result.notSearched)}.`;
}

/**
 * Import a `.txt` watchlist file (TVP-5.3) as a new watchlist named after the
 * file. `notice` reports the result, including symbols that were not found or
 * were ambiguous. Nothing is created when no symbol resolves.
 */
export function useTradingWatchlistImport({
  catalog,
  preferredInstrumentIds,
  create,
  onInstrumentsFound,
}: {
  catalog: readonly CanonicalInstrument[];
  /** Symbols already in the user's watchlists; they settle a bare symbol listed on several venues. */
  preferredInstrumentIds: ReadonlySet<string>;
  create: (payload: WatchlistPayload) => Promise<boolean>;
  onInstrumentsFound: (instruments: CanonicalInstrument[]) => void;
}) {
  const [notice, setNotice] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const importFile = async (file: File) => {
    if (file.size > MAX_WATCHLIST_IMPORT_BYTES) {
      setNotice(`${file.name} is too large to import (the limit is 1 MB).`);
      return;
    }
    setNotice(`Importing ${file.name}…`);
    try {
      const result = await importWatchlistText(
        file.name.replace(/\.txt$/i, '').trim() || 'Imported watchlist',
        await file.text(),
        catalog,
        (query) => tradingApi.instruments(query),
        { preferredInstrumentIds },
      );
      onInstrumentsFound(result.instruments);
      if (!importHasSymbols(result)) {
        setNotice(`No symbols in ${file.name} could be found, so no watchlist was created${listed('not found', result.notFound)}${listed('ambiguous', result.ambiguous)}${listed('not searched (search limit reached)', result.notSearched)}.`);
        return;
      }
      const created = await create(result.payload);
      setNotice(created ? importNotice(result) : 'The imported watchlist could not be saved.');
    } catch (error) {
      setNotice(error instanceof WatchlistImportError ? error.message : 'The watchlist file could not be read.');
    }
  };

  const onInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Clearing the value lets the same file be imported again.
    event.target.value = '';
    if (file) void importFile(file);
  };

  return { notice, inputRef, onInputChange, openFilePicker: () => inputRef.current?.click() };
}
