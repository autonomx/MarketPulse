import { afterEach, describe, expect, it } from 'vitest';
import { clearDrawingClipboard, copyDrawingsToClipboard, drawingClipboard, hasCopiedDrawings } from './drawingClipboard';
import {
  addDrawing,
  cloneDrawings,
  deleteSelectedDrawing,
  emptyDrawingState,
  pasteDrawings,
  selectDrawing,
  selectedDrawingIds,
  toggleDrawingSelection,
  translateDrawing,
  redoDrawing,
  undoDrawing,
  type DrawingState,
  type TradingDrawing,
} from './drawingCommands';
import { constrainToSquare } from './tools/shapes';

const at = (minute: number, price: number) => ({ time: new Date(Date.UTC(2026, 9, 8, 14, minute)).toISOString(), price });

function line(id: string, minute: number, locked = false): TradingDrawing {
  return { drawingId: id, instrumentId: 'crypto:BTC', toolType: 'trend-line', points: [at(minute, 100), at(minute + 5, 110)], selected: false, revision: 1, locked };
}

function stateWith(...drawings: TradingDrawing[]): DrawingState {
  return drawings.reduce((state, drawing) => addDrawing(state, drawing), emptyDrawingState());
}

let counter = 0;
const nextId = () => `copy-${counter += 1}`;

afterEach(() => {
  counter = 0;
  clearDrawingClipboard();
});

describe('multi-select (TVP-2.2)', () => {
  it('Ctrl+click adds and removes drawings; the last added is the primary one', () => {
    let state = selectDrawing(stateWith(line('a', 0), line('b', 10), line('c', 20)), 'a');
    state = toggleDrawingSelection(state, 'c');
    expect(selectedDrawingIds(state)).toEqual(['a', 'c']);
    expect(state.selectedId).toBe('c');
    expect(state.drawings.filter((drawing) => drawing.selected).map((drawing) => drawing.drawingId)).toEqual(['a', 'c']);
    state = toggleDrawingSelection(state, 'c');
    expect(selectedDrawingIds(state)).toEqual(['a']);
    expect(state.selectedId).toBe('a');
    expect(selectDrawing(state, null).selectedIds).toEqual([]);
  });

  it('moves the whole selection as one undo step, leaving locked drawings', () => {
    let state = selectDrawing(stateWith(line('a', 0), line('b', 10), line('locked', 20, true)), 'a');
    state = toggleDrawingSelection(toggleDrawingSelection(state, 'b'), 'locked');
    const moved = translateDrawing(state, 'b', at(10, 100), at(12, 105));
    expect(moved.drawings.find((drawing) => drawing.drawingId === 'a')!.points[0]).toEqual(at(2, 105));
    expect(moved.drawings.find((drawing) => drawing.drawingId === 'b')!.points[0]).toEqual(at(12, 105));
    expect(moved.drawings.find((drawing) => drawing.drawingId === 'locked')!.points[0]).toEqual(at(20, 100));
    expect(undoDrawing(moved).drawings.find((drawing) => drawing.drawingId === 'a')!.points[0]).toEqual(at(0, 100));
    // A drawing outside the selection moves alone.
    const alone = translateDrawing(selectDrawing(state, 'a'), 'b', at(10, 100), at(11, 100));
    expect(alone.drawings.find((drawing) => drawing.drawingId === 'a')!.points[0]).toEqual(at(0, 100));
  });

  it('deletes every selected drawing at once', () => {
    let state = selectDrawing(stateWith(line('a', 0), line('b', 10), line('c', 20)), 'a');
    state = toggleDrawingSelection(state, 'c');
    const deleted = deleteSelectedDrawing(state);
    expect(deleted.drawings.map((drawing) => drawing.drawingId)).toEqual(['b']);
    expect(deleted.selectedIds).toEqual([]);
    expect(undoDrawing(deleted).drawings).toHaveLength(3);
  });
});

describe('undo and selection (TVP-2.2 review)', () => {
  it('leaves no selected flags after undo or redo', () => {
    let state = toggleDrawingSelection(selectDrawing(stateWith(line('a', 0), line('b', 10)), 'a'), 'b');
    state = translateDrawing(state, 'a', at(0, 100), at(1, 100));
    const undone = undoDrawing(state);
    expect(undone.drawings.some((drawing) => drawing.selected)).toBe(false);
    expect(redoDrawing(undone).drawings.some((drawing) => drawing.selected)).toBe(false);
  });
});

describe('clone, copy and paste (TVP-2.2)', () => {
  it('Ctrl+drag adds moved copies, selected, and keeps the originals', () => {
    const state = toggleDrawingSelection(selectDrawing(stateWith(line('a', 0), line('b', 10, true)), 'a'), 'b');
    const cloned = cloneDrawings(state, ['a', 'b'], at(0, 100), at(30, 90), nextId);
    expect(cloned.drawings.map((drawing) => drawing.drawingId)).toEqual(['a', 'b', 'copy-1', 'copy-2']);
    expect(cloned.drawings[2].points[0]).toEqual(at(30, 90));
    // A copy of a locked drawing can be moved.
    expect(cloned.drawings[3]).toMatchObject({ locked: false, revision: 1 });
    expect(selectedDrawingIds(cloned)).toEqual(['copy-1', 'copy-2']);
    expect(undoDrawing(cloned).drawings).toHaveLength(2);
  });

  it('pastes copies onto another symbol at the same times and prices', () => {
    copyDrawingsToClipboard([line('a', 0)]);
    expect(hasCopiedDrawings()).toBe(true);
    const target = stateWith(line('x', 40));
    const pasted = pasteDrawings(target, drawingClipboard(), 'equity:NASDAQ:AAPL', nextId);
    expect(pasted.drawings[1]).toMatchObject({ drawingId: 'copy-1', instrumentId: 'equity:NASDAQ:AAPL', points: line('a', 0).points, selected: true });
    // Pasting twice gives two independent copies; the clipboard is unchanged.
    expect(pasteDrawings(pasted, drawingClipboard(), 'equity:NASDAQ:AAPL', nextId).drawings).toHaveLength(3);
    expect(drawingClipboard()[0].drawingId).toBe('a');
    // Copies of hidden or locked drawings come back visible and movable.
    copyDrawingsToClipboard([{ ...line('h', 0), hidden: true, locked: true }]);
    expect(pasteDrawings(target, drawingClipboard(), 'crypto:BTC', nextId).drawings[1]).toMatchObject({ hidden: false, locked: false });
  });
});

describe('Shift constrain (TVP-2.2)', () => {
  it('squares a box from the previous anchor in the drag direction', () => {
    const origin = { x: 100, y: 100 };
    expect(constrainToSquare({ x: 160, y: 120 }, [origin], { shift: true, alt: false, ctrl: false })).toEqual({ x: 160, y: 160 });
    expect(constrainToSquare({ x: 70, y: 20 }, [origin], { shift: true, alt: false, ctrl: false })).toEqual({ x: 20, y: 20 });
    expect(constrainToSquare({ x: 160, y: 120 }, [origin], { shift: false, alt: false, ctrl: false })).toEqual({ x: 160, y: 120 });
  });
});

describe('pointer modifiers (TVP-2.2 review)', () => {
  it('Ctrl means Cmd on macOS, where Ctrl+click is the context menu', async () => {
    const { modifiersOf } = await import('./useDrawingEditing');
    const event = { shiftKey: false, altKey: false, ctrlKey: true, metaKey: false };
    expect(modifiersOf(event, true).ctrl).toBe(false);
    expect(modifiersOf({ ...event, ctrlKey: false, metaKey: true }, true).ctrl).toBe(true);
    expect(modifiersOf(event, false).ctrl).toBe(true);
  });
});
