import type { TradingChartAdapter } from '../chart/chartAdapter';
import { copyDrawingsToClipboard, drawingClipboard, hasCopiedDrawings } from '../drawings/drawingClipboard';
import type { DrawingPoint, DrawingTool, TradingDrawing } from '../drawings/drawingCommands';
import type { useTradingDrawings } from '../drawings/useTradingDrawings';
import type { TradingCommandId } from './tradingCommands';
import { useTradingCommand } from './useTradingCommands';

type Drawings = Pick<ReturnType<typeof useTradingDrawings>, 'selected' | 'translate' | 'paste'>;

export type DrawingCommandModel = {
  active: boolean;
  /** Ctrl+Alt+H is on: keys don't act on drawings nobody can see. */
  drawingsHidden?: boolean;
  /** "Lock all drawings" is on (TVP-3.8): nothing moves, also not by keys. */
  drawingToolSettings?: { lockAll: boolean };
  adapter: TradingChartAdapter | null;
  drawings: Drawings;
  setDrawingTool: (tool: DrawingTool) => void;
  toggleDrawingsHidden: () => void;
};

/** The move one arrow key makes: one bar left or right, one pixel up or down, measured at the drawing's first anchor. */
export function nudgeTarget(adapter: TradingChartAdapter, anchor: DrawingPoint, direction: 'left' | 'right' | 'up' | 'down'): DrawingPoint | null {
  if (direction === 'left' || direction === 'right') {
    const time = adapter.drawingTimeAfterBars(anchor.time, direction === 'left' ? -1 : 1);
    return time ? { time, price: anchor.price } : null;
  }
  const projected = adapter.projectDrawingPoint(anchor);
  if (!projected) return null;
  const moved = adapter.drawingPointFromCoordinate(projected.x, projected.y + (direction === 'up' ? -1 : 1), { exactTime: true });
  return moved ? { time: anchor.time, price: moved.price } : null;
}

const TOOL_COMMANDS: readonly [TradingCommandId, DrawingTool][] = [
  ['drawing.trendLine', 'trend-line'],
  ['drawing.horizontalLine', 'horizontal-line'],
  ['drawing.verticalLine', 'vertical-line'],
  ['drawing.crossLine', 'crossline'],
  ['drawing.fibRetracement', 'fibonacci'],
  ['drawing.rectangle', 'rectangle'],
];

const NUDGES = [
  ['drawing.nudgeLeft', 'left'],
  ['drawing.nudgeRight', 'right'],
  ['drawing.nudgeUp', 'up'],
  ['drawing.nudgeDown', 'down'],
] as const;

/** The active chart's drawing commands (TVP-2.2): tool keys, copy and paste, nudge and hide-all. */
export function useTradingDrawingCommands({ active, adapter, drawings, drawingsHidden = false, drawingToolSettings, setDrawingTool, toggleDrawingsHidden }: DrawingCommandModel): void {
  const isActive = () => active;
  const movable = (): TradingDrawing | null => {
    if (drawingsHidden || drawingToolSettings?.lockAll) return null;
    const selection = drawings.selected().filter((drawing) => !drawing.locked);
    return selection.at(-1) ?? null;
  };

  // Hooks are called in a fixed order: one per catalogued command.
  useTradingCommand(TOOL_COMMANDS[0][0], () => setDrawingTool(TOOL_COMMANDS[0][1]), isActive);
  useTradingCommand(TOOL_COMMANDS[1][0], () => setDrawingTool(TOOL_COMMANDS[1][1]), isActive);
  useTradingCommand(TOOL_COMMANDS[2][0], () => setDrawingTool(TOOL_COMMANDS[2][1]), isActive);
  useTradingCommand(TOOL_COMMANDS[3][0], () => setDrawingTool(TOOL_COMMANDS[3][1]), isActive);
  useTradingCommand(TOOL_COMMANDS[4][0], () => setDrawingTool(TOOL_COMMANDS[4][1]), isActive);
  useTradingCommand(TOOL_COMMANDS[5][0], () => setDrawingTool(TOOL_COMMANDS[5][1]), isActive);

  useTradingCommand('drawing.copy', () => copyDrawingsToClipboard(drawings.selected()), () => active && !drawingsHidden && drawings.selected().length > 0);
  useTradingCommand('drawing.paste', () => drawings.paste(drawingClipboard()), () => active && hasCopiedDrawings());
  useTradingCommand('drawing.hideAll', toggleDrawingsHidden, isActive);

  const nudge = (direction: (typeof NUDGES)[number][1]) => () => {
    const drawing = movable();
    const anchor = drawing?.points[0];
    if (!drawing || !anchor || !adapter) return;
    const target = nudgeTarget(adapter, anchor, direction);
    if (target) drawings.translate(drawing.drawingId, anchor, target);
  };
  const canNudge = () => active && adapter !== null && movable() !== null;
  useTradingCommand(NUDGES[0][0], nudge(NUDGES[0][1]), canNudge);
  useTradingCommand(NUDGES[1][0], nudge(NUDGES[1][1]), canNudge);
  useTradingCommand(NUDGES[2][0], nudge(NUDGES[2][1]), canNudge);
  useTradingCommand(NUDGES[3][0], nudge(NUDGES[3][1]), canNudge);
}
