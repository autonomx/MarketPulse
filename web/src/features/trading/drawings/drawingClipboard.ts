// The drawing clipboard (TVP-2.2): Ctrl+C copies the selected drawings, Ctrl+V
// pastes them on the active chart, also on another chart or symbol. It lives
// in the page, like TradingView's, so a copy survives switching charts and tabs.
import { normalizeDrawing, type TradingDrawing } from './drawingCommands';

let copied: readonly TradingDrawing[] = [];
const listeners = new Set<() => void>();

export function copyDrawingsToClipboard(drawings: readonly TradingDrawing[]): void {
  copied = drawings.map((drawing) => ({ ...normalizeDrawing(drawing), selected: false }));
  for (const listener of listeners) listener();
}

/** The copied drawings (copies; pasting changes nothing here). */
export function drawingClipboard(): readonly TradingDrawing[] {
  return copied.map(normalizeDrawing);
}

export function hasCopiedDrawings(): boolean {
  return copied.length > 0;
}

export function onDrawingClipboardChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only: starts from an empty clipboard. */
export function clearDrawingClipboard(): void {
  copied = [];
}
