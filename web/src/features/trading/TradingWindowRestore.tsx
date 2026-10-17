/**
 * The main window's note about popped-out windows that are gone (TVP-4.6): reopen each (a browser opens one window
 * per click) or bring its tabs back here.
 */
import type { TradingTabState } from './tradingStore';

type Props = {
  windows: ReadonlyArray<{ windowKey: string; tabIds: string[] }>;
  tabs: readonly TradingTabState[];
  tabLabel: (tab: TradingTabState) => string;
  onReopen: (windowKey: string, tabId: string) => void;
  onBringBack: (windowKey: string) => void;
};

export function TradingWindowRestore({ windows, tabs, tabLabel, onReopen, onBringBack }: Props) {
  const shown = windows.flatMap((window) => {
    const named = window.tabIds.map((tabId) => tabs.find((tab) => tab.tabId === tabId)).filter((tab): tab is TradingTabState => tab !== undefined);
    return named.length ? [{ ...window, named }] : [];
  });
  if (shown.length === 0) return null;
  return (
    <section className="trading-window-restore" role="status" aria-label="Closed trading windows">
      <span>{shown.length === 1 ? 'A popped-out window is closed:' : `${shown.length} popped-out windows are closed:`}</span>
      {shown.map((window) => {
        const names = window.named.map(tabLabel).join(', ');
        return (
          <span key={window.windowKey} className="trading-window-restore-item">
            <strong>{names}</strong>
            <button type="button" onClick={() => onReopen(window.windowKey, window.named[0].tabId)} aria-label={`Reopen the window with ${names}`}>Reopen</button>
            <button type="button" onClick={() => onBringBack(window.windowKey)} aria-label={`Bring ${names} back to this window`}>Bring back</button>
          </span>
        );
      })}
    </section>
  );
}
