import { createPortal } from 'react-dom';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { formatCommandKeys, formatHotkey } from './hotkeyLabels';
import { hotkeyFromEvent } from './hotkeys';
import { saveKeyOverrides } from './keyOverridesStorage';
import {
  TRADING_COMMANDS,
  commandKeys,
  findKeyClashes,
  isRebindable,
  rebindProblem,
  type KeyClash,
  type TradingCommandDefinition,
} from './tradingCommands';
import { useModalDialog } from './useModalDialog';
import { useTradingCommandAvailability, useTradingCommandKeyOverrides } from './useTradingCommands';
import { InstallAppNote } from '../InstallAppNote';
import './TradingKeyboard.css';

const COMMANDS: readonly TradingCommandDefinition[] = TRADING_COMMANDS;
const GROUPS = [...new Set(COMMANDS.map((command) => command.group))];
const labelById = new Map(COMMANDS.map((command) => [command.id, command.label]));

const CLASH_TEXT: Record<KeyClash['kind'], string> = {
  conflict: 'Same key as',
  shadowed: 'Same key, other context:',
  typing: 'Clashes with typing:',
};

/** One line per clash a command is part of, naming the other commands. */
function clashNotes(clashes: readonly KeyClash[]): Map<string, string[]> {
  const notes = new Map<string, string[]>();
  for (const clash of clashes) {
    for (const id of clash.ids) {
      const others = clash.ids.filter((other) => other !== id).map((other) => labelById.get(other) ?? other);
      notes.set(id, [...(notes.get(id) ?? []), `${CLASH_TEXT[clash.kind]} ${others.join(', ')}`]);
    }
  }
  return notes;
}

type RowProps = {
  definition: TradingCommandDefinition;
  keys: readonly string[];
  appOnlyKeys: readonly string[];
  overridden: boolean;
  capturing: boolean;
  notes?: readonly string[];
  onToggleCapture: () => void;
  onCaptureKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onCancelCapture: () => void;
  onReset: () => void;
};

function ShortcutKeys({ definition, keys, appOnlyKeys }: Pick<RowProps, 'definition' | 'keys' | 'appOnlyKeys'>) {
  if (definition.planned) return <em>Not available yet</em>;
  return (
    <span className="trading-shortcut-keys">
      {keys.map((key) => <kbd key={key}>{key}</kbd>)}
      {keys.length === 0 ? <em>Not set</em> : null}
      {appOnlyKeys.map((key) => <kbd key={key} className="is-app-only" title="Installed app only">{key} · app</kbd>)}
    </span>
  );
}

function ShortcutRow({ definition, keys, appOnlyKeys, overridden, capturing, notes, onToggleCapture, onCaptureKeyDown, onCancelCapture, onReset }: RowProps) {
  const fixedText = definition.planned ? 'Not available yet' : definition.handledLocally ? 'Watchlist grid' : 'Fixed';
  return (
    <tr data-command-id={definition.id}>
      <th scope="row">
        {definition.label}
        {notes?.map((note) => <small key={note} className="trading-shortcut-conflict">{note}</small>)}
      </th>
      <td><ShortcutKeys definition={definition} keys={keys} appOnlyKeys={appOnlyKeys} /></td>
      <td className="trading-shortcut-actions">
        {isRebindable(definition) ? (
          <>
            <button
              type="button"
              aria-label={capturing ? `Press new keys for ${definition.label}` : `Change keys for ${definition.label}`}
              aria-pressed={capturing}
              onClick={onToggleCapture}
              onKeyDown={onCaptureKeyDown}
              onBlur={() => { if (capturing) onCancelCapture(); }}
            >
              {capturing ? 'Press keys…' : 'Change'}
            </button>
            {overridden ? <button type="button" aria-label={`Reset ${definition.label}`} onClick={onReset}>Reset</button> : null}
          </>
        ) : <span className="trading-shortcut-fixed">{fixedText}</span>}
      </td>
    </tr>
  );
}

/**
 * Ctrl+/: every shortcut by context, searchable, with rebinding. Change waits
 * for the next key press (Escape cancels, Tab leaves); keys that can't be
 * bound are refused with the reason, and clashes with other commands or with
 * typing on the chart are listed. Overrides persist in this browser.
 */
export function TradingShortcutDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const overrides = useTradingCommandKeyOverrides();
  const availability = useTradingCommandAvailability();
  const [query, setQuery] = useState('');
  const [capturingId, setCapturingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const onDialogKeyDown = useModalDialog(open, dialogRef, onClose);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setCapturingId(null);
    setNotice(null);
    searchRef.current?.focus();
  }, [open]);

  const clashes = useMemo(() => findKeyClashes(COMMANDS, overrides, availability), [availability, overrides]);
  const notesById = useMemo(() => clashNotes(clashes), [clashes]);

  if (!open || typeof document === 'undefined') return null;

  const keysFor = (definition: TradingCommandDefinition) => formatCommandKeys(definition, commandKeys(definition, overrides, availability));
  const appOnlyKeys = (definition: TradingCommandDefinition) => (
    availability === 'browser' ? (definition.installedKeys ?? []).map((key) => formatHotkey(key)) : []
  );
  const normalizedQuery = query.trim().toLowerCase();
  const matches = (definition: TradingCommandDefinition) => !normalizedQuery
    || [definition.label, definition.group, ...keysFor(definition), ...appOnlyKeys(definition)].join(' ').toLowerCase().includes(normalizedQuery);

  const reset = (id: string) => {
    const next = { ...overrides };
    delete next[id];
    saveKeyOverrides(next);
    setNotice(`${labelById.get(id) ?? id}: default keys restored`);
  };

  const onCaptureKeyDown = (definition: TradingCommandDefinition) => (event: KeyboardEvent<HTMLButtonElement>) => {
    if (capturingId !== definition.id) return;
    if (event.key === 'Tab' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      setCapturingId(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Escape') {
      setCapturingId(null);
      return;
    }
    const hotkey = hotkeyFromEvent(event);
    if (!hotkey) return;
    const problem = rebindProblem(definition, hotkey, availability);
    if (problem) {
      setNotice(`${formatHotkey(hotkey)}: ${problem}`);
      return;
    }
    saveKeyOverrides({ ...overrides, [definition.id]: [hotkey] });
    setCapturingId(null);
    setNotice(`${definition.label}: ${formatHotkey(hotkey)}`);
  };

  return createPortal(
    <div className="trading-shortcut-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        ref={dialogRef}
        className="trading-shortcut-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trading-shortcut-dialog-title"
        onKeyDown={onDialogKeyDown}
      >
        <header>
          <h2 id="trading-shortcut-dialog-title">Keyboard shortcuts</h2>
          <button
            type="button"
            onClick={() => { saveKeyOverrides({}); setNotice('All shortcuts restored to their defaults'); }}
            disabled={Object.keys(overrides).length === 0}
          >
            Reset all
          </button>
          <button type="button" aria-label="Close keyboard shortcuts" onClick={onClose}>×</button>
        </header>
        <input
          ref={searchRef}
          type="search"
          aria-label="Search shortcuts"
          placeholder="Search by name, context or key"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <InstallAppNote availability={availability} />
        {clashes.length > 0 ? (
          <p className="trading-shortcut-conflicts" role="alert">
            {clashes.length === 1 ? '1 key is' : `${clashes.length} keys are`} used by more than one command or by typing on the chart.
          </p>
        ) : null}
        <p className="trading-shortcut-notice" role="status">{notice ?? ''}</p>
        <div className="trading-shortcut-groups">
          {GROUPS.map((group) => {
            const rows = COMMANDS.filter((definition) => definition.group === group && matches(definition));
            if (rows.length === 0) return null;
            const headingId = `trading-shortcut-group-${group.toLowerCase()}`;
            return (
              <section key={group} aria-labelledby={headingId}>
                <h3 id={headingId}>{group}</h3>
                <table>
                  <tbody>
                    {rows.map((definition) => (
                      <ShortcutRow
                        key={definition.id}
                        definition={definition}
                        keys={keysFor(definition)}
                        appOnlyKeys={appOnlyKeys(definition)}
                        overridden={Boolean(overrides[definition.id])}
                        capturing={capturingId === definition.id}
                        notes={notesById.get(definition.id)}
                        onToggleCapture={() => setCapturingId(capturingId === definition.id ? null : definition.id)}
                        onCaptureKeyDown={onCaptureKeyDown(definition)}
                        onCancelCapture={() => setCapturingId(null)}
                        onReset={() => reset(definition.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </section>
            );
          })}
        </div>
      </section>
    </div>,
    document.body,
  );
}
