/**
 * A script indicator's inputs in the indicator settings (TVP-11.1): the `input.*` calls of its saved script, read by
 * compiling it on the server, with this chart's values (`params` `in:<title>`) or the script's defaults.
 */
import { useEffect, useState } from 'react';
import type { CoreIndicatorInstance } from '../indicators/coreIndicators';
import { loadScriptSource, scriptIdOf, scriptInputValues, withScriptInput } from './scriptIndicators';
import { scriptsApi, type ScriptInput } from './scriptsApi';

const SOURCES = ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4', 'volume'];

function numberOption(options: ScriptInput['options'], key: string): number | undefined {
  const value = options?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function ScriptIndicatorInputs({
  draft,
  setDraft,
}: {
  draft: CoreIndicatorInstance;
  setDraft: (update: (current: CoreIndicatorInstance) => CoreIndicatorInstance) => void;
}) {
  const scriptId = scriptIdOf(String(draft.id));
  const revision = Number(draft.params?.revision ?? 0);
  const [inputs, setInputs] = useState<ScriptInput[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (!scriptId) return undefined;
    let live = true;
    loadScriptSource(scriptId, revision)
      .then((script) => scriptsApi.check(script.source))
      .then((checked) => {
        if (!live) return;
        const [first] = checked.diagnostics;
        setProblem(first ? `Line ${first.line}: ${first.message}` : null);
        setInputs(checked.inputs as ScriptInput[]);
      })
      .catch((error: unknown) => { if (live) setProblem(error instanceof Error ? error.message : String(error)); });
    return () => { live = false; };
  }, [scriptId, revision]);

  if (problem) return <p className="trading-indicator-settings-help" role="alert">{problem}</p>;
  if (!inputs) return <p className="trading-indicator-settings-help" role="status">Loading the script's inputs…</p>;
  if (inputs.length === 0) return <p className="trading-indicator-settings-help">This script has no inputs.</p>;
  const values = scriptInputValues(draft);
  const set = (title: string, value: unknown) => setDraft((current) => ({ ...current, params: withScriptInput(current.params, title, value) }));
  return (
    <>
      <div className="trading-indicator-settings-section-label">Script inputs</div>
      {inputs.map((input) => {
        const value = values[input.title] ?? input.default;
        const choices = Array.isArray(input.options?.options) ? (input.options.options as unknown[]).map(String) : null;
        if (input.type === 'bool') {
          return (
            <label key={input.title} className="trading-indicator-settings-check">
              <input type="checkbox" checked={value === true} onChange={(event) => set(input.title, event.target.checked)} />
              <span>{input.title}</span>
            </label>
          );
        }
        const control = choices || input.type === 'source' ? (
          <select aria-label={input.title} value={String(value)} onChange={(event) => set(input.title, input.type === 'int' || input.type === 'float' ? Number(event.target.value) : event.target.value)}>
            {(choices ?? SOURCES).map((choice) => <option key={choice} value={choice}>{choice}</option>)}
          </select>
        ) : input.type === 'int' || input.type === 'float' ? (
          <input
            aria-label={input.title}
            type="number"
            value={Number(value)}
            min={numberOption(input.options, 'minval')}
            max={numberOption(input.options, 'maxval')}
            step={numberOption(input.options, 'step') ?? (input.type === 'int' ? 1 : 'any')}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (event.target.value !== '' && Number.isFinite(next)) set(input.title, input.type === 'int' ? Math.round(next) : next);
            }}
          />
        ) : input.type === 'color' ? (
          <input aria-label={input.title} type="color" value={String(value).slice(0, 7)} onChange={(event) => set(input.title, event.target.value)} />
        ) : (
          <input aria-label={input.title} type="text" value={String(value ?? '')} onChange={(event) => set(input.title, event.target.value)} />
        );
        return (
          <label key={input.title} className="trading-indicator-settings-field">
            <span>{input.title}</span>
            {control}
          </label>
        );
      })}
    </>
  );
}
