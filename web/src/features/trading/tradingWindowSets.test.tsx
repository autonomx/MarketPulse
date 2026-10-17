import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TradingTabState } from './tradingStore';
import {
  MAIN_WINDOW, assignTab, currentWindowKey, forgetWindow, goneWindows, heartbeatWindow, popOutTab, pruneAssignments, tabsForWindow,
  useWindowTabs, windowAssignments, windowUrl,
} from './tradingWindowSets';
import { TradingWindowRestore } from './TradingWindowRestore';
import { PRESENCE_TTL_MS } from './windowPresence';

const tab = (tabId: string, name = tabId) => ({ tabId, name } as TradingTabState);
const TABS = [tab('tab-1', 'Main'), tab('tab-2', 'Swing'), tab('tab-3', 'Crypto')];

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('window sets (TVP-4.6)', () => {
  it('splits a workspace’s tabs between the main window and popped-out windows', () => {
    assignTab('ws', 'tab-2', 'w-aaaaaaaa');
    expect(tabsForWindow(TABS, windowAssignments('ws'), MAIN_WINDOW).map((item) => item.tabId)).toEqual(['tab-1', 'tab-3']);
    expect(tabsForWindow(TABS, windowAssignments('ws'), 'w-aaaaaaaa').map((item) => item.tabId)).toEqual(['tab-2']);
    // Moving a tab to another window takes it from the first; the main window means no assignment.
    assignTab('ws', 'tab-2', 'w-bbbbbbbb');
    expect(windowAssignments('ws')).toEqual({ 'w-bbbbbbbb': ['tab-2'] });
    assignTab('ws', 'tab-2', MAIN_WINDOW);
    expect(windowAssignments('ws')).toEqual({});
    assignTab('ws', 'tab-3', 'w-bbbbbbbb');
    pruneAssignments('ws', ['tab-1', 'tab-2']);
    expect(windowAssignments('ws')).toEqual({});
    expect(windowAssignments('other')).toEqual({});
  });

  it('reads the window key from the URL and builds a popped window’s URL', () => {
    expect(currentWindowKey('?window=w-abc12345')).toBe('w-abc12345');
    expect(currentWindowKey('?window=../evil')).toBe(MAIN_WINDOW);
    expect(currentWindowKey('')).toBe(MAIN_WINDOW);
    expect(windowUrl('ws', 'tab-2', 'w-abc12345', { origin: 'http://x', pathname: '/trading' })).toBe('http://x/trading?workspace=ws&tab=tab-2&window=w-abc12345');
  });

  it('pops a tab out into a window of its own and finds windows that are gone', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const key = popOutTab('ws', 'tab-2');
    expect(open).toHaveBeenCalledWith(expect.stringContaining(`window=${key}`), `omnix-trading-${key}`, expect.stringContaining('popup'));
    expect(windowAssignments('ws')[key]).toEqual(['tab-2']);
    const now = Date.now();
    expect(goneWindows('ws', now)).toEqual([]);
    expect(goneWindows('ws', now + PRESENCE_TTL_MS + 1)).toEqual([{ windowKey: key, tabIds: ['tab-2'] }]);
    heartbeatWindow(key, 0); // closed
    expect(goneWindows('ws', now)).toHaveLength(1);
    forgetWindow('ws', key);
    expect(goneWindows('ws', now + PRESENCE_TTL_MS + 1)).toEqual([]);
  });

  it('keeps the main window on its own tabs, and a popped window takes the tabs made in it', () => {
    const setActiveTab = vi.fn();
    assignTab('ws', 'tab-2', 'w-aaaaaaaa');
    const main = renderHook((props: { active: string }) => useWindowTabs({ workspaceId: 'ws', tabs: TABS, activeTabId: props.active, setActiveTab, ready: true }), { initialProps: { active: 'tab-2' } });
    expect(main.result.current.visibleTabs.map((item) => item.tabId)).toEqual(['tab-1', 'tab-3']);
    expect(setActiveTab).toHaveBeenCalledWith('tab-1');
    expect(main.result.current.moveToMain).toBeUndefined();
    main.unmount();

    window.history.replaceState(null, '', '/trading?window=w-aaaaaaaa');
    setActiveTab.mockClear();
    const popped = renderHook((props: { tabs: TradingTabState[]; active: string }) => useWindowTabs({ workspaceId: 'ws', tabs: props.tabs, activeTabId: props.active, setActiveTab, ready: true }), {
      initialProps: { tabs: TABS, active: 'tab-2' },
    });
    expect(popped.result.current.visibleTabs.map((item) => item.tabId)).toEqual(['tab-2']);
    // A new tab made here joins this window.
    act(() => popped.rerender({ tabs: [...TABS, tab('tab-4')], active: 'tab-4' }));
    expect(windowAssignments('ws')['w-aaaaaaaa']).toEqual(['tab-2', 'tab-4']);
    expect(popped.result.current.visibleTabs.map((item) => item.tabId)).toEqual(['tab-2', 'tab-4']);
    // Moving one back: this window shows its other tab; moving the last back closes it.
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    act(() => popped.result.current.moveToMain?.('tab-4'));
    popped.rerender({ tabs: [...TABS, tab('tab-4')], active: 'tab-4' });
    expect(setActiveTab).toHaveBeenLastCalledWith('tab-2');
    act(() => popped.result.current.moveToMain?.('tab-2'));
    expect(close).toHaveBeenCalled();
    expect(windowAssignments('ws')).toEqual({});
  });

  it('offers to reopen a closed window or bring its tabs back', () => {
    const onReopen = vi.fn();
    const onBringBack = vi.fn();
    render(<TradingWindowRestore windows={[{ windowKey: 'w-aaaaaaaa', tabIds: ['tab-2', 'tab-gone'] }]} tabs={TABS} tabLabel={(item) => item.name} onReopen={onReopen} onBringBack={onBringBack} />);
    expect(screen.getByRole('status')).toHaveTextContent('A popped-out window is closed:Swing');
    fireEvent.click(screen.getByRole('button', { name: 'Reopen the window with Swing' }));
    expect(onReopen).toHaveBeenCalledWith('w-aaaaaaaa', 'tab-2');
    fireEvent.click(screen.getByRole('button', { name: 'Bring Swing back to this window' }));
    expect(onBringBack).toHaveBeenCalledWith('w-aaaaaaaa');
  });
});
