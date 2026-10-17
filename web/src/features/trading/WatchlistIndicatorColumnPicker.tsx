import { useMemo, useState } from 'react';
import { indicatorLines } from './indicatorLineChoices';
import { isExternalIndicatorId } from './indicators/externalIndicatorData';
import {
  MAX_INDICATOR_COLUMNS,
  indicatorColumnId,
  isIndicatorColumnId,
  watchlistColumn,
  type WatchlistColumnId,
} from './tradingWatchlistColumns';
import { useAlertIndicatorIds } from './useTradingAlerts';

/**
 * The watchlist's indicator columns (TVP-5.2) in the column chooser: the chosen ones (click to remove) and a row to add
 * any indicator the server computes, with its period and line.
 */
export function WatchlistIndicatorColumnPicker({
  columns,
  onToggleColumn,
}: {
  columns: readonly WatchlistColumnId[];
  onToggleColumn: (id: WatchlistColumnId) => void;
}) {
  const serverIds = useAlertIndicatorIds();
  const indicatorIds = useMemo(() => [...(serverIds ?? [])].filter((id) => indicatorLines(id, 14).length > 0), [serverIds]);
  const [indicatorId, setIndicatorId] = useState('rsi');
  const [period, setPeriod] = useState(14);
  const chosenId = indicatorIds.includes(indicatorId) ? indicatorId : indicatorIds[0] ?? '';
  const lines = useMemo(() => (chosenId ? indicatorLines(chosenId, period) : []), [chosenId, period]);
  const [output, setOutput] = useState('');
  const chosenOutput = lines.some((line) => line.key === output) ? output : lines[0]?.key ?? '';
  const indicatorColumns = columns.filter(isIndicatorColumnId);
  const full = indicatorColumns.length >= MAX_INDICATOR_COLUMNS;
  const candidate = chosenId && chosenOutput ? indicatorColumnId({ indicatorId: chosenId, period, output: chosenOutput }) : null;

  return (
    <div className="trading-watchlist-options-group" role="group" aria-label="Indicator columns">
      <span aria-hidden="true">Indicator columns</span>
      {indicatorColumns.map((id) => (
        <button key={id} type="button" role="menuitemcheckbox" aria-checked onClick={() => onToggleColumn(id)} title="Remove this column">
          {watchlistColumn(id)?.label ?? id}
        </button>
      ))}
      <div className="trading-watchlist-indicator-column-form">
        <select aria-label="Indicator for a column" value={chosenId} disabled={indicatorIds.length === 0} onChange={(event) => setIndicatorId(event.target.value)}>
          {indicatorIds.length === 0 ? <option value="">Loading…</option> : null}
          {indicatorIds.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        {isExternalIndicatorId(chosenId) ? null : (
          <input aria-label="Indicator column period" inputMode="numeric" value={period} onChange={(event) => setPeriod(Math.max(1, Math.min(500, Number(event.target.value) || 1)))} />
        )}
        <select aria-label="Indicator column line" value={chosenOutput} disabled={lines.length === 0} onChange={(event) => setOutput(event.target.value)}>
          {lines.map((line) => <option key={line.key} value={line.key}>{line.title}</option>)}
        </select>
        <button
          type="button"
          disabled={!candidate || full || columns.includes(candidate as WatchlistColumnId)}
          title={full ? `At most ${MAX_INDICATOR_COLUMNS} indicator columns` : undefined}
          onClick={() => candidate && onToggleColumn(candidate)}
        >
          Add column
        </button>
      </div>
    </div>
  );
}
