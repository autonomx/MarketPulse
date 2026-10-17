import { describe, expect, it } from 'vitest';
import {
  addDrawing,
  deleteDrawing,
  deleteSelectedDrawing,
  emptyDrawingState,
  moveDrawingPoint,
  redoDrawing,
  selectDrawing,
  translateDrawing,
  undoDrawing,
  type TradingDrawing,
} from './drawingCommands';

const drawing: TradingDrawing = {
  drawingId: 'trend-1',
  instrumentId: 'crypto:BINANCE:spot:BTC-USDT',
  toolType: 'trend-line',
  points: [
    { time: '2026-08-05T00:00:00Z', price: 100 },
    { time: '2026-08-05T01:00:00Z', price: 110 },
  ],
  selected: true,
  revision: 1,
};

describe('Trading drawing command engine', () => {
  it('creates, selects, moves, deletes, undoes, and redoes drawings', () => {
    let state = addDrawing(emptyDrawingState(), drawing);
    expect(state.drawings).toHaveLength(1);
    state = moveDrawingPoint(state, drawing.drawingId, 1, { time: '2026-08-05T02:00:00Z', price: 120 });
    expect(state.drawings[0].points[1].price).toBe(120);
    expect(state.drawings[0].revision).toBe(2);
    state = selectDrawing(state, drawing.drawingId);
    state = deleteSelectedDrawing(state);
    expect(state.drawings).toEqual([]);
    state = undoDrawing(state);
    expect(state.drawings).toHaveLength(1);
    state = redoDrawing(state);
    expect(state.drawings).toEqual([]);
  });

  it('translates an entire unlocked drawing in time and price', () => {
    let state = addDrawing(emptyDrawingState(), drawing);
    state = translateDrawing(
      state,
      drawing.drawingId,
      { time: '2026-08-05T00:00:00Z', price: 100 },
      { time: '2026-08-05T02:00:00Z', price: 125 },
    );
    expect(state.drawings[0].points).toEqual([
      { time: '2026-08-05T02:00:00.000Z', price: 125 },
      { time: '2026-08-05T03:00:00.000Z', price: 135 },
    ]);
    expect(state.drawings[0].revision).toBe(2);
    expect(state.history).toHaveLength(2);
  });

  it('does not translate a locked drawing', () => {
    const locked = { ...drawing, locked: true };
    const state = addDrawing(emptyDrawingState(), locked);
    expect(translateDrawing(
      state,
      locked.drawingId,
      locked.points[0],
      { time: '2026-08-05T02:00:00Z', price: 125 },
    )).toBe(state);
  });

  it('keeps authority in time and price coordinates', () => {
    const state = addDrawing(emptyDrawingState(), drawing);
    const text = JSON.stringify(state.drawings[0]);
    expect(text).toContain('2026-08-05T00:00:00Z');
    expect(text).not.toContain('pixel');
    expect(text).not.toContain('clientX');
  });

  it('erases a drawing by id without changing another selection', () => {
    const second = { ...drawing, drawingId: 'trend-2' };
    let state = addDrawing(emptyDrawingState(), drawing);
    state = addDrawing(state, second);
    state = selectDrawing(state, second.drawingId);
    state = deleteDrawing(state, drawing.drawingId);
    expect(state.drawings.map((item) => item.drawingId)).toEqual([second.drawingId]);
    expect(state.selectedId).toBe(second.drawingId);
  });
});
