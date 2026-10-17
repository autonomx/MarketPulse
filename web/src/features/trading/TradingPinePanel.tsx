/**
 * The Omnix Scripts editor (TVP-11.2, 11.3): write, check, run and save Pine-compatible scripts, add them to the
 * chart, read their logs and profile, and browse, diff and restore their saved versions. A built-in indicator's
 * source (the legend's source button) opens as an unsaved copy.
 */
import { useEffect, useEffectEvent, useRef, useState, type ChangeEvent } from 'react';
import type { CoreIndicatorId, CoreIndicatorInstance } from './indicators/coreIndicators';
import { indicatorPineSource, indicatorPineTitle } from './indicators/indicatorPine';
import { ScriptCodeEditor } from './scripts/ScriptCodeEditor';
import { ScriptHistory } from './scripts/ScriptHistory';
import { isScriptIndicatorId, scriptIdOf } from './scripts/scriptIndicators';
import { useScriptEditor, type ScriptEditorModel } from './scripts/useScriptEditor';
import './TradingPinePanel.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

type BottomTab = 'console' | 'profiler' | 'history';
/** The last source request the editor handled, so reopening the panel doesn't repeat it over the restored draft. */
const HANDLED_KEY = 'omnix.trading.scripts.opened';

function storedHandled(): string | null {
  try { return window.localStorage.getItem(HANDLED_KEY); } catch { return null; }
}

function storeHandled(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(HANDLED_KEY, id);
    else window.localStorage.removeItem(HANDLED_KEY);
  } catch { /* storage unavailable */ }
}

function download(name: string, source: string): void {
  const url = URL.createObjectURL(new Blob([source], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${name.replace(/[^\w.-]+/g, '_') || 'script'}.pine`;
  link.click();
  URL.revokeObjectURL(url);
}

function ScriptConsole({ editor }: { editor: ScriptEditorModel }) {
  const problems = editor.diagnostics;
  return (
    <div className="trading-script-console" role="log" aria-label="Script console">
      {problems.map((problem, index) => (
        <p key={`problem-${index}`} className="is-error">Line {problem.line}: {problem.message}</p>
      ))}
      {editor.consoleEntries.map((entry, index) => (
        <p key={index} className={`is-${entry.level}`}>
          <time>{new Date(entry.at).toLocaleTimeString()}</time>
          {entry.bar !== undefined && entry.bar !== null ? <span>bar {entry.bar}</span> : null}
          {entry.message}
        </p>
      ))}
      {problems.length === 0 && editor.consoleEntries.length === 0 ? <p className="trading-script-empty">Run the script to see its log.info, log.warning and log.error messages.</p> : null}
    </div>
  );
}

function ScriptProfiler({ editor }: { editor: ScriptEditorModel }) {
  const total = editor.profile.reduce((sum, item) => sum + item.seconds, 0) || 1;
  const lines = [...editor.profile].sort((left, right) => right.seconds - left.seconds);
  return (
    <div className="trading-script-profiler">
      <button type="button" onClick={() => editor.run(true)} disabled={editor.busy !== null}>Run with profiler</button>
      {lines.length === 0 ? <p className="trading-script-empty">Time per line appears here and beside the code.</p> : (
        <table aria-label="Time per line">
          <thead><tr><th>Line</th><th>Time</th><th>Share</th></tr></thead>
          <tbody>
            {lines.map((item) => (
              <tr key={item.line}><td>{item.line}</td><td>{(item.seconds * 1_000).toFixed(2)} ms</td><td>{Math.round((item.seconds / total) * 100)}%</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function TradingPinePanel({
  indicators,
  activeIndicatorId,
  onActiveIndicatorChange,
  instrumentId,
  bindingId,
  interval,
  onSetIndicators,
}: {
  indicators: CoreIndicatorInstance[];
  activeIndicatorId: CoreIndicatorId | null;
  onActiveIndicatorChange: (id: CoreIndicatorId) => void;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  onSetIndicators: (indicators: CoreIndicatorInstance[]) => void;
}) {
  const editor = useScriptEditor({ instrumentId, bindingId, interval, indicators, onSetIndicators });
  const [tab, setTab] = useState<BottomTab>('console');
  const [saveAsName, setSaveAsName] = useState<string | null>(null);
  const [pending, setPending] = useState<{ label: string; open: () => void } | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const builtIns = indicators.filter((indicator) => indicator.enabled && !isScriptIndicatorId(String(indicator.id)));
  const { editing, dirty } = editor;

  /** Open something unless that loses unsaved changes; then offer to discard them. */
  const guarded = (label: string, open: (force: boolean) => void) => {
    if (dirty) {
      setPending({ label, open: () => open(true) });
      return;
    }
    setPending(null);
    open(false);
  };

  // The legend's source button: a script opens itself, a built-in an unsaved copy of its source.
  const handled = useRef<string | null>(storedHandled());
  // Only a new request opens something; the editor's own state changes don't.
  const openRequested = useEffectEvent((id: string) => {
    const scriptId = scriptIdOf(id);
    const builtIn = indicators.find((indicator) => indicator.id === id);
    if (scriptId) guarded('the script', (force) => editor.openScript(scriptId, force));
    else if (builtIn) guarded(`${indicatorPineTitle(builtIn.id)}'s source`, (force) => editor.openDraft(`${indicatorPineTitle(builtIn.id)} (copy)`, indicatorPineSource(builtIn), force));
  });
  useEffect(() => {
    const id = activeIndicatorId ? String(activeIndicatorId) : null;
    if (!id || handled.current === id) return;
    handled.current = id;
    storeHandled(id);
    openRequested(id);
  }, [activeIndicatorId]);

  const importFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    void file.text().then((text) => guarded(file.name, (force) => editor.openDraft(file.name.replace(/\.(pine|txt)$/i, ''), text, force)));
  };

  const problems = editor.diagnostics.length;
  const status = problems > 0 ? `${problems} problem${problems === 1 ? '' : 's'}` : !editing.record ? 'Not saved' : dirty ? 'Unsaved changes' : 'Saved';
  return (
    <div role="group" className="trading-pine-panel trading-script-editor" aria-label={`${PRODUCT_NAME} Scripts editor`}>
      <header className="trading-pine-header">
        <div className="trading-pine-heading"><span className="trading-pine-glyph" aria-hidden="true">{'{}'}</span><strong>{PRODUCT_NAME} Scripts</strong></div>
        <span className={`trading-script-status${problems > 0 ? ' has-problems' : dirty || !editing.record ? ' is-dirty' : ''}`} role="status">{status}</span>
      </header>
      <div className="trading-script-toolbar">
        <input aria-label="Script name" value={editing.name} onChange={(event) => editor.setName(event.target.value)} />
        <select aria-label="Open script" value={editing.record?.record_id ?? ''} onChange={(event) => { const id = event.target.value; if (id) guarded('the script', (force) => editor.openScript(id, force)); }}>
          <option value="">{editing.record ? 'Open…' : 'Unsaved script'}</option>
          {editor.scripts.map((record) => <option key={record.record_id} value={record.record_id}>{String(record.payload.name ?? record.record_id)}</option>)}
        </select>
        <button type="button" onClick={() => guarded('a new script', (force) => editor.newScript(force))}>New</button>
        <button type="button" onClick={() => guarded('a new strategy', (force) => editor.newStrategy(force))}>New strategy</button>
        <button type="button" onClick={editor.save} disabled={editor.busy !== null || (!dirty && editing.record !== null)}>Save</button>
        <button type="button" onClick={() => setSaveAsName(`${editing.name} (copy)`)} disabled={editor.busy !== null}>Save as…</button>
        <button type="button" className="trading-script-primary" onClick={editor.addToChart} disabled={editor.busy !== null || problems > 0}>{editor.onChart ? 'Update on chart' : 'Add to chart'}</button>
        <button type="button" onClick={() => editor.run(false)} disabled={editor.busy !== null || problems > 0}>Run</button>
        <button type="button" onClick={() => fileInput.current?.click()}>Import</button>
        <button type="button" onClick={() => download(editing.name, editing.source)}>Export</button>
        <input ref={fileInput} type="file" accept=".pine,.txt,text/plain" hidden aria-label="Import script file" onChange={importFile} />
        {builtIns.length > 0 ? (
          <select aria-label="Built-in source" value="" onChange={(event) => {
            const id = event.target.value as CoreIndicatorId;
            if (!id) return;
            handled.current = null;
            storeHandled(null);
            onActiveIndicatorChange(id);
          }}>
            <option value="">Built-in source…</option>
            {builtIns.map((indicator) => <option key={indicator.id} value={indicator.id}>{indicatorPineTitle(indicator.id)}</option>)}
          </select>
        ) : null}
      </div>
      {saveAsName !== null ? (
        <form className="trading-script-saveas" onSubmit={(event) => { event.preventDefault(); editor.saveAs(saveAsName); setSaveAsName(null); }}>
          <input aria-label="New script name" value={saveAsName} autoFocus onChange={(event) => setSaveAsName(event.target.value)} />
          <button type="submit">Save copy</button>
          <button type="button" onClick={() => setSaveAsName(null)}>Cancel</button>
        </form>
      ) : null}
      {pending || editor.notice ? (
        <div className="trading-pine-notice" role="alert">
          <span aria-hidden="true">!</span>
          <strong>{pending ? `Unsaved changes: open ${pending.label} anyway?` : editor.notice}</strong>
          {pending ? <><button type="button" onClick={() => { pending.open(); setPending(null); }}>Discard and open</button><button type="button" onClick={() => setPending(null)}>Keep editing</button></> : null}
        </div>
      ) : null}
      <ScriptCodeEditor
        value={editing.source}
        onChange={editor.setSource}
        reference={editor.reference}
        diagnostics={editor.diagnostics}
        profile={editor.profile}
        onSave={editor.save}
        label="Script source (Pine-compatible)"
      />
      <section className="trading-script-bottom">
        <nav role="tablist" aria-label="Script tools">
          {(['console', 'profiler', 'history'] as const).map((item) => (
            <button key={item} type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>
              {item === 'console' ? 'Console' : item === 'profiler' ? 'Profiler' : 'History'}
            </button>
          ))}
          {tab === 'console' ? <button type="button" className="trading-script-clear" onClick={editor.clearConsole}>Clear</button> : null}
        </nav>
        <div role="tabpanel" className="trading-script-tabpanel">
          {tab === 'console' ? <ScriptConsole editor={editor} /> : null}
          {tab === 'profiler' ? <ScriptProfiler editor={editor} /> : null}
          {tab === 'history' ? (
            <ScriptHistory
              scriptId={editing.record?.record_id ?? null}
              currentSource={editing.source}
              savedRevision={editing.record?.revision ?? null}
              onRestore={(version) => { editor.setSource(version.source); if (version.name) editor.setName(version.name); }}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}
