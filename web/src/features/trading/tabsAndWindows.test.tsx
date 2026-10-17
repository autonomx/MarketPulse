import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emitOmnixEvent } from '../../events/bus';
import { useChartTimeSync } from './chartTimeSync';
import { tradingDraftRecovery } from './persistence/draftRecovery';
import { parseTradingWorkspace, serializeTradingWorkspace } from './persistence/workspaceDocument';
import { useTradingWorkspacePersistence } from './persistence/useTradingWorkspacePersistence';
import { tradingApi } from './tradingApi';
import { buildTradingWorkspaceExport } from './tradingExport';
import { TradingSessionTabs } from './TradingSessionTabs';
import { useTradingStore } from './tradingStore';
import type { TradingDocument } from './tradingTypes';
import {
  HEARTBEAT_MS, PRESENCE_TTL_MS, TradingWindowPresence, forgetRequestedTradingWindow, requestedTradingWindow, tradingWindowPresence, tradingWindowUrl, useTradingWindowPresence,
} from './windowPresence';

const initialStore = useTradingStore.getState();

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useTradingStore.setState(initialStore, true);
  window.history.replaceState(null, '', '/');
});

/** Two presences on an in-memory channel, with a shared clock; both joined. */
function pairedWindows(clock = { now: 1_000 }) {
  const peers: TradingWindowPresence[] = [];
  const channel = (index: number) => ({
    postMessage: (message: unknown) => peers.forEach((peer, at) => at !== index && peer.receive(message as never)),
    onmessage: null,
  }) as unknown as BroadcastChannel;
  peers.push(new TradingWindowPresence(channel(0), () => clock.now));
  peers.push(new TradingWindowPresence(channel(1), () => clock.now));
  peers.forEach((peer) => peer.join());
  return peers;
}

describe('trading windows (TVP-4.3)', () => {
  it('rings alerts in the most recently focused window only, and in the next one when it leaves', () => {
    const [first, second] = pairedWindows();
    first.focus(100);
    second.focus(200);
    expect([first.isAlertWindow(), second.isAlertWindow()]).toEqual([false, true]);
    first.focus(300);
    expect([first.isAlertWindow(), second.isAlertWindow()]).toEqual([true, false]);
    first.leave();
    expect(second.isAlertWindow()).toBe(true);
    expect(new TradingWindowPresence(null).isAlertWindow()).toBe(true);
  });

  it('forgets a window that stopped speaking (crashed or frozen), and keeps one that heartbeats', () => {
    const clock = { now: 1_000 };
    const [first, second] = pairedWindows(clock);
    first.focus(2_000);
    expect(second.isAlertWindow()).toBe(false);
    clock.now += HEARTBEAT_MS;
    first.heartbeat();
    clock.now += HEARTBEAT_MS;
    expect(second.isAlertWindow()).toBe(false);
    // No heartbeat for longer than the time-to-live: gone.
    clock.now += PRESENCE_TTL_MS + 1;
    expect(second.isAlertWindow()).toBe(true);
  });

  it('takes part while the Trading workspace is mounted', () => {
    const presence = tradingWindowPresence();
    const join = vi.spyOn(presence, 'join');
    const leave = vi.spyOn(presence, 'leave');
    const view = renderHook(() => useTradingWindowPresence());
    expect(join).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(leave).toHaveBeenCalledTimes(1);
  });

  it('tells the other windows when a document was saved', () => {
    const [first, second] = pairedWindows();
    const heard = vi.fn();
    const own = vi.fn();
    second.onOtherWindowSaved(heard);
    first.onOtherWindowSaved(own);
    first.announceSaved('watchlist', 'default', 7);
    expect(heard).toHaveBeenCalledWith('watchlist', 'default', 7);
    expect(own).not.toHaveBeenCalled();
  });

  it('opens a workspace and tab by URL', () => {
    const url = tradingWindowUrl('swing-1a2b', 'tab-3', { origin: 'https://omnix.test', pathname: '/trading' });
    expect(url).toBe('https://omnix.test/trading?workspace=swing-1a2b&tab=tab-3');
    expect(requestedTradingWindow('?workspace=swing-1a2b&tab=tab-3')).toEqual({ workspaceId: 'swing-1a2b', tabId: 'tab-3' });
    expect(requestedTradingWindow('')).toEqual({ workspaceId: null, tabId: null });
  });

  it('forgets the opening tab, and the workspace too unless the window is a popped-out one (TVP-4.6)', () => {
    window.history.replaceState(null, '', '/trading?workspace=swing&tab=tab-3');
    forgetRequestedTradingWindow();
    expect(window.location.search).toBe('');
    window.history.replaceState(null, '', '/trading?workspace=swing&tab=tab-3&window=w-abc12345');
    forgetRequestedTradingWindow();
    expect(window.location.search).toBe('?workspace=swing&window=w-abc12345');
  });

  it('offers to pop a tab out, or move it back from a popped-out window (TVP-4.6)', () => {
    const onPopOut = vi.fn();
    const onMoveToMain = vi.fn();
    const tabs = useTradingStore.getState().tabs;
    render(<TradingSessionTabs tabs={tabs} activeTabId={tabs[0].tabId} canAdd getTabLabel={(tab) => tab.name} onSelect={vi.fn()} onClose={vi.fn()} workspaceId="main" onPopOut={onPopOut} onMoveToMain={onMoveToMain} />);
    fireEvent.click(screen.getByRole('button', { name: `More actions for ${tabs[0].name} chart session` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pop out to a new window' }));
    expect(onPopOut).toHaveBeenCalledWith(tabs[0].tabId);
    fireEvent.click(screen.getByRole('button', { name: `More actions for ${tabs[0].name} chart session` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to the main window' }));
    expect(onMoveToMain).toHaveBeenCalledWith(tabs[0].tabId);
  });
});

describe('tab management (TVP-4.4)', () => {
  it('duplicates a tab next to it with new chart ids, and adds a blank tab', () => {
    const store = useTradingStore.getState();
    store.updateChart(store.activeChartId, { interval: '15m' });
    const original = useTradingStore.getState().tabs[0];
    const copyId = useTradingStore.getState().duplicateTab(original.tabId)!;
    const state = useTradingStore.getState();
    expect(state.tabs.map((tab) => tab.tabId)).toEqual([original.tabId, copyId]);
    const copy = state.tabs[1];
    expect(copy.name).toBe(`${original.name} copy`);
    expect(copy.charts[0].interval).toBe('15m');
    expect(copy.charts[0].chartId).not.toBe(original.charts[0].chartId);
    expect(state.activeTabId).toBe(copyId);
    const blankId = useTradingStore.getState().addBlankTab()!;
    const blank = useTradingStore.getState().tabs.find((tab) => tab.tabId === blankId)!;
    expect(blank.charts).toHaveLength(1);
    expect(blank.charts[0].interval).toBe('1h');
  });

  it('offers new, duplicate, reopen, a new window, saved layouts and tools from +', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    vi.spyOn(tradingApi, 'allDocuments').mockResolvedValue([]);
    const onSelectWorkspace = vi.fn();
    const onOpenTool = vi.fn();
    const ui = () => {
      const state = useTradingStore.getState();
      return (
        <TradingSessionTabs
          tabs={state.tabs}
          activeTabId={state.activeTabId}
          canAdd
          getTabLabel={(tab) => tab.name}
          onSelect={state.setActiveTab}
          onClose={(tab) => state.removeTab(tab.tabId)}
          workspaceId="main"
          workspaces={[{ workspaceId: 'main', name: 'Main' }, { workspaceId: 'swing', name: 'Swing' }]}
          onSelectWorkspace={onSelectWorkspace}
          onOpenTool={onOpenTool}
        />
      );
    };
    const view = render(ui());
    fireEvent.click(screen.getByRole('button', { name: 'Create chart session tab' }));
    const menu = screen.getByRole('menu', { name: 'New tab' });
    expect(within(menu).getByRole('menuitem', { name: 'Reopen closed tab' })).toBeDisabled();
    expect(within(menu).getByRole('menuitem', { name: 'Main (open)' })).toBeDisabled();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'New chart tab' }));
    expect(useTradingStore.getState().tabs).toHaveLength(2);
    view.rerender(ui());
    fireEvent.click(screen.getByRole('button', { name: 'Create chart session tab' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Swing' }));
    expect(onSelectWorkspace).toHaveBeenCalledWith('swing');
    fireEvent.click(screen.getByRole('button', { name: 'Create chart session tab' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Screener' }));
    expect(onOpenTool).toHaveBeenCalledWith('scanner');
    fireEvent.click(screen.getByRole('button', { name: 'Create chart session tab' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Open this tab in a new window' }));
    expect(open).toHaveBeenCalledWith(expect.stringContaining(`workspace=main&tab=${useTradingStore.getState().activeTabId}`), '_blank', expect.stringContaining('popup'));
    // Right-click a tab: duplicate it (after its drawings are copied).
    const firstTab = screen.getAllByRole('button', { name: /^Open .* chart session$/ })[0].parentElement!;
    fireEvent.contextMenu(firstTab);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Duplicate' }));
    await waitFor(() => expect(useTradingStore.getState().tabs).toHaveLength(3));
    // Escape closes a menu and gives focus back to its trigger; a second press on the trigger closes it too.
    view.rerender(ui());
    const launcher = () => screen.getByRole('button', { name: 'Create chart session tab' });
    fireEvent.click(launcher());
    expect(document.activeElement?.getAttribute('role')).toBe('menuitem');
    fireEvent.keyDown(document, { key: 'ArrowDown' });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(launcher());
    fireEvent.click(launcher());
    fireEvent.pointerDown(launcher());
    fireEvent.click(launcher());
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('time sync (TVP-4.2)', () => {
  it('scrolls the other charts to a clicked bar while the Time link is on', () => {
    const clicks: Array<(timeMs: number) => void> = [];
    const adapter = { onBarClick: (listener: (timeMs: number) => void) => { clicks.push(listener); return () => undefined; } };
    const goA = vi.fn();
    const goB = vi.fn();
    renderHook(() => useChartTimeSync('chart-a', adapter, goA));
    renderHook(() => useChartTimeSync('chart-b', null, goB));
    expect(clicks).toHaveLength(0);
    act(() => useTradingStore.getState().setLink('time', true));
    expect(clicks).toHaveLength(1);
    act(() => clicks[0](1_700_000_000_000));
    expect(goB).toHaveBeenCalledWith(1_700_000_000_000);
    expect(goA).not.toHaveBeenCalled();
    act(() => useTradingStore.getState().setLink('time', false));
    act(() => emitOmnixEvent('omnix:trading-time-sync', { sourceChartId: 'chart-a', timeMs: 1 }));
    expect(goB).toHaveBeenCalledTimes(1);
  });

  it('keeps the Time link in saved workspaces, and reads older ones without it', () => {
    const state = useTradingStore.getState();
    const payload = serializeTradingWorkspace({
      name: 'Main', layout: state.layout, activeChartId: state.activeChartId, charts: state.charts, links: { ...state.links, time: true },
      panels: state.panels, favoriteInstrumentIds: [], favoriteIntervals: [], activeTabId: state.activeTabId, tabs: state.tabs,
    });
    expect(parseTradingWorkspace(payload)?.links.time).toBe(true);
    const older = { ...payload, links: { instrument: false, interval: false, crosshair: true, visibleRange: false } };
    expect(parseTradingWorkspace(older)?.links.time).toBeUndefined();
    expect(parseTradingWorkspace({ ...payload, links: { ...payload.links, time: 'yes' } })).toBeNull();
  });
});

describe('workspace persistence across windows', () => {
  let records: TradingDocument[];
  const documentOf = (recordId: string, revision: number, tabName: string): TradingDocument => {
    const state = useTradingStore.getState();
    const tabs = state.tabs.map((tab) => ({ ...tab, name: tabName }));
    return {
      record_id: recordId,
      record_type: 'workspace',
      revision,
      status: 'active',
      updated_at: null,
      payload: serializeTradingWorkspace({
        name: recordId, layout: state.layout, activeChartId: state.activeChartId, charts: state.charts, links: state.links,
        panels: state.panels, favoriteInstrumentIds: [], favoriteIntervals: [], activeTabId: state.activeTabId, tabs,
      }) as unknown as Record<string, unknown>,
    };
  };

  beforeEach(() => {
    vi.spyOn(tradingDraftRecovery, 'load').mockResolvedValue(null);
    vi.spyOn(tradingDraftRecovery, 'save').mockResolvedValue(undefined);
    vi.spyOn(tradingDraftRecovery, 'clear').mockResolvedValue(undefined);
    records = [documentOf('main', 1, 'Main tab'), documentOf('swing', 4, 'Swing tab')];
    vi.spyOn(tradingApi, 'documents').mockImplementation(async () => records);
  });

  it('opens the workspace the window was opened on', async () => {
    window.history.replaceState(null, '', '/trading?workspace=swing');
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    expect(result.current.activeWorkspaceId).toBe('swing');
    expect(useTradingStore.getState().tabs[0].name).toBe('Swing tab');
  });

  it('reloads the workspace another window saved, unless it has edits of its own', async () => {
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    records = [documentOf('main', 2, 'Renamed elsewhere'), records[1]];
    act(() => tradingWindowPresence().receive({ type: 'saved', windowId: 'other', kind: 'workspace', id: 'main', revision: 2 }));
    await waitFor(() => expect(useTradingStore.getState().tabs[0].name).toBe('Renamed elsewhere'));
  });

  it('asks before leaving with unsaved edits', async () => {
    vi.spyOn(tradingApi, 'updateDocument').mockImplementation(() => new Promise(() => undefined));
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    act(() => useTradingStore.getState().renameTab(useTradingStore.getState().activeTabId, 'Edited'));
    await waitFor(() => expect(result.current.status).toBe('draft'));
    const dirty = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it('imports an exported workspace as a new workspace, keeping the favourites, and refuses other files', async () => {
    const created = vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (_kind, recordId, payload) => ({
      record_id: recordId, record_type: 'workspace', revision: 1, status: 'active', updated_at: null, payload,
    }));
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    act(() => useTradingStore.setState({ favoriteInstrumentIds: ['alpaca:stock:SPY'] }));
    const state = useTradingStore.getState();
    const exported = buildTradingWorkspaceExport({
      layout: 'columns-2', activeChartId: state.activeChartId,
      charts: [state.charts[0], { ...state.charts[0], chartId: 'chart-imported', interval: '1h' }], links: state.links,
    });

    expect(await result.current.importWorkspace({ hello: 'world' }, 'Nope')).toBe('invalid');
    expect(created).not.toHaveBeenCalled();
    let outcome = '';
    await act(async () => { outcome = await result.current.importWorkspace(JSON.parse(JSON.stringify(exported)), ' From file '); });
    expect(outcome).toBe('imported');
    const [, recordId, payload] = created.mock.calls[0];
    expect(recordId).toMatch(/^from-file-/);
    expect(parseTradingWorkspace(payload)?.name).toBe('From file');
    expect(result.current.activeWorkspaceId).toBe(recordId);
    expect(result.current.workspaces.map((workspace) => workspace.name)).toContain('From file');
    expect(useTradingStore.getState().layout).toBe('columns-2');
    expect(useTradingStore.getState().charts.map((chart) => chart.chartId)).toContain('chart-imported');
    expect(useTradingStore.getState().favoriteInstrumentIds).toEqual(['alpaca:stock:SPY']);
  });
});

describe('duplicating a tab with its drawings (TVP-4.4)', () => {
  it('copies the tab\u2019s shared and per-chart drawing documents to the copy', async () => {
    const { tabDrawingCopies } = await import('./persistence/duplicateWorkspace');
    const doc = (recordId: string) => ({ record_id: recordId, record_type: 'drawing', revision: 1, status: 'active', updated_at: null, payload: { drawings: [{ drawingId: 'd' }] } }) as TradingDocument;
    const copies = tabDrawingCopies(
      [doc('ws-tab-1-instrument-btc'), doc('ws-tab-1-chart-chart-1-instrument-btc'), doc('ws-tab-2-instrument-btc'), { ...doc('ws-tab-1-instrument-eth'), payload: { drawings: [] } }],
      'ws', 'tab-1', 'tab-9', new Map([['chart-1', 'tab-9-chart-1']]),
    );
    expect(copies.map((copy) => copy.recordId)).toEqual(['ws-tab-9-instrument-btc', 'ws-tab-9-chart-tab-9-chart-1-instrument-btc']);
  });
});

describe('workspace saves across windows (TVP-4.3 review)', () => {
  it('saves nothing for a change that leaves the workspace as saved, such as replay', async () => {
    vi.spyOn(tradingDraftRecovery, 'load').mockResolvedValue(null);
    vi.spyOn(tradingDraftRecovery, 'save').mockResolvedValue(undefined);
    vi.spyOn(tradingDraftRecovery, 'clear').mockResolvedValue(undefined);
    const state = useTradingStore.getState();
    const record = {
      record_id: 'main', record_type: 'workspace', revision: 1, status: 'active', updated_at: null,
      payload: serializeTradingWorkspace({
        name: 'Main', layout: state.layout, activeChartId: state.activeChartId, charts: state.charts, links: state.links,
        panels: state.panels, favoriteInstrumentIds: [], favoriteIntervals: [], activeTabId: state.activeTabId, tabs: state.tabs,
      }) as unknown as Record<string, unknown>,
    } as TradingDocument;
    vi.spyOn(tradingApi, 'documents').mockResolvedValue([record]);
    const update = vi.spyOn(tradingApi, 'updateDocument');
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    act(() => useTradingStore.getState().setReplayMode(true));
    expect(result.current.status).toBe('saved');
    expect(update).not.toHaveBeenCalled();
  });

  it('keeps an edit made while another window\u2019s save loads, so it meets the revision conflict', async () => {
    vi.spyOn(tradingDraftRecovery, 'load').mockResolvedValue(null);
    vi.spyOn(tradingDraftRecovery, 'save').mockResolvedValue(undefined);
    vi.spyOn(tradingDraftRecovery, 'clear').mockResolvedValue(undefined);
    const state = useTradingStore.getState();
    const payload = (name: string) => serializeTradingWorkspace({
      name, layout: state.layout, activeChartId: state.activeChartId, charts: state.charts, links: state.links,
      panels: state.panels, favoriteInstrumentIds: [], favoriteIntervals: [], activeTabId: state.activeTabId, tabs: state.tabs,
    }) as unknown as Record<string, unknown>;
    const record = (revision: number, name: string) => ({ record_id: 'main', record_type: 'workspace', revision, status: 'active', updated_at: null, payload: payload(name) }) as TradingDocument;
    let release: (records: TradingDocument[]) => void = () => undefined;
    const documents = vi.spyOn(tradingApi, 'documents').mockResolvedValueOnce([record(1, 'Main')]);
    const update = vi.spyOn(tradingApi, 'updateDocument').mockResolvedValue(record(3, 'Main'));
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    documents.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    act(() => tradingWindowPresence().receive({ type: 'saved', windowId: 'other', kind: 'workspace', id: 'main', revision: 2 }));
    act(() => useTradingStore.getState().renameTab(useTradingStore.getState().activeTabId, 'Edited here'));
    await act(async () => { release([record(2, 'Main')]); });
    // The edit saves on the revision this window loaded (1): the server answers with a conflict, nothing is lost.
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1].revision).toBe(1);
  });
});

describe('TVP-4.3 re-review fixes', () => {
  it('lets a shown window ring while the most recently focused one is hidden', () => {
    const clock = { now: 1_000 };
    const [main, popup] = pairedWindows(clock);
    popup.focus(1_500);
    main.focus(2_000);
    expect([main.isAlertWindow(), popup.isAlertWindow()]).toEqual([true, false]);
    // The main window goes to another browser tab: hidden, it doesn't poll alerts.
    main.setVisible(false);
    expect([main.isAlertWindow(), popup.isAlertWindow()]).toEqual([false, true]);
    // With none shown, the most recently focused one rings, as before.
    popup.setVisible(false);
    expect([main.isAlertWindow(), popup.isAlertWindow()]).toEqual([true, false]);
  });

  it('keeps replay, closed tabs and the shown tab through another window\u2019s save, and then saves nothing for a session-only change', async () => {
    vi.spyOn(tradingDraftRecovery, 'load').mockResolvedValue(null);
    vi.spyOn(tradingDraftRecovery, 'save').mockResolvedValue(undefined);
    vi.spyOn(tradingDraftRecovery, 'clear').mockResolvedValue(undefined);
    // Two tabs, the stored one showing the first.
    const store = useTradingStore.getState();
    store.addBlankTab();
    const [first, second] = useTradingStore.getState().tabs.map((tab) => tab.tabId);
    useTradingStore.getState().setActiveTab(first);
    const payload = (name: string) => {
      const state = useTradingStore.getState();
      return serializeTradingWorkspace({
        name, layout: state.layout, activeChartId: state.activeChartId, charts: state.charts, links: state.links,
        panels: state.panels, favoriteInstrumentIds: [], favoriteIntervals: [], activeTabId: first, tabs: state.tabs.map((tab) => ({ ...tab, name: tab.tabId === second ? name : tab.name })),
      }) as unknown as Record<string, unknown>;
    };
    const record = (revision: number, name: string) => ({ record_id: 'main', record_type: 'workspace', revision, status: 'active', updated_at: null, payload: payload(name) }) as TradingDocument;
    let records = [record(1, 'Second')];
    vi.spyOn(tradingApi, 'documents').mockImplementation(async () => records);
    const update = vi.spyOn(tradingApi, 'updateDocument');
    // This window was opened on the second tab; it replays and has a closed tab to reopen (session-only state).
    window.history.replaceState(null, '', `/trading?tab=${second}`);
    const { result } = renderHook(() => useTradingWorkspacePersistence());
    await waitFor(() => expect(result.current.status).toBe('saved'));
    expect(useTradingStore.getState().activeTabId).toBe(second);
    act(() => useTradingStore.setState({ replayMode: true, closedTabs: [{ tab: useTradingStore.getState().tabs[0], index: 0 }] }));
    expect(result.current.status).toBe('saved');
    records = [record(2, 'Renamed elsewhere')];
    act(() => tradingWindowPresence().receive({ type: 'saved', windowId: 'other', kind: 'workspace', id: 'main', revision: 2 }));
    await waitFor(() => expect(useTradingStore.getState().tabs[1].name).toBe('Renamed elsewhere'));
    const state = useTradingStore.getState();
    expect([state.activeTabId, state.replayMode, state.closedTabs.length]).toEqual([second, true, 1]);
    act(() => useTradingStore.getState().setReplayMode(false));
    expect(result.current.status).toBe('saved');
    expect(update).not.toHaveBeenCalled();
  });

  it('duplicates once for a double click', async () => {
    let release: (records: TradingDocument[]) => void = () => undefined;
    vi.spyOn(tradingApi, 'allDocuments').mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const { duplicateTradingTab } = await import('./persistence/duplicateWorkspace');
    const source = useTradingStore.getState().activeTabId;
    const first = duplicateTradingTab(source);
    const second = duplicateTradingTab(source);
    await waitFor(() => expect(tradingApi.allDocuments).toHaveBeenCalled());
    await act(async () => { release([]); await first; await second; });
    expect(await second).toBeNull();
    expect(useTradingStore.getState().tabs).toHaveLength(2);
  });
});
