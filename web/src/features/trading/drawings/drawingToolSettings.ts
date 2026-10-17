// Drawing tool behaviour (TVP-3.8), as the toggles at the bottom of
// TradingView's drawing toolbar. Kept in this browser.
//
// - stayInDrawingMode: a finished drawing keeps its tool selected instead of
//   going back to the cursor;
// - lockAll: no drawing can be moved or edited on the chart (each keeps its
//   own lock for when this is off);
// - favoritesBar: the favourite tools' floating toolbar is shown;
// - syncDrawings: drawings are shared by every chart of the tab showing the
//   symbol; off, each chart keeps its own.

export type DrawingToolSettings = { stayInDrawingMode: boolean; lockAll: boolean; favoritesBar: boolean; syncDrawings: boolean };

export const DEFAULT_DRAWING_TOOL_SETTINGS: DrawingToolSettings = { stayInDrawingMode: false, lockAll: false, favoritesBar: true, syncDrawings: true };

const STORAGE_KEY = 'omnix.trading.drawing-tool-settings';

export function loadDrawingToolSettings(): DrawingToolSettings {
  try {
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}') as Record<string, unknown>;
    const flag = (key: keyof DrawingToolSettings) => (typeof stored[key] === 'boolean' ? (stored[key] as boolean) : DEFAULT_DRAWING_TOOL_SETTINGS[key]);
    return { stayInDrawingMode: flag('stayInDrawingMode'), lockAll: flag('lockAll'), favoritesBar: flag('favoritesBar'), syncDrawings: flag('syncDrawings') };
  } catch {
    return { ...DEFAULT_DRAWING_TOOL_SETTINGS };
  }
}

export function saveDrawingToolSettings(settings: DrawingToolSettings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // The settings last for this session without storage.
  }
}

/**
 * The drawing store scope of a chart: shared by the tab's charts while drawings sync, else the chart's own. Charts
 * that stop syncing start with their own (empty) set; their shared drawings come back when sync is on again.
 */
export function drawingScopeId(tabScopeId: string | undefined, chartId: string, syncDrawings: boolean): string | undefined {
  return syncDrawings ? tabScopeId : `${tabScopeId ?? 'global'}:chart:${chartId}`;
}
