import { describe, expect, it } from 'vitest';
import {
  addDrawing,
  emptyDrawingState,
  moveDrawingPoint,
  snapDrawingPoint,
  updateSelectedDrawing,
  type TradingDrawing,
} from './drawingCommands';

const rectangle: TradingDrawing = {
  drawingId: 'rectangle-1',
  instrumentId: 'fixture',
  toolType: 'rectangle',
  points: [
    { time: '2026-08-05T00:00:14.000Z', price: 100.004 },
    { time: '2026-08-05T01:00:29.000Z', price: 110.006 },
  ],
  selected: true,
  revision: 1,
};

describe('advanced drawing commands', () => {
  it('snaps authority coordinates without storing pixels', () => {
    expect(snapDrawingPoint(rectangle.points[0], 'ohlc')).toEqual({
      time: '2026-08-05T00:00:00.000Z',
      price: 100,
    });
  });

  it('locks movement and keeps style, visibility, and text in history', () => {
    let state = addDrawing(emptyDrawingState(), rectangle);
    state = updateSelectedDrawing(state, {
      locked: true,
      hidden: true,
      text: 'Range',
      style: { color: '#ffffff', lineWidth: 3, lineStyle: 'dashed' },
    });
    const locked = state.drawings[0];
    state = moveDrawingPoint(state, locked.drawingId, 0, { time: locked.points[0].time, price: 999 });
    expect(state.drawings[0].points[0].price).toBe(100.004);
    expect(state.drawings[0].hidden).toBe(true);
    expect(state.drawings[0].style?.lineStyle).toBe('dashed');
    expect(JSON.stringify(state.drawings[0])).not.toContain('pixel');
  });
});
