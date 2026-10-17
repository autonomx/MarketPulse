import { createPortal } from 'react-dom';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { fuzzyFilter } from './fuzzyMatch';
import { useModalDialog } from './useModalDialog';
import './TradingKeyboard.css';

export type TradingPaletteItem = {
  id: string;
  label: string;
  group: string;
  /** Formatted keys, e.g. `Ctrl+K`. */
  keys?: readonly string[];
  /** Listed but not runnable now (no chart, dialog or panel to act on). */
  disabled?: boolean;
  /** Why a disabled item can't run; defaults to "not available now". */
  unavailableText?: string;
  run: () => void;
};

const MAX_RESULTS = 80;

/**
 * Ctrl+K: a searchable list of commands and quick actions. Keyboard only:
 * type to filter, ↑/↓ to move, Enter to run, Escape to close. Tab stays in
 * the palette, and closing returns focus to where it was.
 */
export function TradingCommandPalette({
  open,
  title,
  placeholder,
  items,
  onClose,
}: {
  open: boolean;
  title: string;
  placeholder: string;
  items: readonly TradingPaletteItem[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const onDialogKeyDown = useModalDialog(open, dialogRef, onClose);
  const listId = useId();
  const results = useMemo(
    () => fuzzyFilter(items, query, (item) => `${item.label} ${item.group}`).slice(0, MAX_RESULTS),
    [items, query],
  );
  const active = results[Math.min(activeIndex, results.length - 1)];

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActiveIndex(0);
    inputRef.current?.focus();
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  const runItem = (item: TradingPaletteItem | undefined) => {
    if (!item || item.disabled) return;
    onClose();
    item.run();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = results.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: activeIndex >= last ? 0 : activeIndex + 1,
      ArrowUp: activeIndex <= 0 ? last : activeIndex - 1,
      PageDown: Math.min(last, activeIndex + 10),
      PageUp: Math.max(0, activeIndex - 10),
    };
    if (event.key in moves) {
      event.preventDefault();
      setActiveIndex(Math.max(0, moves[event.key]));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      runItem(active);
    }
  };

  return createPortal(
    <div className="trading-command-palette-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="trading-command-palette" role="dialog" aria-modal="true" aria-label={title} onKeyDown={onDialogKeyDown}>
        <input
          ref={inputRef}
          className="trading-command-palette-input"
          role="combobox"
          aria-label={title}
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active ? `${listId}-${active.id}` : undefined}
          placeholder={placeholder}
          value={query}
          onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }}
          onKeyDown={onKeyDown}
        />
        <p className="visually-hidden" role="status" aria-live="polite">
          {results.length === 1 ? '1 result' : `${results.length} results`}
        </p>
        <ul id={listId} className="trading-command-palette-list" role="listbox" aria-label={`${title} results`}>
          {results.map((item, index) => (
            <li
              key={item.id}
              id={`${listId}-${item.id}`}
              role="option"
              aria-selected={item === active}
              aria-disabled={item.disabled || undefined}
              className={item === active ? 'is-active' : undefined}
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => setActiveIndex(index)}
              onClick={() => runItem(item)}
            >
              <span className="trading-command-palette-label">{item.label}</span>
              <span className="trading-command-palette-group">{item.disabled ? `${item.group} · ${item.unavailableText ?? 'not available now'}` : item.group}</span>
              {item.keys?.length ? <span className="trading-command-palette-keys">{item.keys.map((key) => <kbd key={key}>{key}</kbd>)}</span> : null}
            </li>
          ))}
          {results.length === 0 ? <li className="trading-command-palette-empty" role="presentation">No matching commands</li> : null}
        </ul>
      </section>
    </div>,
    document.body,
  );
}
