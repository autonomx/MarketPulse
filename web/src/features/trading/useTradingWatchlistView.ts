import { useMemo, useState } from 'react';
import {
  nextWatchlistSort,
  readWatchlistView,
  toggleWatchlistColumn,
  watchlistColumn,
  writeWatchlistView,
  type WatchlistColumnId,
  type WatchlistSortKey,
  type WatchlistView,
} from './tradingWatchlistColumns';

/** The chosen columns and sort (TVP-5.2), remembered per browser. */
export function useTradingWatchlistView() {
  const [view, setView] = useState<WatchlistView>(readWatchlistView);
  const columns = useMemo(() => view.columns.flatMap((id) => watchlistColumn(id) ?? []), [view.columns]);

  const update = (next: WatchlistView) => {
    setView(next);
    writeWatchlistView(next);
  };

  const toggleColumn = (id: WatchlistColumnId) => {
    const nextColumns = toggleWatchlistColumn(view.columns, id);
    // Hiding the sorted column also drops its sort.
    const sortHidden = view.sort != null && view.sort.key !== 'symbol' && !nextColumns.includes(view.sort.key);
    update({ columns: nextColumns, sort: sortHidden ? null : view.sort });
  };

  const sortBy = (key: WatchlistSortKey) => update({ ...view, sort: nextWatchlistSort(view.sort, key) });

  return { columnIds: view.columns, columns, sort: view.sort, toggleColumn, sortBy };
}
