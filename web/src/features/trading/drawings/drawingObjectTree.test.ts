// Object tree commands (TVP-3.8): names, groups and the drawing order, each one undo step, kept in the saved document.
import { describe, expect, it } from 'vitest';
import {
  addDrawing,
  deleteDrawings,
  emptyDrawingState,
  groupDrawings,
  moveDrawingTo,
  nextDrawingGroupName,
  renameDrawing,
  renameDrawingGroup,
  reorderDrawing,
  setDrawingsHidden,
  undoDrawing,
  type DrawingState,
  type TradingDrawing,
} from './drawingCommands';
import { drawingDocumentPayload, upgradeDrawingDocument } from './drawingDocument';

const drawing = (drawingId: string): TradingDrawing => ({
  drawingId, instrumentId: 'equity:NASDAQ:AAPL', toolType: 'trend-line', selected: false, revision: 1,
  points: [{ time: '2026-10-01T00:00:00Z', price: 100 }, { time: '2026-10-02T00:00:00Z', price: 101 }],
});

function three(): DrawingState {
  return ['a', 'b', 'c'].reduce((state, id) => addDrawing(state, drawing(id)), emptyDrawingState());
}

const order = (state: DrawingState) => state.drawings.map((item) => item.drawingId);

describe('object tree commands (TVP-3.8)', () => {
  it('names a drawing, and an empty name goes back to the tool name', () => {
    const named = renameDrawing(three(), 'b', '  Breakout line  ');
    expect(named.drawings[1].name).toBe('Breakout line');
    expect(renameDrawing(named, 'b', '   ').drawings[1].name).toBeUndefined();
    expect(order(undoDrawing(named))).toEqual(['a', 'b', 'c']);
    expect(undoDrawing(named).drawings[1].name).toBeUndefined();
  });

  it('groups drawings by name, renames and merges groups, and ungroups', () => {
    let state = groupDrawings(three(), ['a', 'c'], nextDrawingGroupName(three().drawings));
    expect(state.drawings.map((item) => item.group)).toEqual(['Group 1', undefined, 'Group 1']);
    expect(nextDrawingGroupName(state.drawings)).toBe('Group 2');
    state = groupDrawings(state, ['b'], 'Levels');
    state = renameDrawingGroup(state, 'Group 1', 'Levels');
    expect(state.drawings.map((item) => item.group)).toEqual(['Levels', 'Levels', 'Levels']);
    expect(renameDrawingGroup(state, 'Levels', '  ')).toBe(state);
    expect(groupDrawings(state, ['a'], null).drawings[0].group).toBeUndefined();
  });

  it('changes the drawing order a step or to the front or back', () => {
    expect(order(reorderDrawing(three(), 'a', 'up'))).toEqual(['b', 'a', 'c']);
    expect(order(reorderDrawing(three(), 'c', 'down'))).toEqual(['a', 'c', 'b']);
    expect(order(reorderDrawing(three(), 'a', 'front'))).toEqual(['b', 'c', 'a']);
    expect(order(reorderDrawing(three(), 'c', 'back'))).toEqual(['c', 'a', 'b']);
    const top = three();
    expect(reorderDrawing(top, 'c', 'up')).toBe(top);
  });

  it("drops a drawing in another one's place, joining its group", () => {
    const grouped = groupDrawings(three(), ['c'], 'Top');
    const up = moveDrawingTo(grouped, 'a', 'c');
    expect(order(up)).toEqual(['b', 'c', 'a']);
    expect(up.drawings[2].group).toBe('Top');
    const down = moveDrawingTo(three(), 'c', 'a');
    expect(order(down)).toEqual(['c', 'a', 'b']);
  });

  it("hides and deletes a group's drawings at once", () => {
    const hidden = setDrawingsHidden(three(), ['a', 'b'], true);
    expect(hidden.drawings.map((item) => item.hidden)).toEqual([true, true, false]);
    const deleted = deleteDrawings(hidden, ['a', 'c']);
    expect(order(deleted)).toEqual(['b']);
    expect(order(undoDrawing(deleted))).toEqual(['a', 'b', 'c']);
  });

  it('keeps names, groups and the order in the saved document', () => {
    const state = reorderDrawing(groupDrawings(renameDrawing(three(), 'a', 'Support'), ['a', 'b'], 'Levels'), 'a', 'front');
    const loaded = upgradeDrawingDocument(drawingDocumentPayload('equity:NASDAQ:AAPL', state.drawings), 'equity:NASDAQ:AAPL');
    expect(loaded.drawings.map((item) => [item.drawingId, item.name, item.group])).toEqual([
      ['b', undefined, 'Levels'], ['c', undefined, undefined], ['a', 'Support', 'Levels'],
    ]);
  });
});
