import { WATCHLIST_COLUMNS, type WatchlistColumnId } from './tradingWatchlistColumns';
import { WatchlistIndicatorColumnPicker } from './WatchlistIndicatorColumnPicker';

/** The watchlist's "⋯" menu: list actions, import/export and the column chooser. */
export function TradingWatchlistOptionsMenu({
  canEdit,
  canDelete,
  columns,
  onCreate,
  onAddSection,
  onRename,
  onDelete,
  onImport,
  onExport,
  onAdvancedView,
  onToggleColumn,
}: {
  /** False while a generated flag list is shown. */
  canEdit: boolean;
  canDelete: boolean;
  columns: readonly WatchlistColumnId[];
  onCreate: () => void;
  onAddSection: () => void;
  onRename: () => void;
  onDelete: () => void;
  onImport: () => void;
  onExport: () => void;
  /** Opens the advanced view (TVP-5.4) on this list. */
  onAdvancedView: () => void;
  onToggleColumn: (id: WatchlistColumnId) => void;
}) {
  return (
    <div className="trading-watchlist-options-menu" role="menu">
      <button type="button" role="menuitem" onClick={onAdvancedView}>Advanced view</button>
      <button type="button" role="menuitem" onClick={onCreate}>New watchlist</button>
      <button type="button" role="menuitem" onClick={onAddSection} disabled={!canEdit}>Add section</button>
      <button type="button" role="menuitem" onClick={onRename} disabled={!canEdit}>Rename watchlist</button>
      <button type="button" role="menuitem" onClick={onDelete} disabled={!canDelete}>Delete watchlist</button>
      <button type="button" role="menuitem" onClick={onImport}>Import list…</button>
      <button type="button" role="menuitem" onClick={onExport}>Export list</button>
      <div className="trading-watchlist-options-group" role="group" aria-label="Columns">
        <span aria-hidden="true">Columns</span>
        {WATCHLIST_COLUMNS.map((column) => (
          <button
            key={column.id}
            type="button"
            role="menuitemcheckbox"
            aria-checked={columns.includes(column.id)}
            onClick={() => onToggleColumn(column.id)}
          >
            {column.label} <small>{column.description}</small>
          </button>
        ))}
      </div>
      <WatchlistIndicatorColumnPicker columns={columns} onToggleColumn={onToggleColumn} />
    </div>
  );
}
