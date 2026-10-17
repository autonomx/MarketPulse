import type { MouseEvent, ReactElement } from 'react';
import { TradingWatchlistLogo } from './TradingWatchlistLogo';
import {
  columnTone,
  nextWatchlistSort,
  type WatchlistColumnDefinition,
  type WatchlistSort,
  type WatchlistSortKey,
} from './tradingWatchlistColumns';
import {
  WATCHLIST_FLAG_COLORS,
  WATCHLIST_FLAG_LABELS,
  type WatchlistFlagColor,
  type WatchlistSectionItem,
} from './tradingWatchlistModel';
import type { WatchlistQuoteSnapshot } from './useTradingWatchlistQuotes';

function sortLabel(sort: WatchlistSort, key: WatchlistSortKey, description: string): string {
  const next = nextWatchlistSort(sort, key);
  return next
    ? `Sort watchlist by ${description} ${next.direction === 'asc' ? 'ascending' : 'descending'}`
    : `Clear watchlist ${description} sort`;
}

/** Action cells in every row (move up, move down, flag or rename, remove); the header spans them. */
export const WATCHLIST_ACTION_CELLS = 4;

/**
 * The header row of the watchlist tree grid; each column header sorts the
 * list by its column and reports the current order with `aria-sort`. The
 * last header names the row action cells.
 */
export function TradingWatchlistHeader({
  columns,
  sort,
  interval,
  onSort,
}: {
  columns: readonly WatchlistColumnDefinition[];
  sort: WatchlistSort;
  interval: string;
  onSort: (key: WatchlistSortKey) => void;
}) {
  const header = (key: WatchlistSortKey, label: string, description: string, title: string) => {
    const direction = sort?.key === key ? sort.direction : null;
    return (
      <div
        key={key}
        role="columnheader"
        className={`trading-watchlist-header-cell${key === 'symbol' ? ' symbol' : ''}`}
        aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none'}
      >
        <button
          type="button"
          className="trading-watchlist-sort"
          aria-label={sortLabel(sort, key, description)}
          title={title}
          onClick={() => onSort(key)}
        >
          <span>{label}</span>
          <span aria-hidden="true">{direction === 'desc' ? '↓' : direction === 'asc' ? '↑' : '↕'}</span>
        </button>
      </div>
    );
  };
  return (
    <div className="trading-watchlist-columns" role="row">
      {header('symbol', 'Symbol', 'symbol', 'Click to sort by symbol.')}
      {columns.map((column) => header(
        column.id,
        column.label,
        column.description,
        `${column.hint ?? column.label}${column.signed ? ` over ${interval}` : ''}. Click to sort.`,
      ))}
      <div role="columnheader" aria-colspan={WATCHLIST_ACTION_CELLS} className="trading-watchlist-actions-header">Actions</div>
    </div>
  );
}

export function TradingWatchlistFlagMenu({
  symbol,
  current,
  onPick,
  onClose,
}: {
  symbol: string;
  current: WatchlistFlagColor | undefined;
  onPick: (color: WatchlistFlagColor | null) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="trading-watchlist-flag-menu"
      role="menu"
      aria-label={`Flag ${symbol}`}
      onKeyDown={(event) => {
        if (event.key !== 'Escape') return;
        event.stopPropagation();
        onClose();
      }}
    >
      {WATCHLIST_FLAG_COLORS.map((color) => (
        <button
          key={color}
          type="button"
          role="menuitemradio"
          aria-checked={current === color}
          aria-label={`${WATCHLIST_FLAG_LABELS[color]} flag`}
          title={`${WATCHLIST_FLAG_LABELS[color]} flag`}
          onClick={() => onPick(color)}
        >
          <span className={`trading-watchlist-flag-swatch ${color}`} aria-hidden="true" />
        </button>
      ))}
      <button type="button" role="menuitem" className="trading-watchlist-flag-clear" onClick={() => onPick(null)} disabled={!current}>
        Remove flag
      </button>
    </div>
  );
}

/**
 * Roving tab stop of one row: `false` when the stop is in another row,
 * `null` when it is on the row itself, else the index of the cell holding it.
 */
export type WatchlistRowTabStop = number | null | false;

/** Props for a navigable cell; a cell with an enabled control passes the stop to it. */
function cellProps(index: number, tabStop: WatchlistRowTabStop, hasControl: boolean) {
  return { 'data-cell': index, tabIndex: tabStop === index && !hasControl ? 0 : -1 };
}

function controlTabIndex(index: number, tabStop: WatchlistRowTabStop): 0 | -1 {
  return tabStop === index ? 0 : -1;
}

/** An action cell: a gridcell with one button, or an empty cell where the action does not apply. */
function ActionCell({
  index,
  tabStop,
  button,
}: {
  index: number | null;
  tabStop: WatchlistRowTabStop;
  button: ((tabIndex: 0 | -1) => ReactElement<{ disabled?: boolean }>) | null;
}) {
  if (index == null || !button) return <span role="gridcell" />;
  const control = button(controlTabIndex(index, tabStop));
  return <span role="gridcell" {...cellProps(index, tabStop, !control.props.disabled)}>{control}</span>;
}

export function TradingWatchlistSectionRow({
  rowKey,
  section,
  symbolCount,
  columnCount,
  sorted,
  tabStop,
  canMoveUp,
  canMoveDown,
  onToggle,
  onMove,
  onRename,
  onRemove,
}: {
  rowKey: string;
  section: WatchlistSectionItem;
  symbolCount: number;
  /** Data columns shown, so the name cell spans the symbol and data columns. */
  columnCount: number;
  sorted: boolean;
  tabStop: WatchlistRowTabStop;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onToggle: () => void;
  onMove: (direction: -1 | 1) => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  return (
    <li
      className="trading-watchlist-section"
      role="row"
      aria-level={1}
      aria-expanded={!section.collapsed}
      data-row-key={rowKey}
      tabIndex={tabStop === null ? 0 : -1}
    >
      <div role="gridcell" aria-colspan={1 + columnCount} className="trading-watchlist-section-cell" {...cellProps(0, tabStop, true)}>
        <button
          type="button"
          className="trading-watchlist-section-toggle"
          aria-expanded={!section.collapsed}
          aria-label={`${section.collapsed ? 'Expand' : 'Collapse'} section ${section.name}`}
          tabIndex={controlTabIndex(0, tabStop)}
          onClick={onToggle}
        >
          <span className="trading-watchlist-section-chevron" aria-hidden="true">{section.collapsed ? '▸' : '▾'}</span>
          <strong>{section.name}</strong>
          <small>{symbolCount}</small>
        </button>
      </div>
      <span role="presentation" className={`trading-watchlist-row-actions${sorted ? ' is-sorted' : ''}`}>
        <ActionCell index={1} tabStop={tabStop} button={(tabIndex) => <button type="button" tabIndex={tabIndex} onClick={() => onMove(-1)} disabled={!canMoveUp} aria-label={`Move section ${section.name} up`} title="Move up">↑</button>} />
        <ActionCell index={2} tabStop={tabStop} button={(tabIndex) => <button type="button" tabIndex={tabIndex} onClick={() => onMove(1)} disabled={!canMoveDown} aria-label={`Move section ${section.name} down`} title="Move down">↓</button>} />
        <ActionCell index={3} tabStop={tabStop} button={(tabIndex) => <button type="button" tabIndex={tabIndex} onClick={onRename} aria-label={`Rename section ${section.name}`} title="Rename section">✎</button>} />
        <ActionCell index={4} tabStop={tabStop} button={(tabIndex) => <button type="button" tabIndex={tabIndex} onClick={onRemove} aria-label={`Remove section ${section.name}`} title="Remove section (keeps its symbols)">×</button>} />
      </span>
    </li>
  );
}

export function TradingWatchlistSymbolRow({
  rowKey,
  level,
  tabStop,
  instrumentId,
  symbol,
  quote,
  columns,
  flag,
  active,
  selected,
  sorted,
  movable,
  removable,
  canMoveUp,
  canMoveDown,
  flagMenuOpen,
  removeTitle,
  onSelect,
  onMove,
  onToggleFlagMenu,
  onPickFlag,
  onRemove,
}: {
  rowKey: string;
  /** 2 inside a section, 1 before the first section. */
  level: number;
  tabStop: WatchlistRowTabStop;
  instrumentId: string;
  symbol: string;
  quote: WatchlistQuoteSnapshot | undefined;
  columns: readonly WatchlistColumnDefinition[];
  flag: WatchlistFlagColor | undefined;
  active: boolean;
  /** Part of the keyboard or mouse selection. */
  selected: boolean;
  sorted: boolean;
  /** False in a generated flag list, which has no manual order. */
  movable: boolean;
  /** False for a read-only list (saved by a newer Omnix). */
  removable: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  flagMenuOpen: boolean;
  removeTitle: string;
  onSelect: (event: MouseEvent) => void;
  onMove: (direction: -1 | 1) => void;
  onToggleFlagMenu: (open: boolean) => void;
  onPickFlag: (color: WatchlistFlagColor | null) => void;
  onRemove: () => void;
}) {
  // Navigable cells are numbered in order; absent actions leave an empty cell.
  let next = 1 + columns.length;
  const take = (present: boolean) => {
    if (!present) return null;
    next += 1;
    return next - 1;
  };
  const upCell = take(movable);
  const downCell = take(movable);
  const flagCell = take(true);
  const removeCell = take(removable);
  return (
    <li
      role="row"
      aria-level={level}
      aria-selected={selected}
      className={[active ? 'active' : '', selected ? 'selected' : ''].join(' ').trim() || undefined}
      data-instrument-id={instrumentId}
      data-row-key={rowKey}
      tabIndex={tabStop === null ? 0 : -1}
    >
      <div role="gridcell" className="trading-watchlist-symbol-cell" {...cellProps(0, tabStop, true)}>
        <button type="button" onClick={onSelect} aria-label={`Select ${symbol}`} tabIndex={controlTabIndex(0, tabStop)}>
          {flag ? (
            <span className={`trading-watchlist-flag ${flag}`} role="img" aria-label={`${WATCHLIST_FLAG_LABELS[flag]} flag`} title={`${WATCHLIST_FLAG_LABELS[flag]} flag`} />
          ) : null}
          <TradingWatchlistLogo symbol={symbol} instrumentId={instrumentId} />
          <strong>{symbol}</strong>
        </button>
        {flagMenuOpen ? (
          <TradingWatchlistFlagMenu symbol={symbol} current={flag} onPick={onPickFlag} onClose={() => onToggleFlagMenu(false)} />
        ) : null}
      </div>
      {columns.map((column, index) => {
        const tone = columnTone(column, quote);
        return (
          <span
            key={column.id}
            role="gridcell"
            className={column.signed ? `trading-watchlist-change${tone ? ` ${tone}` : ''}` : 'trading-watchlist-price'}
            {...cellProps(1 + index, tabStop, false)}
          >
            {column.format(quote)}
          </span>
        );
      })}
      <span role="presentation" className={`trading-watchlist-row-actions${sorted ? ' is-sorted' : ''}`}>
        <ActionCell index={upCell} tabStop={tabStop} button={upCell == null ? null : (tabIndex) => <button type="button" tabIndex={tabIndex} onClick={() => onMove(-1)} disabled={!canMoveUp} aria-label={`Move ${symbol} up`} title="Move up">↑</button>} />
        <ActionCell index={downCell} tabStop={tabStop} button={downCell == null ? null : (tabIndex) => <button type="button" tabIndex={tabIndex} onClick={() => onMove(1)} disabled={!canMoveDown} aria-label={`Move ${symbol} down`} title="Move down">↓</button>} />
        <ActionCell
          index={flagCell}
          tabStop={tabStop}
          button={(tabIndex) => (
            <button
              type="button"
              tabIndex={tabIndex}
              onClick={() => onToggleFlagMenu(!flagMenuOpen)}
              aria-label={`Flag ${symbol}`}
              aria-haspopup="menu"
              aria-expanded={flagMenuOpen}
              title="Flag"
            >
              ⚑
            </button>
          )}
        />
        <ActionCell index={removeCell} tabStop={tabStop} button={removeCell == null ? null : (tabIndex) => <button type="button" tabIndex={tabIndex} onClick={onRemove} aria-label={`Remove ${symbol}`} title={removeTitle}>×</button>} />
      </span>
    </li>
  );
}
