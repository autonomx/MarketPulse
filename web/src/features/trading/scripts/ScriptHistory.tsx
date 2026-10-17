/**
 * A script's saved versions (TVP-11.3): pick one to see how it differs from the editor or from the version before
 * it, and restore it into the editor (saving it then adds it as the newest version).
 */
import { useEffect, useMemo, useState } from 'react';
import { diffLines, diffStats } from './scriptDiff';
import { scriptsApi, type ScriptVersion, type ScriptVersionSummary } from './scriptsApi';

function savedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function ScriptHistory({
  scriptId,
  currentSource,
  savedRevision,
  onRestore,
}: {
  scriptId: string | null;
  currentSource: string;
  /** The document revision last saved; a new one reloads the list. */
  savedRevision: number | null;
  onRestore: (version: ScriptVersion) => void;
}) {
  const [versions, setVersions] = useState<ScriptVersionSummary[] | null>(null);
  const [selected, setSelected] = useState<ScriptVersion | null>(null);
  const [previous, setPrevious] = useState<ScriptVersion | null>(null);
  const [against, setAgainst] = useState<'editor' | 'previous'>('editor');
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!scriptId) return undefined;
    let live = true;
    scriptsApi.versions(scriptId).then((loaded) => { if (live) { setVersions(loaded); setProblem(null); } }, (error: unknown) => {
      if (live) setProblem(error instanceof Error ? error.message : String(error));
    });
    return () => { live = false; };
  }, [scriptId, savedRevision]);

  const choose = (summary: ScriptVersionSummary) => {
    if (!scriptId || !versions) return;
    const older = versions[versions.findIndex((item) => item.revision === summary.revision) + 1];
    Promise.all([scriptsApi.version(scriptId, summary.revision), older ? scriptsApi.version(scriptId, older.revision) : null])
      .then(([version, before]) => { setSelected(version); setPrevious(before); }, (error: unknown) => setProblem(error instanceof Error ? error.message : String(error)));
  };

  const lines = useMemo(() => {
    if (!selected) return [];
    return against === 'editor' ? diffLines(selected.source, currentSource) : diffLines(previous?.source ?? '', selected.source);
  }, [against, currentSource, previous, selected]);
  const stats = diffStats(lines);

  if (!scriptId) return <p className="trading-script-empty">Save the script to keep its versions.</p>;
  if (problem) return <p className="trading-script-empty" role="alert">{problem}</p>;
  if (!versions) return <p className="trading-script-empty" role="status">Loading versions…</p>;
  return (
    <div className="trading-script-history">
      <ul className="trading-script-versions" aria-label="Script versions">
        {versions.map((version, index) => (
          <li key={version.revision}>
            <button type="button" aria-pressed={selected?.revision === version.revision} onClick={() => choose(version)}>
              <strong>{index === 0 ? 'Latest' : `Version ${versions.length - index}`}</strong>
              <span>{savedAt(version.saved_at)} · {version.lines} lines{version.name ? ` · ${version.name}` : ''}</span>
            </button>
          </li>
        ))}
      </ul>
      {selected ? (
        <div className="trading-script-diff-panel">
          <div className="trading-script-diff-toolbar">
            <label>
              <span>Compare with</span>
              <select aria-label="Compare version with" value={against} onChange={(event) => setAgainst(event.target.value as 'editor' | 'previous')}>
                <option value="editor">the editor</option>
                <option value="previous">the version before</option>
              </select>
            </label>
            <span className="trading-script-diff-stats">+{stats.added} −{stats.removed}</span>
            <button type="button" onClick={() => onRestore(selected)}>Restore this version</button>
          </div>
          <div className="trading-script-diff" role="table" aria-label="Changes">
            {lines.map((line, index) => (
              <div key={index} role="row" className={`trading-script-diff-line is-${line.kind}`}>
                <span role="cell" aria-hidden="true">{line.kind === 'added' ? '+' : line.kind === 'removed' ? '−' : ' '}</span>
                <code role="cell">{line.text || ' '}</code>
              </div>
            ))}
          </div>
        </div>
      ) : <p className="trading-script-empty">Pick a version to see its changes.</p>}
    </div>
  );
}
