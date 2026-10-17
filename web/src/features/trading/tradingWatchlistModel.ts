import { binanceInstrumentIdFor } from './cryptoInstrumentDefaults';

/**
 * Watchlist documents (TVP-5.1).
 *
 * Version 1 stored `{ name, instrumentIds }`. Version 2 stores an ordered list
 * of items, each a symbol or a named section header. A section owns the
 * symbols that follow it until the next section; symbols before the first
 * section belong to no section.
 *
 * Compatibility rules:
 * - every version 2 write also writes `instrumentIds`, the item symbols in
 *   order, so a client that only knows version 1 still shows the list;
 * - `items` count only when `schemaVersion === 2` and `items` is an array.
 *   A list last saved by a version 1 client has no `schemaVersion`, so its
 *   `instrumentIds` are read and its symbols kept (its sections are lost);
 * - a payload from a newer version (`schemaVersion > 2`) is shown from its
 *   `instrumentIds` and is read-only: this client never saves over it.
 */
export const WATCHLIST_SCHEMA_VERSION = 2;

export type WatchlistSymbolItem = { type: 'symbol'; instrumentId: string };
export type WatchlistSectionItem = { type: 'section'; id: string; name: string; collapsed: boolean };
export type WatchlistItem = WatchlistSymbolItem | WatchlistSectionItem;
export type WatchlistPayload = {
  schemaVersion: typeof WATCHLIST_SCHEMA_VERSION;
  name: string;
  items: WatchlistItem[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseItem(value: unknown): WatchlistItem[] {
  if (!isRecord(value)) return [];
  if (value.type === 'symbol' && typeof value.instrumentId === 'string' && value.instrumentId) {
    return [{ type: 'symbol', instrumentId: value.instrumentId }];
  }
  if (value.type === 'section' && typeof value.id === 'string' && value.id) {
    return [{
      type: 'section',
      id: value.id,
      name: typeof value.name === 'string' && value.name.trim() ? value.name : 'Section',
      collapsed: value.collapsed === true,
    }];
  }
  return [];
}

/** Canonical ids, one entry per symbol and per section id; the first occurrence wins. */
function normalizeItems(items: readonly WatchlistItem[]): WatchlistItem[] {
  const seenSymbols = new Set<string>();
  const seenSections = new Set<string>();
  const result: WatchlistItem[] = [];
  for (const item of items) {
    if (item.type === 'symbol') {
      const instrumentId = binanceInstrumentIdFor(item.instrumentId);
      if (seenSymbols.has(instrumentId)) continue;
      seenSymbols.add(instrumentId);
      result.push({ type: 'symbol', instrumentId });
    } else {
      if (seenSections.has(item.id)) continue;
      seenSections.add(item.id);
      result.push(item);
    }
  }
  return result;
}

/** True for a payload written by a newer version of Omnix; it is read-only here. */
export function isNewerWatchlistPayload(raw: unknown): boolean {
  return isRecord(raw) && typeof raw.schemaVersion === 'number' && raw.schemaVersion > WATCHLIST_SCHEMA_VERSION;
}

function rawItems(raw: unknown): WatchlistItem[] {
  if (!isRecord(raw)) return [];
  if (raw.schemaVersion === WATCHLIST_SCHEMA_VERSION && Array.isArray(raw.items)) {
    return raw.items.flatMap(parseItem);
  }
  return Array.isArray(raw.instrumentIds)
    ? raw.instrumentIds
      .filter((item): item is string => typeof item === 'string' && item.length > 0)
      .map((instrumentId) => ({ type: 'symbol', instrumentId }))
    : [];
}

/** Read any stored watchlist payload (version 1, 2 or newer) as version 2. */
export function upgradeWatchlistPayload(raw: unknown): WatchlistPayload {
  const name = isRecord(raw) && typeof raw.name === 'string' ? raw.name : 'Watchlist';
  return { schemaVersion: WATCHLIST_SCHEMA_VERSION, name, items: normalizeItems(rawItems(raw)) };
}

/** True when reading the payload changed its symbols (legacy ids or duplicates). */
export function watchlistSymbolsNeedRewrite(raw: unknown): boolean {
  const before = rawItems(raw).filter(isSymbolItem).map((item) => item.instrumentId);
  const after = watchlistSymbolIds(upgradeWatchlistPayload(raw));
  return before.length !== after.length || before.some((instrumentId, index) => instrumentId !== after[index]);
}

/** The stored form: version 2 items plus the `instrumentIds` mirror for version 1 readers. */
export function serializeWatchlist(payload: WatchlistPayload): Record<string, unknown> {
  return {
    schemaVersion: WATCHLIST_SCHEMA_VERSION,
    name: payload.name,
    items: payload.items,
    instrumentIds: watchlistSymbolIds(payload),
  };
}

export function newWatchlistPayload(name: string, instrumentIds: readonly string[] = []): WatchlistPayload {
  return watchlistPayloadFromItems(name, instrumentIds.map((instrumentId) => ({ type: 'symbol', instrumentId })));
}

/** A list built in one pass; duplicate symbols and section ids are dropped. */
export function watchlistPayloadFromItems(name: string, items: readonly WatchlistItem[]): WatchlistPayload {
  return { schemaVersion: WATCHLIST_SCHEMA_VERSION, name, items: normalizeItems(items) };
}

export function isSymbolItem(item: WatchlistItem): item is WatchlistSymbolItem {
  return item.type === 'symbol';
}

export function watchlistSymbolIds(payload: WatchlistPayload): string[] {
  return payload.items.filter(isSymbolItem).map((item) => item.instrumentId);
}

export function addWatchlistSymbols(payload: WatchlistPayload, instrumentIds: readonly string[]): WatchlistPayload {
  return {
    ...payload,
    items: normalizeItems([
      ...payload.items,
      ...instrumentIds.map((instrumentId): WatchlistItem => ({ type: 'symbol', instrumentId })),
    ]),
  };
}

export function removeWatchlistSymbols(payload: WatchlistPayload, instrumentIds: ReadonlySet<string>): WatchlistPayload {
  return {
    ...payload,
    items: payload.items.filter((item) => !(item.type === 'symbol' && instrumentIds.has(item.instrumentId))),
  };
}

export type WatchlistEntryKey = { type: 'symbol'; instrumentId: string } | { type: 'section'; id: string };

function entryIndex(items: readonly WatchlistItem[], key: WatchlistEntryKey): number {
  return items.findIndex((item) => key.type === 'symbol'
    ? item.type === 'symbol' && item.instrumentId === key.instrumentId
    : item.type === 'section' && item.id === key.id);
}

function nextSectionIndex(items: readonly WatchlistItem[], after: number): number {
  for (let index = after + 1; index < items.length; index += 1) if (items[index].type === 'section') return index;
  return items.length;
}

function previousSectionIndex(items: readonly WatchlistItem[], before: number): number {
  for (let index = before - 1; index >= 0; index -= 1) if (items[index].type === 'section') return index;
  return -1;
}

function isCollapsedSection(item: WatchlistItem | undefined): boolean {
  return item?.type === 'section' && item.collapsed;
}

/** A section moves with its symbols, past the neighbouring section. */
function moveSection(items: readonly WatchlistItem[], index: number, direction: -1 | 1): WatchlistItem[] | null {
  const end = nextSectionIndex(items, index);
  if (direction < 0) {
    // Moving above the symbols before the first section would make them part of this section.
    const previous = previousSectionIndex(items, index);
    if (previous < 0) return null;
    return [...items.slice(0, previous), ...items.slice(index, end), ...items.slice(previous, index), ...items.slice(end)];
  }
  if (end >= items.length) return null;
  const nextEnd = nextSectionIndex(items, end);
  return [...items.slice(0, index), ...items.slice(end, nextEnd), ...items.slice(index, end), ...items.slice(nextEnd)];
}

/**
 * A symbol moves one visible row; across a header it changes section. It
 * never lands inside a collapsed section, where it would disappear: it moves
 * past the collapsed section instead, or stays put when there is no place.
 */
function moveSymbol(items: readonly WatchlistItem[], index: number, direction: -1 | 1): WatchlistItem[] | null {
  const neighbour = items[index + direction];
  if (!neighbour) return null;
  if (neighbour.type === 'symbol') {
    const next = [...items];
    [next[index], next[index + direction]] = [neighbour, items[index]];
    return next;
  }
  const symbol = items[index];
  const rest = items.filter((_, position) => position !== index);
  let insertAt: number;
  if (direction < 0) {
    insertAt = index - 1;
    for (let header = previousSectionIndex(rest, insertAt); header >= 0 && isCollapsedSection(rest[header]); header = previousSectionIndex(rest, insertAt)) {
      insertAt = header;
    }
  } else {
    let header = index;
    while (isCollapsedSection(rest[header])) {
      header = nextSectionIndex(rest, header);
      if (header >= rest.length) return null;
    }
    insertAt = header + 1;
  }
  return [...rest.slice(0, insertAt), symbol, ...rest.slice(insertAt)];
}

/** Move a symbol or a section one step; unchanged when it cannot move that way. */
export function moveWatchlistEntry(payload: WatchlistPayload, key: WatchlistEntryKey, direction: -1 | 1): WatchlistPayload {
  const index = entryIndex(payload.items, key);
  if (index < 0) return payload;
  const items = key.type === 'section'
    ? moveSection(payload.items, index, direction)
    : moveSymbol(payload.items, index, direction);
  return items ? { ...payload, items } : payload;
}

export function newWatchlistSectionId(): string {
  return `section-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Insert a section header before `beforeInstrumentId`, or at the end. */
export function addWatchlistSection(
  payload: WatchlistPayload,
  name: string,
  beforeInstrumentId?: string | null,
  id: string = newWatchlistSectionId(),
): WatchlistPayload {
  const section: WatchlistSectionItem = { type: 'section', id, name, collapsed: false };
  const index = beforeInstrumentId
    ? payload.items.findIndex((item) => item.type === 'symbol' && item.instrumentId === beforeInstrumentId)
    : -1;
  const items = [...payload.items];
  items.splice(index < 0 ? items.length : index, 0, section);
  return { ...payload, items };
}

export function updateWatchlistSection(
  payload: WatchlistPayload,
  sectionId: string,
  change: Partial<Pick<WatchlistSectionItem, 'name' | 'collapsed'>>,
): WatchlistPayload {
  return {
    ...payload,
    items: payload.items.map((item) => item.type === 'section' && item.id === sectionId ? { ...item, ...change } : item),
  };
}

/** Remove a header only; its symbols join the section above. */
export function removeWatchlistSection(payload: WatchlistPayload, sectionId: string): WatchlistPayload {
  return { ...payload, items: payload.items.filter((item) => !(item.type === 'section' && item.id === sectionId)) };
}

export type WatchlistRow =
  | { kind: 'section'; section: WatchlistSectionItem; itemIndex: number; symbolCount: number }
  | { kind: 'symbol'; instrumentId: string; itemIndex: number; sectionId: string | null };

/**
 * The rows the watchlist shows: section headers, and the symbols of every
 * expanded section. A sort orders symbols within their own section, so
 * sorting never moves a symbol to another section.
 */
export function watchlistRows(
  items: readonly WatchlistItem[],
  compare?: (left: string, right: string) => number,
): WatchlistRow[] {
  type Group = { header: { section: WatchlistSectionItem; itemIndex: number } | null; symbols: Array<{ instrumentId: string; itemIndex: number }> };
  const groups: Group[] = [{ header: null, symbols: [] }];
  items.forEach((item, itemIndex) => {
    if (item.type === 'section') groups.push({ header: { section: item, itemIndex }, symbols: [] });
    else groups[groups.length - 1].symbols.push({ instrumentId: item.instrumentId, itemIndex });
  });
  const rows: WatchlistRow[] = [];
  for (const group of groups) {
    const sectionId = group.header?.section.id ?? null;
    if (group.header) {
      rows.push({ kind: 'section', section: group.header.section, itemIndex: group.header.itemIndex, symbolCount: group.symbols.length });
      if (group.header.section.collapsed) continue;
    }
    const symbols = compare
      ? [...group.symbols].sort((left, right) => compare(left.instrumentId, right.instrumentId) || left.itemIndex - right.itemIndex)
      : group.symbols;
    for (const symbol of symbols) rows.push({ kind: 'symbol', instrumentId: symbol.instrumentId, itemIndex: symbol.itemIndex, sectionId });
  }
  return rows;
}

/* Colour flags (TVP-5.1): one user-level document shared by every watchlist. */

export const WATCHLIST_FLAG_SCHEMA_VERSION = 1;
export const WATCHLIST_FLAGS_RECORD_ID = 'default';
export const WATCHLIST_FLAG_COLORS = ['red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple'] as const;
export type WatchlistFlagColor = (typeof WATCHLIST_FLAG_COLORS)[number];
export type WatchlistFlag = { instrumentId: string; color: WatchlistFlagColor };
export type WatchlistFlagsPayload = { schemaVersion: typeof WATCHLIST_FLAG_SCHEMA_VERSION; flags: WatchlistFlag[] };

export const WATCHLIST_FLAG_LABELS: Record<WatchlistFlagColor, string> = {
  red: 'Red',
  orange: 'Orange',
  yellow: 'Yellow',
  green: 'Green',
  cyan: 'Cyan',
  blue: 'Blue',
  purple: 'Purple',
};

export function isWatchlistFlagColor(value: unknown): value is WatchlistFlagColor {
  return typeof value === 'string' && (WATCHLIST_FLAG_COLORS as readonly string[]).includes(value);
}

export function emptyWatchlistFlags(): WatchlistFlagsPayload {
  return { schemaVersion: WATCHLIST_FLAG_SCHEMA_VERSION, flags: [] };
}

export function readWatchlistFlags(raw: unknown): WatchlistFlagsPayload {
  const flags = isRecord(raw) && Array.isArray(raw.flags) ? raw.flags : [];
  const seen = new Set<string>();
  const result: WatchlistFlag[] = [];
  for (const flag of flags) {
    if (!isRecord(flag) || typeof flag.instrumentId !== 'string' || !isWatchlistFlagColor(flag.color)) continue;
    const instrumentId = binanceInstrumentIdFor(flag.instrumentId);
    if (seen.has(instrumentId)) continue;
    seen.add(instrumentId);
    result.push({ instrumentId, color: flag.color });
  }
  return { schemaVersion: WATCHLIST_FLAG_SCHEMA_VERSION, flags: result };
}

/** A symbol has at most one flag; `null` clears it. A recoloured symbol keeps its place. */
export function setWatchlistFlags(
  payload: WatchlistFlagsPayload,
  instrumentIds: readonly string[],
  color: WatchlistFlagColor | null,
): WatchlistFlagsPayload {
  const targets = new Set(instrumentIds.map(binanceInstrumentIdFor));
  const flags = payload.flags.flatMap((flag) => {
    if (!targets.has(flag.instrumentId)) return [flag];
    targets.delete(flag.instrumentId);
    return color ? [{ ...flag, color }] : [];
  });
  if (color) for (const instrumentId of targets) flags.push({ instrumentId, color });
  return { ...payload, flags };
}

export function watchlistFlagMap(payload: WatchlistFlagsPayload): Map<string, WatchlistFlagColor> {
  return new Map(payload.flags.map((flag) => [flag.instrumentId, flag.color]));
}

/** The generated list for one colour, in the order the symbols were flagged. */
export function flaggedInstrumentIds(payload: WatchlistFlagsPayload, color: WatchlistFlagColor): string[] {
  return payload.flags.filter((flag) => flag.color === color).map((flag) => flag.instrumentId);
}

const FLAG_LIST_PREFIX = 'flag:';

export function flagListId(color: WatchlistFlagColor): string {
  return `${FLAG_LIST_PREFIX}${color}`;
}

export function flagListColor(listId: string): WatchlistFlagColor | null {
  const color = listId.startsWith(FLAG_LIST_PREFIX) ? listId.slice(FLAG_LIST_PREFIX.length) : null;
  return isWatchlistFlagColor(color) ? color : null;
}
