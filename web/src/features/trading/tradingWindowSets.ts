/**
 * Full multi-window (TVP-4.6): tabs popped out into their own browser windows, moved back, and the window set
 * restored on reload. On top of the shared workspace (every window holds all its tabs, TVP-4.3), each popped-out
 * window has a key (`?window=`) and shows only the tabs assigned to it; the main window shows the rest. Assignments
 * live on this computer (localStorage), so every window sees a change at once through the `storage` event; popped
 * windows heartbeat there too, and the main window offers to reopen a window that is gone (one click each: a browser
 * opens one window per click) or to bring its tabs back.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { startTicker } from '../../shared/timers';
import type { TradingTabState } from './tradingStore';
import { HEARTBEAT_MS, PRESENCE_TTL_MS } from './windowPresence';

const SETS_KEY = 'omnix.trading.windowSets.v1';
const ALIVE_KEY = 'omnix.trading.windowAlive.v1';
const CHANGED_EVENT = 'omnix-trading-window-sets';
export const MAIN_WINDOW = 'main';
const WINDOW_KEY = /^w-[a-z0-9]{6,32}$/;

/** Workspace id -> window key -> the tab ids it shows. */
type WindowSets = Record<string, Record<string, string[]>>;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage blocked: the window set lasts only as long as this page.
  }
  // `storage` fires in the other windows only; this one hears its own change here.
  window.dispatchEvent(new Event(CHANGED_EVENT));
}

/** This window's key: `?window=` for a popped-out window, else the main window. */
export function currentWindowKey(search: string = typeof window === 'undefined' ? '' : window.location.search): string {
  const key = new URLSearchParams(search).get('window');
  return key && WINDOW_KEY.test(key) ? key : MAIN_WINDOW;
}

export function newWindowKey(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID().replace(/-/g, '') : Math.random().toString(36).slice(2);
  return `w-${random.slice(0, 16)}`;
}

export function windowAssignments(workspaceId: string): Record<string, string[]> {
  return read<WindowSets>(SETS_KEY, {})[workspaceId] ?? {};
}

function saveAssignments(workspaceId: string, assignments: Record<string, string[]>): void {
  const sets = read<WindowSets>(SETS_KEY, {});
  const kept = Object.fromEntries(Object.entries(assignments).filter(([, tabIds]) => tabIds.length > 0));
  if (Object.keys(kept).length > 0) sets[workspaceId] = kept;
  else delete sets[workspaceId];
  write(SETS_KEY, sets);
}

/** The tabs a window shows, in the workspace's order: its own, or (the main window) every tab no other window has. */
export function tabsForWindow<T extends Pick<TradingTabState, 'tabId'>>(tabs: readonly T[], assignments: Record<string, string[]>, windowKey: string): T[] {
  if (windowKey !== MAIN_WINDOW) return tabs.filter((tab) => assignments[windowKey]?.includes(tab.tabId));
  const elsewhere = new Set(Object.values(assignments).flat());
  return tabs.filter((tab) => !elsewhere.has(tab.tabId));
}

/** Moves a tab to a window (the main window: no assignment). */
export function assignTab(workspaceId: string, tabId: string, windowKey: string): void {
  const assignments = Object.fromEntries(Object.entries(windowAssignments(workspaceId)).map(([key, tabIds]) => [key, tabIds.filter((id) => id !== tabId)]));
  if (windowKey !== MAIN_WINDOW) assignments[windowKey] = [...(assignments[windowKey] ?? []), tabId];
  saveAssignments(workspaceId, assignments);
}

/** A window's tabs go back to the main window. */
export function forgetWindow(workspaceId: string, windowKey: string): void {
  const assignments = { ...windowAssignments(workspaceId) };
  delete assignments[windowKey];
  saveAssignments(workspaceId, assignments);
}

/** Drops tabs the workspace no longer has (closed in some window). */
export function pruneAssignments(workspaceId: string, tabIds: readonly string[]): void {
  const existing = new Set(tabIds);
  const assignments = windowAssignments(workspaceId);
  const pruned = Object.fromEntries(Object.entries(assignments).map(([key, ids]) => [key, ids.filter((id) => existing.has(id))]));
  if (JSON.stringify(pruned) !== JSON.stringify(assignments)) saveAssignments(workspaceId, pruned);
}

export function heartbeatWindow(windowKey: string, at = Date.now()): void {
  if (windowKey === MAIN_WINDOW) return;
  write(ALIVE_KEY, { ...read<Record<string, number>>(ALIVE_KEY, {}), [windowKey]: at });
}

/** Popped-out windows of the workspace that hold tabs but are gone (closed, or silent past the presence TTL). */
export function goneWindows(workspaceId: string, now = Date.now()): Array<{ windowKey: string; tabIds: string[] }> {
  const alive = read<Record<string, number>>(ALIVE_KEY, {});
  return Object.entries(windowAssignments(workspaceId))
    .filter(([windowKey, tabIds]) => tabIds.length > 0 && now - (alive[windowKey] ?? 0) > PRESENCE_TTL_MS)
    .map(([windowKey, tabIds]) => ({ windowKey, tabIds }));
}

/** The workspace's window set as stored now: its assignments and its gone windows. */
function storedWindowSet(workspaceId: string): { assignments: Record<string, string[]>; gone: ReturnType<typeof goneWindows> } {
  return { assignments: windowAssignments(workspaceId), gone: goneWindows(workspaceId) };
}

export function windowUrl(workspaceId: string, tabId: string, windowKey: string, base: Pick<Location, 'origin' | 'pathname'> = window.location): string {
  const url = new URL(base.pathname, base.origin);
  url.searchParams.set('workspace', workspaceId);
  url.searchParams.set('tab', tabId);
  url.searchParams.set('window', windowKey);
  return url.toString();
}

function openWindow(workspaceId: string, tabId: string, windowKey: string): void {
  window.open(windowUrl(workspaceId, tabId, windowKey), `omnix-trading-${windowKey}`, 'popup,width=1400,height=900');
}

/** Moves a tab into a new browser window of its own. */
export function popOutTab(workspaceId: string, tabId: string): string {
  const windowKey = newWindowKey();
  assignTab(workspaceId, tabId, windowKey);
  heartbeatWindow(windowKey);
  openWindow(workspaceId, tabId, windowKey);
  return windowKey;
}

/** Opens a gone window again, on its tabs. */
export function reopenWindow(workspaceId: string, windowKey: string, tabId: string): void {
  heartbeatWindow(windowKey);
  openWindow(workspaceId, tabId, windowKey);
}

type WindowTabsInput = {
  workspaceId: string;
  tabs: readonly TradingTabState[];
  activeTabId: string;
  setActiveTab: (tabId: string) => void;
  /** False until the workspace has loaded (its tabs aren't known yet). */
  ready: boolean;
};

/**
 * This window's share of the workspace's tabs: the tabs it shows, popping one out or moving it back, and (in the
 * main window) the popped windows that are gone. A tab made in a popped window (new, duplicated, reopened) joins it.
 */
export function useWindowTabs({ workspaceId, tabs, activeTabId, setActiveTab, ready }: WindowTabsInput) {
  const windowKey = useMemo(() => currentWindowKey(), []);
  // The workspace's window set as stored on this computer, read again on every change and now and then (a popped
  // window that stops heartbeating is gone).
  const [stored, setStored] = useState(() => storedWindowSet(workspaceId));
  useEffect(() => {
    const refresh = () => setStored(storedWindowSet(workspaceId));
    const changed = (event: Event) => {
      if (event instanceof StorageEvent && event.key !== SETS_KEY && event.key !== ALIVE_KEY) return;
      refresh();
    };
    refresh();
    window.addEventListener('storage', changed);
    window.addEventListener(CHANGED_EVENT, changed);
    const stopTicker = startTicker(refresh, HEARTBEAT_MS);
    return () => {
      window.removeEventListener('storage', changed);
      window.removeEventListener(CHANGED_EVENT, changed);
      stopTicker();
    };
  }, [workspaceId]);
  useEffect(() => {
    if (windowKey === MAIN_WINDOW) return undefined;
    heartbeatWindow(windowKey);
    const stopTicker = startTicker(() => heartbeatWindow(windowKey), HEARTBEAT_MS);
    const closing = (event: PageTransitionEvent) => { if (!event.persisted) heartbeatWindow(windowKey, 0); };
    window.addEventListener('pagehide', closing);
    return () => {
      stopTicker();
      window.removeEventListener('pagehide', closing);
    };
  }, [windowKey]);

  const assignments = stored.assignments;
  const visibleTabs = useMemo(() => tabsForWindow(tabs, assignments, windowKey), [tabs, assignments, windowKey]);

  useEffect(() => {
    if (!ready) return;
    pruneAssignments(workspaceId, tabs.map((tab) => tab.tabId));
  }, [ready, tabs, workspaceId]);
  // The tabs this window already knew: a tab it hadn't seen is one made here (it joins this window); a known one that
  // left (moved to another window) makes the window show another of its own.
  const known = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!ready) return;
    const seen = known.current;
    known.current = new Set(tabs.map((tab) => tab.tabId));
    if (visibleTabs.some((tab) => tab.tabId === activeTabId)) return;
    const madeHere = seen !== null && !seen.has(activeTabId) && tabs.some((tab) => tab.tabId === activeTabId);
    if (windowKey !== MAIN_WINDOW && madeHere) {
      assignTab(workspaceId, activeTabId, windowKey);
      return;
    }
    if (visibleTabs[0]) setActiveTab(visibleTabs[0].tabId);
    // A popped window whose tabs all went (moved back, brought back, closed) closes.
    else if (windowKey !== MAIN_WINDOW && seen !== null) window.close();
  }, [activeTabId, ready, setActiveTab, tabs, visibleTabs, windowKey, workspaceId]);

  const popOut = useCallback((tabId: string) => { popOutTab(workspaceId, tabId); }, [workspaceId]);
  const moveToMain = useCallback((tabId: string) => {
    assignTab(workspaceId, tabId, MAIN_WINDOW);
    // A popped window left without tabs closes (a window the page opened itself may).
    if (windowKey !== MAIN_WINDOW && (windowAssignments(workspaceId)[windowKey] ?? []).length === 0) window.close();
  }, [windowKey, workspaceId]);
  const gone = useMemo(() => (windowKey === MAIN_WINDOW && ready ? stored.gone : []), [ready, windowKey, stored]);
  return {
    windowKey,
    visibleTabs,
    popOut,
    moveToMain: windowKey === MAIN_WINDOW ? undefined : moveToMain,
    goneWindows: gone,
    reopen: (windowKeyToOpen: string, tabId: string) => reopenWindow(workspaceId, windowKeyToOpen, tabId),
    bringBack: (windowKeyToForget: string) => forgetWindow(workspaceId, windowKeyToForget),
  };
}
