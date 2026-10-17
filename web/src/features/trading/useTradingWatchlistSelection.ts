import { useEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type MouseEvent } from 'react';
import type { WatchlistRow } from './tradingWatchlistModel';

/**
 * Watchlist keyboard navigation and row selection (TVP-5.3).
 *
 * The list is an ARIA tree grid: sections are parent rows, symbols their
 * children. One element holds the tab stop (roving tabindex): a row, or a
 * cell inside the focused row. Keys:
 * - Up/Down and Space/Shift+Space move between rows; landing on a symbol
 *   selects it and shows it on the chart;
 * - Shift+Up/Down extend the selection over symbols; Ctrl/Cmd+A selects all;
 * - Right expands a collapsed section, else enters the row's cells and moves
 *   right; Left moves left, leaves the cells, collapses an expanded section,
 *   or goes from a symbol to its section;
 * - Home/End go to the first/last cell in a row (or first/last row from a
 *   row); Ctrl+Home/End go to the first/last row;
 * - Enter on a row shows its symbol or toggles its section; in a cell, Enter
 *   and Space press the cell's button.
 *
 * Keys are read by the watchlist's own handler because Omnix has no shared
 * shortcut layer yet; `watchlistKeyCommand` and `applyWatchlistCommand` are
 * pure so the selection commands can move to the command layer (TVP-0.3).
 */
export type WatchlistSelection = { ids: readonly string[]; cursor: string | null; anchor: string | null };
export type WatchlistKeyCommand = 'next' | 'previous' | 'extendNext' | 'extendPrevious' | 'selectAll';

type KeyLike = Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey'>;

const EMPTY_SELECTION: WatchlistSelection = { ids: [], cursor: null, anchor: null };

export function watchlistKeyCommand(event: KeyLike): WatchlistKeyCommand | null {
  if (event.altKey) return null;
  if (event.ctrlKey || event.metaKey) {
    return !event.shiftKey && event.key.toLowerCase() === 'a' ? 'selectAll' : null;
  }
  switch (event.key) {
    case 'ArrowDown':
      return event.shiftKey ? 'extendNext' : 'next';
    case 'ArrowUp':
      return event.shiftKey ? 'extendPrevious' : 'previous';
    case ' ':
    case 'Spacebar':
      return event.shiftKey ? 'previous' : 'next';
    default:
      return null;
  }
}

/** Keys typed into a field or an open menu belong to that control. */
export function isWatchlistKeyTargetIgnored(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable
    || target.tagName === 'INPUT'
    || target.tagName === 'TEXTAREA'
    || target.tagName === 'SELECT'
    || target.closest('[role="menu"]') != null;
}

function rangeIds(order: readonly string[], from: string, to: string): string[] {
  const [start, end] = [order.indexOf(from), order.indexOf(to)].sort((left, right) => left - right);
  return order.slice(start, end + 1);
}

/**
 * Apply a command to the selection over the visible symbols in `order`.
 * Moving starts from the cursor, else from `startFrom` (the chart's symbol).
 * `show` is the symbol to open on the chart, if any.
 */
export function applyWatchlistCommand(
  selection: WatchlistSelection,
  order: readonly string[],
  command: WatchlistKeyCommand,
  startFrom?: string | null,
): { selection: WatchlistSelection; show: string | null } {
  if (order.length === 0) return { selection, show: null };
  const cursor = selection.cursor && order.includes(selection.cursor) ? selection.cursor : null;
  if (command === 'selectAll') {
    const target = cursor ?? order[0];
    return { selection: { ids: [...order], cursor: target, anchor: target }, show: null };
  }
  const start = cursor ?? (startFrom && order.includes(startFrom) ? startFrom : null);
  const step = command === 'next' || command === 'extendNext' ? 1 : -1;
  const index = start == null ? -1 : order.indexOf(start);
  const nextIndex = index < 0
    ? (step > 0 ? 0 : order.length - 1)
    : Math.min(order.length - 1, Math.max(0, index + step));
  const next = order[nextIndex];
  if (command === 'next' || command === 'previous') {
    return { selection: { ids: [next], cursor: next, anchor: next }, show: next };
  }
  const anchor = selection.anchor && order.includes(selection.anchor) ? selection.anchor : start ?? next;
  return { selection: { ids: rangeIds(order, anchor, next), cursor: next, anchor }, show: null };
}

/** A row click: plain selects and shows, Shift extends, Ctrl/Cmd toggles. */
export function clickWatchlistRow(
  selection: WatchlistSelection,
  order: readonly string[],
  instrumentId: string,
  modifiers: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean },
): { selection: WatchlistSelection; show: string | null } {
  if (modifiers.shiftKey && selection.anchor && order.includes(selection.anchor)) {
    return { selection: { ids: rangeIds(order, selection.anchor, instrumentId), cursor: instrumentId, anchor: selection.anchor }, show: null };
  }
  if (modifiers.ctrlKey || modifiers.metaKey) {
    const ids = selection.ids.includes(instrumentId)
      ? selection.ids.filter((id) => id !== instrumentId)
      : [...selection.ids, instrumentId];
    return { selection: { ids, cursor: instrumentId, anchor: instrumentId }, show: null };
  }
  return { selection: { ids: [instrumentId], cursor: instrumentId, anchor: instrumentId }, show: instrumentId };
}

/** One visible row of the tree grid. */
export type WatchlistGridRow = {
  key: string;
  /** Set for symbol rows. */
  instrumentId: string | null;
  /** Set for section rows. */
  sectionId: string | null;
  collapsed: boolean;
  /** The section row a symbol belongs to. */
  parentKey: string | null;
};

/** Where the tab stop is: a row (`cell: null`) or a cell of that row. */
export type WatchlistGridFocus = { rowKey: string; cell: number | null };

export function watchlistSymbolRowKey(instrumentId: string): string {
  return `symbol:${instrumentId}`;
}

export function watchlistSectionRowKey(sectionId: string): string {
  return `section:${sectionId}`;
}

function focusFromElement(element: Element | null): WatchlistGridFocus | null {
  const row = element?.closest<HTMLElement>('[data-row-key]');
  if (!row?.dataset.rowKey) return null;
  const cell = element?.closest<HTMLElement>('[data-cell]');
  return { rowKey: row.dataset.rowKey, cell: cell && row.contains(cell) ? Number(cell.dataset.cell) : null };
}

const NO_MODIFIERS = { shiftKey: false, ctrlKey: false, metaKey: false };

export function useTradingWatchlistSelection({
  rows,
  activeInstrumentId,
  onShow,
  onToggleSection,
}: {
  rows: readonly WatchlistGridRow[];
  activeInstrumentId: string;
  onShow: (instrumentId: string) => void;
  onToggleSection: (sectionId: string, collapsed: boolean) => void;
}) {
  const [selection, setSelection] = useState<WatchlistSelection>(EMPTY_SELECTION);
  const [focus, setFocus] = useState<WatchlistGridFocus | null>(null);
  const focusRequested = useRef<WatchlistGridFocus | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const order = useMemo(() => rows.flatMap((row) => row.instrumentId ? [row.instrumentId] : []), [rows]);
  const rowIndex = useMemo(() => new Map(rows.map((row, index) => [row.key, index])), [rows]);
  const visible = useMemo(() => new Set(order), [order]);
  const selectedIds = useMemo(
    () => new Set(selection.ids.filter((id) => visible.has(id))),
    [selection.ids, visible],
  );
  const cursor = selection.cursor && visible.has(selection.cursor) ? selection.cursor : null;
  const activeKey = watchlistSymbolRowKey(activeInstrumentId);
  const currentFocus = focus && rowIndex.has(focus.rowKey) ? focus : null;
  const tabStop: WatchlistGridFocus | null = currentFocus
    ?? (rows.length ? { rowKey: rowIndex.has(activeKey) ? activeKey : rows[0].key, cell: null } : null);

  const rowElement = (key: string) => [...(listRef.current?.querySelectorAll<HTMLElement>('[data-row-key]') ?? [])]
    .find((element) => element.dataset.rowKey === key) ?? null;
  const cellCount = (key: string) => rowElement(key)?.querySelectorAll('[data-cell]').length ?? 0;

  useEffect(() => {
    // Move DOM focus once the render that holds the requested tab stop commits.
    const request = focusRequested.current;
    if (!request || request.rowKey !== currentFocus?.rowKey || request.cell !== currentFocus.cell) return;
    focusRequested.current = null;
    const row = rowElement(currentFocus.rowKey);
    const cell = currentFocus.cell == null ? null : row?.querySelectorAll<HTMLElement>('[data-cell]')[currentFocus.cell];
    const target = cell ? cell.querySelector<HTMLElement>('button:not(:disabled)') ?? cell : row;
    target?.focus();
    target?.scrollIntoView?.({ block: 'nearest' });
  });

  const moveFocus = (rowKey: string, cell: number | null) => {
    const count = cellCount(rowKey);
    const next = { rowKey, cell: cell == null || count === 0 ? null : Math.min(cell, count - 1) };
    setFocus(next);
    focusRequested.current = next;
  };

  const apply = (result: { selection: WatchlistSelection; show: string | null }) => {
    setSelection(result.selection);
    if (result.show) onShow(result.show);
  };

  const moveRow = (from: WatchlistGridFocus | null, step: 1 | -1, cell: number | null) => {
    const start = from ? rowIndex.get(from.rowKey) ?? -1 : rowIndex.get(activeKey) ?? -1;
    const index = start < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, start + step));
    const row = rows[index];
    if (!row) return;
    if (row.instrumentId) apply(clickWatchlistRow(selection, order, row.instrumentId, NO_MODIFIERS));
    moveFocus(row.key, cell);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (isWatchlistKeyTargetIgnored(event.target)) return;
    const from = focusFromElement(event.target as Element) ?? currentFocus;
    const inCell = from?.cell != null;
    // In a cell, Enter and Space press its button.
    if (inCell && (event.key === 'Enter' || event.key === ' ')) return;
    const row = from ? rows[rowIndex.get(from.rowKey) ?? -1] : undefined;
    const command = watchlistKeyCommand(event);
    if (command === 'next' || command === 'previous') {
      event.preventDefault();
      moveRow(from, command === 'next' ? 1 : -1, from?.cell ?? null);
      return;
    }
    if (command) {
      event.preventDefault();
      const base = row?.instrumentId ? { ...selection, cursor: row.instrumentId } : selection;
      const result = applyWatchlistCommand(base, order, command, activeInstrumentId);
      apply(result);
      if (command !== 'selectAll' && result.selection.cursor) moveFocus(watchlistSymbolRowKey(result.selection.cursor), from?.cell ?? null);
      return;
    }
    if (event.altKey || event.shiftKey || event.metaKey) return;
    const toRow = (index: number) => rows[index] && moveFocus(rows[index].key, event.ctrlKey ? from?.cell ?? null : null);
    switch (event.key) {
      case 'ArrowRight':
        if (!row || event.ctrlKey) return;
        event.preventDefault();
        if (!inCell && row.sectionId && row.collapsed) onToggleSection(row.sectionId, false);
        else moveFocus(row.key, inCell ? (from?.cell ?? 0) + 1 : 0);
        return;
      case 'ArrowLeft':
        if (!row || event.ctrlKey) return;
        event.preventDefault();
        if (inCell) moveFocus(row.key, (from?.cell ?? 0) > 0 ? (from?.cell ?? 0) - 1 : null);
        else if (row.sectionId && !row.collapsed) onToggleSection(row.sectionId, true);
        else if (row.parentKey) moveFocus(row.parentKey, null);
        return;
      case 'Home':
      case 'End':
        event.preventDefault();
        if (inCell && row && !event.ctrlKey) moveFocus(row.key, event.key === 'Home' ? 0 : cellCount(row.key) - 1);
        else toRow(event.key === 'Home' ? 0 : rows.length - 1);
        return;
      case 'Enter':
        if (!row || event.ctrlKey) return;
        event.preventDefault();
        if (row.instrumentId) apply(clickWatchlistRow(selection, order, row.instrumentId, NO_MODIFIERS));
        else if (row.sectionId) onToggleSection(row.sectionId, !row.collapsed);
        return;
      default:
    }
  };

  /** Keep the tab stop where focus went by mouse or Tab. */
  const onFocus = (event: FocusEvent<HTMLElement>) => {
    const next = focusFromElement(event.target);
    if (next && (next.rowKey !== currentFocus?.rowKey || next.cell !== currentFocus?.cell)) setFocus(next);
  };

  const onRowClick = (instrumentId: string, event: MouseEvent) => {
    apply(clickWatchlistRow(selection, order, instrumentId, event));
  };

  return { selectedIds, cursor, tabStop, listRef, onKeyDown, onFocus, onRowClick };
}

/** The tree grid rows for the watchlist's visible rows. */
export function watchlistGridRows(rows: readonly WatchlistRow[]): WatchlistGridRow[] {
  return rows.map((row) => row.kind === 'section'
    ? { key: watchlistSectionRowKey(row.section.id), instrumentId: null, sectionId: row.section.id, collapsed: row.section.collapsed, parentKey: null }
    : {
      key: watchlistSymbolRowKey(row.instrumentId),
      instrumentId: row.instrumentId,
      sectionId: null,
      collapsed: false,
      parentKey: row.sectionId ? watchlistSectionRowKey(row.sectionId) : null,
    });
}

/** The tab stop as one row sees it (see `WatchlistRowTabStop`). */
export function watchlistRowTabStop(tabStop: WatchlistGridFocus | null, rowKey: string): number | null | false {
  return tabStop?.rowKey === rowKey ? tabStop.cell : false;
}
