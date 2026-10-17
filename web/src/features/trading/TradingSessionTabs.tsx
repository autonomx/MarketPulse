import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { duplicateTradingTab } from './persistence/duplicateWorkspace';
import { useTradingStore, type TradingTabState } from './tradingStore';
import { openTradingWindow } from './windowPresence';

function isGeneratedTabName(name: string): boolean {
  return name === 'Main Session' || /^Session \d+$/.test(name);
}

/** Tools the new-tab launcher opens (TVP-4.4). */
export type TradingLauncherTool = 'scanner' | 'replay' | 'strategies' | 'tester' | 'seasonals' | 'heatmap' | 'financials' | 'calendar' | 'events' | 'overview' | 'script-screener' | 'yield-curve' | 'options' | 'paper';

const LAUNCHER_TOOLS: ReadonlyArray<{ tool: TradingLauncherTool; label: string }> = [
  { tool: 'scanner', label: 'Screener' },
  { tool: 'replay', label: 'Backtest and replay' },
  { tool: 'strategies', label: 'Strategies' },
  { tool: 'tester', label: 'Strategy tester' },
  { tool: 'seasonals', label: 'Seasonals' },
  { tool: 'heatmap', label: 'Heatmap' },
  { tool: 'financials', label: 'Financials' },
  { tool: 'calendar', label: 'Economic calendar' },
  { tool: 'events', label: 'Earnings & dividends' },
  { tool: 'overview', label: 'Advanced view' },
  { tool: 'script-screener', label: 'Script screener' },
  { tool: 'yield-curve', label: 'Yield curve' },
  { tool: 'options', label: 'Options' },
  { tool: 'paper', label: 'Paper trading' },
];

type MenuItem = { label: string; onSelect: () => void; disabled?: boolean } | { heading: string };

/**
 * A small popup menu, rendered on the page body (the tab strip clips its overflow) under its trigger. It closes on a
 * choice, Escape, or a press outside it and its trigger (a second press on the trigger toggles it closed); arrow keys
 * move between items; focus goes back to the trigger when it closes.
 */
function TabMenu({ label, items, trigger, onClose }: { label: string; items: readonly MenuItem[]; trigger: HTMLElement | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  const rect = trigger?.getBoundingClientRect();
  const left = rect ? Math.max(4, Math.min(rect.left, window.innerWidth - 240)) : 0;
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (ref.current?.contains(target) || trigger?.contains(target)) return;
      close.current();
    };
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close.current();
        trigger?.focus();
        return;
      }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const buttons = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
      if (buttons.length === 0) return;
      event.preventDefault();
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'ArrowDown' ? (at + 1) % buttons.length : (at - 1 + buttons.length) % buttons.length;
      buttons[next].focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keys);
    ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', keys);
    };
  }, [trigger]);
  return createPortal(
    <div
      className="trading-session-tab-menu"
      role="menu"
      aria-label={label}
      ref={ref}
      style={{ position: 'fixed', top: rect ? rect.bottom + 4 : 0, left }}
    >
      {items.map((item, index) => ('heading' in item
        ? <small key={`h-${item.heading}`} role="presentation">{item.heading}</small>
        : (
          <button
            key={`${index}-${item.label}`}
            type="button"
            role="menuitem"
            disabled={item.disabled}
            onClick={() => {
              close.current();
              trigger?.focus();
              item.onSelect();
            }}
          >
            {item.label}
          </button>
        )))}
    </div>,
    document.body,
  );
}

type SessionTabsProps = {
  tabs: readonly TradingTabState[];
  activeTabId: string;
  canAdd: boolean;
  getTabLabel: (tab: TradingTabState) => string;
  onSelect: (tabId: string) => void;
  onClose: (tab: TradingTabState) => void;
  /** The open workspace, for "Open in a new window" (TVP-4.3). */
  workspaceId?: string;
  /** Saved layouts the launcher lists. */
  workspaces?: ReadonlyArray<{ workspaceId: string; name: string }>;
  onSelectWorkspace?: (workspaceId: string) => void;
  onOpenTool?: (tool: TradingLauncherTool) => void;
  /** Moves a tab into a browser window of its own (TVP-4.6). */
  onPopOut?: (tabId: string) => void;
  /** In a popped-out window: moves a tab back to the main window. */
  onMoveToMain?: (tabId: string) => void;
};

/** The "+" launcher's entries (TVP-4.4): new, duplicate, reopen, a new window, saved layouts and tools. */
function launcherItems(props: SessionTabsProps, closedTabs: number): MenuItem[] {
  const { activeTabId, canAdd, workspaceId, workspaces = [], onSelectWorkspace, onOpenTool } = props;
  const store = useTradingStore.getState;
  return [
    { label: 'New chart tab', onSelect: () => store().addBlankTab(), disabled: !canAdd },
    { label: 'Duplicate this tab', onSelect: () => void duplicateTradingTab(activeTabId), disabled: !canAdd },
    { label: `Reopen closed tab${closedTabs ? ` (${closedTabs})` : ''}`, onSelect: () => store().reopenClosedTab(), disabled: !canAdd || closedTabs === 0 },
    ...(workspaceId ? [{ label: 'Open this tab in a new window', onSelect: () => openTradingWindow(workspaceId, activeTabId) }] : []),
    ...(workspaces.length > 0 && onSelectWorkspace ? [
      { heading: 'Saved layouts' },
      ...workspaces.map((workspace) => ({
        label: workspace.workspaceId === workspaceId ? `${workspace.name} (open)` : workspace.name,
        onSelect: () => onSelectWorkspace(workspace.workspaceId),
        disabled: workspace.workspaceId === workspaceId,
      })),
    ] : []),
    ...(onOpenTool ? [{ heading: 'Tools' }, ...LAUNCHER_TOOLS.map(({ tool, label }) => ({ label, onSelect: () => onOpenTool(tool) }))] : []),
  ];
}

export function TradingSessionTabs(props: SessionTabsProps) {
  const { tabs, activeTabId, canAdd, getTabLabel, onSelect, onClose, workspaceId, onPopOut, onMoveToMain } = props;
  const renameTab = useTradingStore((state) => state.renameTab);
  const reopenClosedTab = useTradingStore((state) => state.reopenClosedTab);
  const closedTabs = useTradingStore((state) => state.closedTabs.length);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [menuTabId, setMenuTabId] = useState<string | null>(null);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const tabMenuTriggers = useRef(new Map<string, HTMLElement>());

  const renameSession = (tab: TradingTabState, fallbackLabel: string) => {
    const currentName = isGeneratedTabName(tab.name) ? fallbackLabel : tab.name;
    const nextName = window.prompt('Rename chart session', currentName);
    if (nextName?.trim()) renameTab(tab.tabId, nextName);
  };

  const tabMenuItems = (tab: TradingTabState, fallbackLabel: string): MenuItem[] => [
    { label: 'Duplicate', onSelect: () => void duplicateTradingTab(tab.tabId), disabled: !canAdd },
    ...(workspaceId ? [{ label: 'Open in a new window', onSelect: () => openTradingWindow(workspaceId, tab.tabId) }] : []),
    ...(onPopOut ? [{ label: 'Pop out to a new window', onSelect: () => onPopOut(tab.tabId), disabled: tabs.length <= 1 && !onMoveToMain }] : []),
    ...(onMoveToMain ? [{ label: 'Move to the main window', onSelect: () => onMoveToMain(tab.tabId) }] : []),
    { label: 'Rename…', onSelect: () => renameSession(tab, fallbackLabel) },
    { label: 'Close', onSelect: () => onClose(tab), disabled: tabs.length <= 1 },
    { label: 'Reopen closed tab', onSelect: () => reopenClosedTab(), disabled: !canAdd || closedTabs === 0 },
  ];

  return (
    <nav className="trading-session-tabs" aria-label="Trading chart sessions">
      <div className="trading-session-tabs-scroll" role="group" aria-label="Independent chart sessions">
        {tabs.map((tab, index) => {
          const fallbackLabel = getTabLabel(tab);
          const label = isGeneratedTabName(tab.name) ? fallbackLabel : tab.name;
          const title = label === fallbackLabel ? `${label} chart session` : `${label} · ${fallbackLabel}`;
          return (
          <div
            className={`trading-session-tab${tab.tabId === activeTabId ? ' active' : ''}`}
            key={tab.tabId}
            onContextMenu={(event) => {
              event.preventDefault();
              setMenuTabId(tab.tabId);
            }}
          >
            <button
              type="button"
              aria-current={tab.tabId === activeTabId ? 'true' : undefined}
              aria-label={`Open ${label} chart session`}
              onClick={() => onSelect(tab.tabId)}
              onDoubleClick={() => renameSession(tab, fallbackLabel)}
              title={`${title} · Double-click to rename · Right-click for more`}
            >
              <span className="trading-session-tab-dot" aria-hidden="true" />
              <span className="trading-session-tab-name">{label}</span>
              {tab.tabId === activeTabId ? <span className="trading-session-tab-state" aria-hidden="true" /> : null}
            </button>
            <button
              type="button"
              ref={(element) => {
                if (element) tabMenuTriggers.current.set(tab.tabId, element);
                else tabMenuTriggers.current.delete(tab.tabId);
              }}
              className="trading-session-tab-close trading-session-tab-rename"
              aria-label={`More actions for ${label} chart session`}
              aria-haspopup="menu"
              aria-expanded={menuTabId === tab.tabId}
              title="Duplicate, open in a new window, rename"
              onClick={() => setMenuTabId((current) => (current === tab.tabId ? null : tab.tabId))}
            >
              ⋯
            </button>
            <button
              type="button"
              className="trading-session-tab-close"
              aria-label={`Close ${label} chart session`}
              onClick={() => onClose(tab)}
              disabled={tabs.length <= 1}
            >
              ×
            </button>
            {menuTabId === tab.tabId ? (
              <TabMenu label={`${label} chart session actions`} items={tabMenuItems(tab, fallbackLabel)} trigger={tabMenuTriggers.current.get(tab.tabId) ?? null} onClose={() => setMenuTabId(null)} />
            ) : null}
            {index === 0 ? <span className="trading-session-tab-divider" aria-hidden="true" /> : null}
          </div>
          );
        })}
      </div>
      <div className="trading-session-tab-launcher">
        <button
          type="button"
          ref={launcherRef}
          className="trading-session-tab-add"
          aria-label="Create chart session tab"
          aria-haspopup="menu"
          aria-expanded={launcherOpen}
          onClick={() => setLauncherOpen((open) => !open)}
        >
          +
        </button>
        {launcherOpen ? <TabMenu label="New tab" items={launcherItems(props, closedTabs)} trigger={launcherRef.current} onClose={() => setLauncherOpen(false)} /> : null}
      </div>
      <span className="trading-session-tab-hint">Each tab keeps its own chart layout, indicators, drawings, and settings</span>
    </nav>
  );
}
