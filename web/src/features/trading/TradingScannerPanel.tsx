import { useEffect, useMemo, useRef, useState } from 'react';
import { AUTO_REFRESH_OPTIONS, resultChanges, STALE_RUN_MS, useScannerAutoRefresh, type AutoRefresh } from './scannerAutoRefresh';
import type { CanonicalInstrument } from './tradingTypes';
import type { TradingScannerDefinition, TradingScannerDefinitionInput, TradingScannerResult, TradingScannerRun } from './scannerTypes';
import { ScreenerResultsTable } from './ScreenerResultsTable';
import { ScreenerRuleEditor } from './ScreenerRuleEditor';
import { ScreenFromWords } from './ScreenFromWords';
import { newScreenerRule, screenerHistoryNeeded, type ScreenerRuleInput } from './screenerRules';
import { tradingScannerApi } from './tradingScannerApi';
import { useAlertIndicatorIds } from './useTradingAlerts';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';

const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'timed_out']);

/** The rules of a run's own definition snapshot (stored rules from before TVP-9.1 have no role: filters). */
function snapshotRules(run: TradingScannerRun): TradingScannerDefinition['rules'] {
  const rules = (run.definition_snapshot as { rules?: TradingScannerDefinition['rules'] } | undefined)?.rules ?? [];
  return rules.map((rule) => ({ ...rule, role: rule.role ?? 'filter' }));
}

/** The screen a definition starts from: one filter, change % over a bar at or above 1. */
function defaultRules(): ScreenerRuleInput[] {
  return [newScreenerRule('filter')];
}

export function TradingScannerPanel({ instruments, onShowInstrument }: {
  instruments: CanonicalInstrument[];
  /** Shows a result's symbol on the active chart (and so its colour link group, TVP-9.1). */
  onShowInstrument?: (instrumentId: string) => void;
}) {
  const [definitions, setDefinitions] = useState<TradingScannerDefinition[]>([]);
  const [runs, setRuns] = useState<TradingScannerRun[]>([]);
  const [results, setResults] = useState<TradingScannerResult[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [name, setName] = useState('Momentum scan');
  const [rules, setRules] = useState<ScreenerRuleInput[]>(defaultRules);
  const [interval, setScanInterval] = useState('1d');
  // A saved screen loaded into the editor: saving updates it (at its revision) instead of adding one.
  const [editing, setEditing] = useState<TradingScannerDefinition | null>(null);
  // The rules of the run on screen (its own snapshot): a screen edited since keeps its old columns until it runs again.
  const [resultRules, setResultRules] = useState<TradingScannerDefinition['rules']>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const serverIndicators = useAlertIndicatorIds();
  const indicatorIds = useMemo(() => [...(serverIndicators ?? [])], [serverIndicators]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [autoRefresh, setAutoRefresh] = useState<AutoRefresh | null>(null);
  const [changes, setChanges] = useState<ReturnType<typeof resultChanges>>({ added: new Set(), removed: [] });
  // The results shown before the latest run, and which run they came from, to say what changed.
  const shown = useRef<{ runId: string | null; scannerId: string | null; results: TradingScannerResult[] | null }>({ runId: null, scannerId: null, results: null });

  const refresh = async () => {
    try {
      const [nextDefinitions, nextRuns] = await Promise.all([
        tradingScannerApi.definitions(),
        tradingScannerApi.runs(),
      ]);
      setDefinitions(nextDefinitions);
      setRuns(nextRuns);
      setStatus('ready');
      const latest = nextRuns[0];
      if (latest?.status === 'completed' && latest.run_id !== shown.current.runId) {
        // Claimed before the await: an overlapping refresh does not diff the run against itself.
        const previous = shown.current;
        shown.current = { ...previous, runId: latest.run_id };
        const next = await tradingScannerApi.results(latest.run_id).catch((error: unknown) => {
          shown.current = previous; // load it again on the next refresh
          throw error;
        });
        // Changes only compare runs of the same screen.
        setChanges(previous.scannerId === latest.scanner_id ? resultChanges(previous.results, next) : resultChanges(null, next));
        shown.current = { runId: latest.run_id, scannerId: latest.scanner_id, results: next };
        setResultRules(snapshotRules(latest));
        setResults(next);
      }
    } catch {
      setStatus('error');
    }
  };

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!runs.some((run) => !terminalStatuses.has(run.status))) return;
    return startPolling(refresh, POLL_INTERVALS_MS.scanner);
  }, [runs]);

  // Auto-refresh (TVP-9.2): a new run only when none of the screen's runs is still working.
  // A run left queued or running past any run's limit was abandoned (the server no longer counts it either).
  const working = (scannerId: string) => runs.some((run) => run.scanner_id === scannerId && !terminalStatuses.has(run.status)
    && (!run.created_at || Date.now() - Date.parse(run.created_at) < STALE_RUN_MS));
  useScannerAutoRefresh(autoRefresh, working, async (scannerId) => {
    await tradingScannerApi.start(scannerId);
    await refresh();
  }, () => {
    setAutoRefresh(null);
    setStatus('error');
  });

  const available = useMemo(() => instruments.slice(0, 200), [instruments]);
  const save = async () => {
    const thresholdsValid = rules.every((rule) => rule.role === 'column' || Number.isFinite(Number(rule.threshold)));
    const indicatorsChosen = rules.every((rule) => rule.metric !== 'indicator' || Boolean((rule.source as { output?: string } | null)?.output));
    if (!selectedIds.length || !thresholdsValid || !indicatorsChosen || !rules.some((rule) => rule.role !== 'column')) {
      setStatus('error');
      setNotice(!indicatorsChosen ? 'Choose a line for each indicator rule.' : 'A screen needs symbols, at least one filter, and a value for each filter.');
      return;
    }
    setNotice(null);
    // The history every rule needs (the indicator rules the most), within the server's 500-bar limit; an edit never
    // shortens a screen's history (EMA/RSI/ATR values depend on it).
    const needed = Math.min(500, Math.max(100, rules.some((rule) => rule.metric === 'indicator') ? 500 : screenerHistoryNeeded(rules)));
    const definition: TradingScannerDefinitionInput = editing
      ? { ...editing, name, instrument_ids: selectedIds, interval, rules, history_limit: Math.max(editing.history_limit, needed) }
      : {
        scanner_id: `scanner-${Date.now()}`, name, instrument_ids: selectedIds, binding_ids: {}, interval, history_limit: needed, rules,
        max_concurrency: 4, request_timeout_seconds: 10, run_timeout_seconds: 120, formula_version: 'omnix-indicators-v2', enabled: true, revision: 1,
      };
    setStatus('saving');
    try {
      const saved = editing ? await tradingScannerApi.update({ ...definition, revision: editing.revision }) : await tradingScannerApi.create(definition);
      setEditing(saved);
      if (!working(saved.scanner_id)) await tradingScannerApi.start(saved.scanner_id);
      await refresh();
    } catch (error) {
      setStatus('error');
      if (editing && error instanceof Error && error.message.includes('(409)')) {
        // Changed elsewhere: take its new revision and keep these edits, so saving again applies them.
        const fresh = (await tradingScannerApi.definitions().catch(() => [])).find((item) => item.scanner_id === editing.scanner_id);
        if (fresh) setEditing(fresh);
        setNotice('This screen changed elsewhere. Your edits are kept: save again to apply them over the new version.');
      }
    }
  };

  const load = (definition: TradingScannerDefinition) => {
    setEditing(definition);
    setName(definition.name);
    setSelectedIds([...definition.instrument_ids]);
    setScanInterval(definition.interval);
    setRules(definition.rules.map((rule) => ({ ...rule, role: rule.role ?? 'filter', source: rule.source ?? null })) as ScreenerRuleInput[]);
  };

  const symbolOf = (instrumentId: string) => instruments.find((item) => item.instrument_id === instrumentId)?.display_symbol
    ?? instrumentId.split(':').at(-1) ?? instrumentId;

  const start = async (scannerId: string) => {
    setStatus('saving');
    try {
      await tradingScannerApi.start(scannerId);
      await refresh();
    } catch {
      setStatus('error');
    }
  };

  return (
    <section className="trading-scanner-panel" aria-label="Bounded market scanner" data-status={status}>
      <header><strong>Bounded scanner</strong><span>{status}</span></header>
      <p>Allowlist only. Maximum 200 instruments, 500 bars, 8 concurrent requests, and 5-minute total runtime.</p>
      <div className="trading-scanner-form">
        <label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Interval<select value={interval} onChange={(event) => setScanInterval(event.target.value)}>{['1m', '5m', '15m', '1h', '4h', '1d'].map((item) => <option key={item}>{item}</option>)}</select></label>
      </div>
      <ScreenFromWords onProposal={(proposed, proposedInterval) => { setRules(proposed); setScanInterval(proposedInterval); }} />
      <ScreenerRuleEditor rules={rules} indicatorIds={indicatorIds} onChange={setRules} />
      {notice ? <p className="trading-scanner-changes" role="alert">{notice}</p> : null}
      <fieldset className="trading-scanner-universe">
        <legend>Instrument allowlist ({selectedIds.length}/200)</legend>
        {available.map((instrument) => (
          <label key={instrument.instrument_id}>
            <input
              type="checkbox"
              checked={selectedIds.includes(instrument.instrument_id)}
              onChange={(event) => setSelectedIds((current) => event.target.checked
                ? [...current, instrument.instrument_id].slice(0, 200)
                : current.filter((item) => item !== instrument.instrument_id))}
            />
            {instrument.display_symbol}
          </label>
        ))}
      </fieldset>
      <div className="trading-screener-save">
        <button type="button" disabled={!selectedIds.length || status === 'saving'} onClick={() => void save()}>{editing ? `Save ${editing.name} and run` : 'Save and run screen'}</button>
        {editing ? <button type="button" onClick={() => { setEditing(null); setName('Momentum scan'); setRules(defaultRules()); }}>New screen</button> : null}
      </div>
      <ul className="trading-scanner-definitions">
        {definitions.map((definition) => (
          <li key={definition.scanner_id}>
            <div><strong>{definition.name}</strong><small>{definition.instrument_ids.length} instruments · {definition.interval} · revision {definition.revision}</small></div>
            <button type="button" disabled={working(definition.scanner_id)} onClick={() => void start(definition.scanner_id)}>Run</button>
            <button type="button" onClick={() => load(definition)} aria-label={`Edit ${definition.name}`}>Edit</button>
            <select
              aria-label={`Auto-refresh ${definition.name}`}
              value={autoRefresh?.scannerId === definition.scanner_id ? autoRefresh.everyMs : 0}
              onChange={(event) => {
                const everyMs = Number(event.target.value);
                setAutoRefresh(everyMs > 0 ? { scannerId: definition.scanner_id, everyMs } : null);
                if (everyMs > 0 && !working(definition.scanner_id)) void start(definition.scanner_id);
              }}
            >
              {AUTO_REFRESH_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </li>
        ))}
      </ul>
      <ul className="trading-scanner-runs">
        {runs.slice(0, 10).map((run) => (
          <li key={run.run_id}>
            <div><strong>{run.status}</strong><small>{run.completed_count}/{run.universe_count} · {run.matched_count} matches</small></div>
            {!terminalStatuses.has(run.status) ? <button type="button" onClick={() => void tradingScannerApi.cancel(run.run_id).then(refresh)}>Cancel</button> : null}
          </li>
        ))}
      </ul>
      {changes.added.size > 0 || changes.removed.length > 0 ? (
        <p className="trading-scanner-changes" role="status">
          Since the previous run: {changes.added.size} new, {changes.removed.length} dropped{changes.removed.length > 0 ? ` (${changes.removed.join(', ')})` : ''}.
        </p>
      ) : null}
      <ScreenerResultsTable results={results} rules={resultRules} added={changes.added} symbolOf={symbolOf} onShow={(instrumentId) => onShowInstrument?.(instrumentId)} />
    </section>
  );
}
