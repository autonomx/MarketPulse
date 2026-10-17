import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingDrawing } from './drawingCommands';
import { DrawingStyleControls } from './DrawingStyleControls';

afterEach(cleanup);

const line: TradingDrawing = {
  drawingId: 'line',
  instrumentId: 'fixture',
  toolType: 'trend-line',
  points: [{ time: '2026-10-07T00:00:00.000Z', price: 1 }, { time: '2026-10-07T01:00:00.000Z', price: 2 }],
  selected: true,
  revision: 1,
  style: { color: '#2962ff', lineWidth: 2, lineStyle: 'solid' },
};

describe('DrawingStyleControls', () => {
  it('changes the width and the dash of the selected drawing, keeping its colour', () => {
    const onChange = vi.fn();
    render(<DrawingStyleControls drawing={line} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Drawing line width'), { target: { value: '4' } });
    expect(onChange).toHaveBeenLastCalledWith({ color: '#2962ff', lineWidth: 4, lineStyle: 'solid' });
    fireEvent.change(screen.getByLabelText('Drawing line style'), { target: { value: 'dashed' } });
    expect(onChange).toHaveBeenLastCalledWith({ color: '#2962ff', lineWidth: 2, lineStyle: 'dashed' });
  });

  it('keeps an unusual width selectable instead of showing the wrong one', () => {
    render(<DrawingStyleControls drawing={{ ...line, style: { ...line.style!, lineWidth: 6 } }} onChange={vi.fn()} />);
    expect((screen.getByLabelText('Drawing line width') as HTMLSelectElement).value).toBe('6');
  });
});
