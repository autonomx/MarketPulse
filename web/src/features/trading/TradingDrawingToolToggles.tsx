// The bottom of the drawing toolbar (TVP-3.8): stay in drawing mode, lock
// all, hide all, drawing sync and the favourites toolbar switch, plus the
// favourites toolbar itself.
import type { DrawingTool } from './drawings/drawingCommands';
import { useTradingStore } from './tradingStore';

export type FavoriteTool = { id: string; label: string; glyph: string; tool: DrawingTool };

export function TradingDrawingToolToggles() {
  const settings = useTradingStore((state) => state.drawingToolSettings);
  const setSetting = useTradingStore((state) => state.setDrawingToolSetting);
  const drawingsHidden = useTradingStore((state) => state.drawingsHidden);
  const toggleDrawingsHidden = useTradingStore((state) => state.toggleDrawingsHidden);
  const toggles = [
    { key: 'stay', label: 'Stay in drawing mode', glyph: '✎', pressed: settings.stayInDrawingMode, toggle: () => setSetting('stayInDrawingMode', !settings.stayInDrawingMode) },
    { key: 'lock', label: 'Lock all drawings', glyph: '⚿', pressed: settings.lockAll, toggle: () => setSetting('lockAll', !settings.lockAll) },
    { key: 'hide', label: 'Hide all drawings (Ctrl+Alt+H)', glyph: '◌', pressed: drawingsHidden, toggle: toggleDrawingsHidden },
    { key: 'sync', label: 'Sync drawings between the charts of this tab', title: 'Sync drawings between the charts of this tab. Off: each chart keeps its own drawings, and the shared ones show again when sync is back on.', glyph: '⇄', pressed: settings.syncDrawings, toggle: () => setSetting('syncDrawings', !settings.syncDrawings) },
    { key: 'favorites', label: 'Show favourites toolbar', glyph: '★', pressed: settings.favoritesBar, toggle: () => setSetting('favoritesBar', !settings.favoritesBar) },
  ];
  return (
    <div className="trading-drawing-tool-toggles" role="group" aria-label="Drawing behaviour">
      {toggles.map((item) => (
        <button key={item.key} type="button" aria-label={item.label} title={'title' in item ? item.title : item.label} aria-pressed={item.pressed} className={item.pressed ? 'active' : undefined} onClick={item.toggle}>
          <span aria-hidden="true">{item.glyph}</span>
        </button>
      ))}
    </div>
  );
}

/** TradingView's favourites toolbar: the starred tools as one row of buttons. */
export function TradingDrawingFavoritesBar({ tools, selectedTool, onSelect }: {
  tools: readonly FavoriteTool[];
  selectedTool: DrawingTool;
  onSelect: (tool: DrawingTool) => void;
}) {
  const visible = useTradingStore((state) => state.drawingToolSettings.favoritesBar);
  if (!visible || tools.length === 0) return null;
  return (
    <div className="trading-drawing-favorites" role="toolbar" aria-label="Favourite drawing tools">
      {tools.map((item) => (
        <button key={item.id} type="button" aria-label={item.label} title={item.label} aria-pressed={item.tool === selectedTool} className={item.tool === selectedTool ? 'active' : undefined} onClick={() => onSelect(item.tool)}>
          <span aria-hidden="true">{item.glyph}</span>
        </button>
      ))}
    </div>
  );
}
