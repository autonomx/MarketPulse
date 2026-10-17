/**
 * The Omnix Scripts editor's state (TVP-11.2, 11.3): the saved scripts, the one being edited, its problems (checked
 * on the server as you type), the console (logs, errors, what the chart could not draw) and the profiler.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { CoreIndicatorInstance } from '../indicators/coreIndicators';
import { tradingApi } from '../tradingApi';
import type { TradingDocument } from '../tradingTypes';
import {
  forgetScriptSource, isScriptIndicatorId, newScriptRecordId, scriptIdOf, scriptIndicatorInstance, scriptRunStatus, subscribeScriptRunStatus,
} from './scriptIndicators';
import { scriptsApi, type ScriptCheckResult, type ScriptReference, type ScriptRunResult } from './scriptsApi';

export const NEW_SCRIPT_SOURCE = `//@version=6
indicator("My script", overlay=true)
length = input.int(20, "Length", minval=1)
plot(ta.sma(close, length), "SMA", color=color.blue)
`;

/** A strategy to start from (TVP-11.5): it trades a simulated account; the Strategy Tester shows its backtest. */
export const NEW_STRATEGY_SOURCE = `//@version=6
strategy("My strategy", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10)
fast = ta.sma(close, 14)
slow = ta.sma(close, 28)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
plot(fast, "Fast", color=color.blue)
plot(slow, "Slow", color=color.orange)
`;

export type ConsoleEntry = { at: number; level: 'info' | 'warning' | 'error'; message: string; bar?: number | null };
export type ScriptEditorChart = {
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  indicators: CoreIndicatorInstance[];
  onSetIndicators: (indicators: CoreIndicatorInstance[]) => void;
};

type Editing = { record: TradingDocument | null; name: string; source: string; savedName: string; savedSource: string };
const CHECK_DELAY_MS = 500;
/** The draft survives the panel closing (switching side-panel tabs unmounts it); this browser only. */
const DRAFT_KEY = 'omnix.trading.scripts.draft';

function storedDraft(): Editing | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    const draft = raw ? (JSON.parse(raw) as Partial<Editing>) : null;
    return draft && typeof draft.source === 'string' && typeof draft.name === 'string'
      ? { record: draft.record ?? null, name: draft.name, source: draft.source, savedName: String(draft.savedName ?? ''), savedSource: String(draft.savedSource ?? '') }
      : null;
  } catch {
    return null;
  }
}
const MAX_CONSOLE = 500;

/** An unsaved script: not dirty until edited (the status says it isn't saved). */
function blank(name: string, source: string): Editing {
  return { record: null, name, source, savedName: name, savedSource: source };
}

function fromRecord(record: TradingDocument): Editing {
  const name = String(record.payload.name ?? record.record_id);
  const source = String(record.payload.source ?? '');
  return { record, name, source, savedName: name, savedSource: source };
}

/** A run's console lines: how it went, then its log.* messages with their bar. */
function runEntries(result: ScriptRunResult, chart: string, at: number): ConsoleEntry[] {
  return [
    { at, level: 'info', message: `Ran on ${result.bars} bars of ${chart} in ${(result.seconds * 1_000).toFixed(1)} ms.` },
    ...result.logs.map((entry): ConsoleEntry => ({ at, level: entry.level === 'error' || entry.level === 'warning' ? entry.level : 'info', message: entry.message, bar: entry.bar })),
  ];
}

function useStoredDraft() {
  const [editing, setEditing] = useState<Editing>(() => storedDraft() ?? blank('My script', NEW_SCRIPT_SOURCE));
  useEffect(() => {
    try { window.localStorage.setItem(DRAFT_KEY, JSON.stringify(editing)); } catch { /* storage unavailable: the draft lasts while the panel is open */ }
  }, [editing]);
  return [editing, setEditing] as const;
}

export function useScriptEditor(chart: ScriptEditorChart) {
  const [scripts, setScripts] = useState<TradingDocument[]>([]);
  const [editing, setEditing] = useStoredDraft();
  const [checked, setChecked] = useState<ScriptCheckResult | null>(null);
  const [reference, setReference] = useState<ScriptReference | null>(null);
  const [consoleEntries, setConsole] = useState<ConsoleEntry[]>([]);
  const [profile, setProfile] = useState<Array<{ line: number; seconds: number }>>([]);
  const [busy, setBusy] = useState<null | 'saving' | 'running'>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const dirty = editing.source !== editing.savedSource || editing.name !== editing.savedName;
  const chartRef = useRef(chart);
  useEffect(() => { chartRef.current = chart; });

  const log = useCallback((entries: ConsoleEntry[]) => entries.length > 0 && setConsole((current) => [...current, ...entries].slice(-MAX_CONSOLE)), []);

  const reloadScripts = useCallback(() => tradingApi.allDocuments('scripts')
    .then((records) => setScripts(records.filter((record) => record.status === 'active')))
    .catch(() => setScripts([])), []);
  useEffect(() => { void reloadScripts(); }, [reloadScripts]);
  useEffect(() => {
    let live = true;
    scriptsApi.reference().then((loaded) => { if (live) setReference(loaded); }, () => undefined);
    return () => { live = false; };
  }, []);

  // Problems as you type: the server compiles the source (nothing runs).
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      scriptsApi.check(editing.source).then((result) => { if (live) setChecked(result); }, () => undefined);
    }, CHECK_DELAY_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [editing.source]);

  const scriptId = editing.record?.record_id ?? null;
  // What the charts' runs of this script report (TVP-11.1): errors and logs reach the console.
  const chartStatus = useSyncExternalStore(subscribeScriptRunStatus, () => (scriptId ? scriptRunStatus(scriptId) : null));
  const seenStatus = useRef<number>(0);
  useEffect(() => {
    if (!chartStatus || chartStatus.at === seenStatus.current) return;
    seenStatus.current = chartStatus.at;
    const at = chartStatus.at;
    log([
      ...(chartStatus.error ? [{ at, level: 'error' as const, message: `On the chart: ${chartStatus.error}` }] : []),
      ...chartStatus.notDrawn.map((item) => ({ at, level: 'warning' as const, message: `Not drawn on the chart yet: ${item}` })),
    ]);
  }, [chartStatus, log]);

  const onChart = useMemo(
    () => (scriptId ? chart.indicators.some((indicator) => scriptIdOf(String(indicator.id)) === scriptId) : false),
    [chart.indicators, scriptId],
  );

  /** Open something else, unless that would lose unsaved changes (`force` discards them). */
  const replace = useCallback((next: Editing, force = false): boolean => {
    if (dirty && !force) {
      setNotice('Save or discard your changes first.');
      return false;
    }
    setEditing(next);
    setProfile([]);
    setNotice(null);
    return true;
  }, [dirty, setEditing]);

  const openScript = useCallback((recordId: string, force = false) => {
    const known = scripts.find((record) => record.record_id === recordId);
    if (known) return void replace(fromRecord(known), force);
    tradingApi.document('scripts', recordId).then((record) => replace(fromRecord(record), force), (error: unknown) => {
      setNotice(error instanceof Error ? error.message : String(error));
    });
  }, [replace, scripts]);

  const openDraft = useCallback((name: string, source: string, force = false) => replace(blank(name, source), force), [replace]);

  /** Save the script (a new one is created); every chart showing it re-runs the new source. */
  const save = useCallback(async (asName?: string): Promise<TradingDocument | null> => {
    const name = (asName ?? editing.name).trim() || 'Untitled script';
    const payload = { name, source: editing.source };
    setBusy('saving');
    try {
      const saved = editing.record && asName === undefined
        ? await tradingApi.updateDocument('scripts', editing.record, payload)
        : await tradingApi.createDocument('scripts', newScriptRecordId(), payload);
      forgetScriptSource(saved.record_id);
      setEditing(fromRecord(saved));
      setNotice(null);
      const { indicators, onSetIndicators } = chartRef.current;
      if (indicators.some((indicator) => scriptIdOf(String(indicator.id)) === saved.record_id)) {
        onSetIndicators(indicators.map((indicator) => (scriptIdOf(String(indicator.id)) === saved.record_id
          ? { ...indicator, params: { ...indicator.params, name, revision: saved.revision } }
          : indicator)));
      }
      void reloadScripts();
      return saved;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice(message.includes('409') ? 'This script changed elsewhere: open it again to see the newer version.' : message);
      return null;
    } finally {
      setBusy(null);
    }
  }, [editing, reloadScripts, setEditing]);

  /** Save if needed, then add the script to the chart (or re-run it there). */
  const addToChart = useCallback(async () => {
    const saved = dirty || !editing.record ? await save() : editing.record;
    if (!saved) return;
    const overlay = checked?.declaration?.overlay === true;
    const { indicators, onSetIndicators } = chartRef.current;
    const name = String(saved.payload.name ?? editing.name);
    if (indicators.some((indicator) => scriptIdOf(String(indicator.id)) === saved.record_id)) {
      onSetIndicators(indicators.map((indicator) => (scriptIdOf(String(indicator.id)) === saved.record_id
        ? { ...indicator, enabled: true, params: { ...indicator.params, name, overlay: overlay ? 1 : 0, revision: saved.revision } }
        : indicator)));
    } else {
      onSetIndicators([...indicators, scriptIndicatorInstance(saved.record_id, name, overlay, saved.revision)]);
    }
  }, [checked, dirty, editing, save]);

  /** Run the source being edited on the chart's symbol and interval: logs, errors and (with profiling) time per line. */
  const run = useCallback(async (withProfile: boolean) => {
    const { instrumentId, bindingId, interval } = chartRef.current;
    setBusy('running');
    try {
      const response = await scriptsApi.run({ source: editing.source, instrumentId, bindingId, interval, limit: 1_000, profile: withProfile });
      const at = Date.now();
      if (!response.result) {
        const error = response.error;
        log([{ at, level: 'error', message: error ? `${error.line ? `Line ${error.line}: ` : ''}${error.message}` : 'The script did not run.' }]);
        return;
      }
      log(runEntries(response.result, `${instrumentId} ${interval}`, at));
      if (withProfile) setProfile(response.result.profile);
    } catch (error) {
      log([{ at: Date.now(), level: 'error', message: error instanceof Error ? error.message : String(error) }]);
    } finally {
      setBusy(null);
    }
  }, [editing.source, log]);

  return {
    scripts,
    editing,
    dirty,
    onChart,
    checked,
    diagnostics: checked?.diagnostics ?? [],
    reference,
    consoleEntries,
    clearConsole: () => setConsole([]),
    profile,
    busy,
    notice,
    setNotice,
    setName: (name: string) => setEditing((current) => ({ ...current, name })),
    setSource: (source: string) => setEditing((current) => ({ ...current, source })),
    newScript: (force = false) => openDraft('My script', NEW_SCRIPT_SOURCE, force),
    newStrategy: (force = false) => openDraft('My strategy', NEW_STRATEGY_SOURCE, force),
    openScript,
    openDraft,
    save: () => void save(),
    saveAs: (name: string) => void save(name),
    addToChart: () => void addToChart(),
    run: (withProfile = false) => void run(withProfile),
    /** Whether an indicator id is one of the saved scripts (the legend's source button opens it). */
    isScript: (id: string) => isScriptIndicatorId(id),
  };
}

export type ScriptEditorModel = ReturnType<typeof useScriptEditor>;
