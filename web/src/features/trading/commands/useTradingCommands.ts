import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  claimsBrowserKey,
  resolveCommand,
  tradingCommandDefinition,
  type KeyOverrides,
  type RegisteredCommand,
  type TradingCommandAvailability,
  type TradingCommandId,
} from './tradingCommands';
import { forgetChartClick, noteTradingPointerDown, resetTradingPointerContext } from './chartKeyContext';

const registered: RegisteredCommand[] = [];
let keyOverrides: KeyOverrides = {};
let availability: TradingCommandAvailability = 'browser';
let dispatchers = 0;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setTradingCommandKeyOverrides(overrides: KeyOverrides): void {
  keyOverrides = overrides;
  notify();
}

export function tradingCommandKeyOverrides(): KeyOverrides {
  return keyOverrides;
}

/** Browser mode by default; the installed app (TVP-4.5) switches to `installed` so TradingView's browser-reserved keys work. */
export function setTradingCommandAvailability(next: TradingCommandAvailability): void {
  availability = next;
  notify();
}

export function tradingCommandAvailability(): TradingCommandAvailability {
  return availability;
}

export function useTradingCommandAvailability(): TradingCommandAvailability {
  return useSyncExternalStore(subscribe, tradingCommandAvailability, tradingCommandAvailability);
}

/** The current key overrides; re-renders when they change. */
export function useTradingCommandKeyOverrides(): KeyOverrides {
  return useSyncExternalStore(subscribe, tradingCommandKeyOverrides, tradingCommandKeyOverrides);
}

function dispatch(event: KeyboardEvent): void {
  if (event.key === 'Escape') forgetChartClick();
  if (event.defaultPrevented || event.isComposing) return;
  const match = resolveCommand(registered, event, keyOverrides, availability);
  if (!match) {
    // In the app window a claimed browser key with nothing to run (one tab, typing in a field) must not reach the
    // browser: Ctrl+W would close the whole window.
    if (availability === 'installed' && claimsBrowserKey(event)) event.preventDefault();
    return;
  }
  if (event.repeat && !match.definition.repeatable) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  match.handler.run(event);
}

function pointerDown(event: PointerEvent): void {
  noteTradingPointerDown(event.target);
}

/** Installs the one window key listener for trading commands. Mount once, at the trading workspace. */
export function useTradingCommandDispatcher(): void {
  useEffect(() => {
    dispatchers += 1;
    if (dispatchers === 1) {
      window.addEventListener('keydown', dispatch);
      window.addEventListener('pointerdown', pointerDown, true);
    }
    return () => {
      dispatchers -= 1;
      if (dispatchers === 0) {
        window.removeEventListener('keydown', dispatch);
        window.removeEventListener('pointerdown', pointerDown, true);
        resetTradingPointerContext();
      }
    };
  }, []);
}

function activeRegistration(id: string): RegisteredCommand | undefined {
  for (let index = registered.length - 1; index >= 0; index -= 1) {
    const entry = registered[index];
    if (entry.definition.id === id && entry.handler.isActive()) return entry;
  }
  return undefined;
}

/** True when a mounted component can run the command now. */
export function canRunTradingCommand(id: string): boolean {
  return activeRegistration(id) !== undefined;
}

/** Runs a command without a key press (command palette, toolbar entries). Returns false when nothing can run it. */
export function runTradingCommand(id: string): boolean {
  const entry = activeRegistration(id);
  if (!entry) return false;
  entry.handler.run();
  return true;
}

/** Binds a component's handler to a catalogued command while the component is mounted. */
export function useTradingCommand(
  id: TradingCommandId,
  run: (event?: KeyboardEvent) => void,
  isActive: () => boolean = () => true,
): void {
  const runRef = useRef(run);
  const isActiveRef = useRef(isActive);
  useEffect(() => {
    runRef.current = run;
    isActiveRef.current = isActive;
  });
  useEffect(() => {
    const definition = tradingCommandDefinition(id);
    if (!definition) throw new Error(`Unknown trading command ${id}`);
    const entry: RegisteredCommand = {
      definition,
      handler: { run: (event) => runRef.current(event), isActive: () => isActiveRef.current() },
    };
    registered.push(entry);
    return () => {
      const index = registered.indexOf(entry);
      if (index >= 0) registered.splice(index, 1);
    };
  }, [id]);
}
