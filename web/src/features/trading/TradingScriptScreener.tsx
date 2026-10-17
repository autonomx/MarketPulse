/**
 * The script screener (TVP-11.6): run a saved Omnix Script on a watchlist's (or the open charts') symbols, then filter
 * and sort them by its plots and alert conditions at the last bar.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { scriptsApi, type ScriptScreenResponse } from './scripts/scriptsApi';
import { SCREEN_OPERATORS, screenMatches, type ScreenFilter, type ScreenOperator } from './scripts/scriptScreen';
import { tradingApi } from './tradingApi';
import { upgradeWatchlistPayload, watchlistSymbolIds } from './tradingWatchlistModel';
import './TradingScriptScreener.css';

const MAX_SYMBOLS = 50;
const INTERVALS = ['5m', '15m', '1h', '4h', '1d', '1w'] as const;
const symbolOf = (instrumentId: string) => instrumentId.split(':').at(-1) ?? instrumentId;
const valueText = (value: number | null | undefined, alert: boolean) => {
  if (value === null || value === undefined) return '—';
  if (alert) return value ? '✓' : '';
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
};

type Props = {
  chartInstrumentIds: readonly string[];
  onShowInstrument?: (instrumentId: string) => void;
};

function useScreenSources() {
  const scripts = useQuery({
    queryKey: ['trading', 'documents', 'scripts', 'screener'],
    queryFn: async () => (await tradingApi.allDocuments('scripts')).filter((record) => record.status === 'active').map((record) => {
      const payload = record.payload as { name?: unknown; source?: unknown };
      return { id: record.record_id, name: String(payload.name ?? record.record_id), source: String(payload.source ?? '') };
    }),
    staleTime: 30_000,
  });
  const watchlists = useQuery({
    queryKey: ['trading', 'documents', 'watchlists', 'screener'],
    queryFn: async () => (await tradingApi.documents('watchlists')).map((record) => {
      const payload = upgradeWatchlistPayload(record.payload);
      return { id: record.record_id, name: payload.name, instrumentIds: watchlistSymbolIds(payload) };
    }),
    staleTime: 60_000,
  });
  return { scripts: scripts.data ?? [], watchlists: watchlists.data ?? [] };
}

function FilterRow({ filter, outputs, onChange, onRemove }: {
  filter: ScreenFilter;
  outputs: ScriptScreenResponse['outputs'];
  onChange: (next: ScreenFilter) => void;
  onRemove: () => void;
}) {
  const operator = SCREEN_OPERATORS.find((item) => item.id === filter.operator);
  return (
    <li>
      <select aria-label="Output" value={filter.output} onChange={(event) => onChange({ ...filter, output: event.target.value })}>
        {outputs.map((output) => <option key={output.key} value={output.key}>{output.title}</option>)}
      </select>
      <select aria-label="Condition" value={filter.operator} onChange={(event) => onChange({ ...filter, operator: event.target.value as ScreenOperator })}>
        {SCREEN_OPERATORS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
      {operator?.needsValue ? (
        <input aria-label="Value" type="number" step="any" value={filter.value} onChange={(event) => onChange({ ...filter, value: Number(event.target.value) })} />
      ) : null}
      <button type="button" aria-label="Remove filter" onClick={onRemove}>×</button>
    </li>
  );
}

export function TradingScriptScreener({ chartInstrumentIds, onShowInstrument }: Props) {
  const { scripts, watchlists } = useScreenSources();
  const [scriptId, setScriptId] = useState('');
  const [scope, setScope] = useState('charts');
  const [screenInterval, setScreenInterval] = useState<string>('1d');
  const [filters, setFilters] = useState<ScreenFilter[]>([]);
  const [sort, setSort] = useState<{ key: string; descending: boolean } | null>(null);
  const script = scripts.find((item) => item.id === scriptId) ?? scripts[0];
  const listed = useMemo(
    () => [...new Set(scope === 'charts' ? chartInstrumentIds : watchlists.find((list) => list.id === scope)?.instrumentIds ?? [])],
    [chartInstrumentIds, scope, watchlists],
  );
  const screen = useMutation({
    mutationFn: () => scriptsApi.screen({ source: script?.source ?? '', instrumentIds: listed.slice(0, MAX_SYMBOLS), interval: screenInterval }),
    onSuccess: (data) => setFilters((current) => current.filter((filter) => data.outputs.some((output) => output.key === filter.output))),
  });
  const data = screen.data;
  const outputs = data?.outputs ?? [];
  const rows = useMemo(() => {
    const matched = data ? screenMatches(data.rows, filters) : [];
    if (!sort) return matched;
    const direction = sort.descending ? -1 : 1;
    return [...matched].sort((left, right) => {
      const a = sort.key === 'symbol' ? symbolOf(left.instrument_id) : left.last?.[sort.key] ?? Number.NEGATIVE_INFINITY;
      const b = sort.key === 'symbol' ? symbolOf(right.instrument_id) : right.last?.[sort.key] ?? Number.NEGATIVE_INFINITY;
      return (a < b ? -1 : a > b ? 1 : 0) * direction;
    });
  }, [data, filters, sort]);
  const failed = data?.rows.filter((row) => row.error) ?? [];
  const sortBy = (key: string) => setSort((current) => (current?.key === key ? { key, descending: !current.descending } : { key, descending: key !== 'symbol' }));
  const addFilter = () => outputs[0] && setFilters((current) => [...current, {
    id: crypto.randomUUID(), output: outputs[0].key, operator: outputs[0].kind === 'alertcondition' ? 'true' : 'above', value: 0,
  }]);
  return (
    <section className="trading-script-screener" aria-label="Script screener">
      <header>
        <select aria-label="Script" value={script?.id ?? ''} onChange={(event) => setScriptId(event.target.value)}>
          {scripts.length === 0 ? <option value="">No saved scripts</option> : null}
          {scripts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <select aria-label="Symbols from" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="charts">Open charts</option>
          {watchlists.map((list) => <option key={list.id} value={list.id}>Watchlist: {list.name}</option>)}
        </select>
        <select aria-label="Interval" value={screenInterval} onChange={(event) => setScreenInterval(event.target.value)}>
          {INTERVALS.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <button type="button" onClick={() => screen.mutate()} disabled={!script || listed.length === 0 || screen.isPending}>{screen.isPending ? 'Screening…' : 'Screen'}</button>
        <span>{listed.length > MAX_SYMBOLS ? `The first ${MAX_SYMBOLS} of ${listed.length} symbols` : `${listed.length} symbols`}</span>
      </header>
      {screen.isError ? <p role="alert">{screen.error instanceof Error ? screen.error.message : 'The screen failed.'}</p> : null}
      {data?.error ? <p role="alert">The script has a problem on line {data.error.line}: {data.error.message}</p> : null}
      {data && !data.error ? (
        <>
          <div className="trading-script-screener-filters">
            <ul aria-label="Filters">
              {filters.map((filter) => (
                <FilterRow
                  key={filter.id} filter={filter} outputs={outputs}
                  onChange={(next) => setFilters((current) => current.map((item) => (item.id === filter.id ? next : item)))}
                  onRemove={() => setFilters((current) => current.filter((item) => item.id !== filter.id))}
                />
              ))}
            </ul>
            <button type="button" onClick={addFilter} disabled={outputs.length === 0}>Add filter</button>
            <span role="status">{rows.length} of {data.rows.length - failed.length} match</span>
          </div>
          <table aria-label="Screen results">
            <thead>
              <tr>
                <th><button type="button" onClick={() => sortBy('symbol')}>Symbol</button></th>
                {outputs.map((output) => <th key={output.key}><button type="button" onClick={() => sortBy(output.key)}>{output.title}</button></th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.instrument_id}>
                  <td>{onShowInstrument ? <button type="button" onClick={() => onShowInstrument(row.instrument_id)}>{symbolOf(row.instrument_id)}</button> : symbolOf(row.instrument_id)}</td>
                  {outputs.map((output) => <td key={output.key}>{valueText(row.last?.[output.key], output.kind === 'alertcondition')}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {failed.length ? (
            <details>
              <summary>{failed.length} not screened</summary>
              <ul>{failed.map((row) => <li key={row.instrument_id}>{symbolOf(row.instrument_id)}: {row.error}</li>)}</ul>
            </details>
          ) : null}
        </>
      ) : null}
      <footer>Values at each symbol's last bar (the forming one included). The script runs on up to 500 bars per symbol, as on a chart.</footer>
    </section>
  );
}
