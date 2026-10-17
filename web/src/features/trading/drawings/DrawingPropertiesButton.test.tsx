import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TradingChartContextMenu } from '../TradingChartContextMenu';
import { addDrawing, emptyDrawingState, undoDrawing, updateSelectedDrawing, type TradingDrawing } from './drawingCommands';
import { onDrawingActionRequest } from './drawingActions';
import { DrawingPropertiesButton } from './DrawingPropertiesButton';

afterEach(cleanup);

const fibonacci: TradingDrawing = {
  drawingId: 'fib',
  instrumentId: 'fixture',
  toolType: 'fibonacci',
  points: [{ time: '2026-10-07T00:00:00.000Z', price: 1 }, { time: '2026-10-07T01:00:00.000Z', price: 2 }],
  selected: true,
  revision: 1,
};

function openSettings(drawing: TradingDrawing) {
  const onChange = vi.fn();
  render(<DrawingPropertiesButton drawing={drawing} onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  return onChange;
}

describe('generic drawing properties dialog', () => {
  it('edits booleans and list-of-level records from the tool schema', () => {
    const onChange = openSettings(fibonacci);
    expect(screen.getByRole('dialog', { name: 'Fib retracement settings' })).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Labels'));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ showLabels: false }), expect.any(String));

    // Number inputs commit on blur, not per keystroke.
    const level = screen.getByLabelText('Levels 2 Level');
    fireEvent.change(level, { target: { value: '0.2' } });
    fireEvent.change(level, { target: { value: '0.25' } });
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.blur(level);
    expect(onChange).toHaveBeenCalledTimes(2);
    const levels = onChange.mock.lastCall![0].levels;
    expect(levels[1]).toEqual({ value: 0.25, color: '', visible: true });
    expect(levels).toHaveLength(7);

    fireEvent.click(screen.getByLabelText('Levels 7 Visible'));
    expect(onChange.mock.lastCall![0].levels[6]).toMatchObject({ value: 1, visible: false });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(onChange.mock.lastCall![0].levels).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Levels 1' }));
    expect(onChange.mock.lastCall![0].levels[0]).toMatchObject({ value: 0.236 });
  });

  it('focuses the dialog and closes on Escape', () => {
    openSettings({ ...fibonacci, toolType: 'trend-line' });
    const dialog = screen.getByRole('dialog');
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows stored values of an unexpected shape as such and never overwrites them', () => {
    const onChange = openSettings({ ...fibonacci, properties: { levels: [0, 0.5, 1] } });
    expect(screen.getByText("Stored value can't be edited here")).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull();
    fireEvent.click(screen.getByLabelText('Labels'));
    expect(onChange.mock.lastCall![0].levels).toEqual([0, 0.5, 1]);
  });

  it('renders nothing for a tool without properties', () => {
    const { container } = render(<DrawingPropertiesButton drawing={{ ...fibonacci, toolType: 'text' }} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('makes one undo step per field per dialog session', () => {
    let state = addDrawing(emptyDrawingState(), fibonacci);
    for (const showLabels of [false, true, false]) state = updateSelectedDrawing(state, { properties: { showLabels } }, 'session-1:showLabels');
    state = updateSelectedDrawing(state, { properties: { extendLeft: true } }, 'session-1:extendLeft');
    expect(state.history).toHaveLength(3);
    state = undoDrawing(state);
    expect(state.drawings[0].properties).toMatchObject({ showLabels: false, extendLeft: false });
    state = undoDrawing(state);
    expect(state.drawings[0].properties).toMatchObject({ showLabels: true });
  });
});

describe('drawing context-menu actions', () => {
  function renderMenu(drawingActions: { id: string; label: string; request: { type: string; payload: unknown } }[]) {
    const noop = () => undefined;
    render(
      <TradingChartContextMenu
        point={{ x: 0, y: 0, time: '2026-10-07T00:00:00.000Z', price: 1, source: 'context-menu', drawingId: 'position', drawingActions }}
        chartId="chart-2"
        symbol="TEST"
        indicatorContext={false}
        drawingCount={1}
        indicatorCount={0}
        cursorLocked={false}
        tableVisible={false}
        onClose={noop}
        onReset={noop}
        onCopyPrice={noop}
        onPastePrice={noop}
        onAddAlert={null}
        onToggleCursor={noop}
        onToggleTable={noop}
        onObjectTree={noop}
        onApplyTemplate={noop}
        onRemoveDrawings={noop}
        onRemoveIndicators={noop}
        onSettings={noop}
      />,
    );
  }

  it("hands a tool action's request to the feature that handles its type", () => {
    const handler = vi.fn();
    const stop = onDrawingActionRequest('order-ticket', handler);
    renderMenu([{ id: 'ticket', label: 'Open order ticket', request: { type: 'order-ticket', payload: { entry: 100, stop: 95 } } }]);
    fireEvent.click(screen.getByRole('menuitem', { name: /Open order ticket/ }));
    expect(handler).toHaveBeenCalledWith({ entry: 100, stop: 95 }, { chartId: 'chart-2', drawingId: 'position' });
    stop();
  });

  it('disables an action nobody handles', () => {
    renderMenu([{ id: 'ticket', label: 'Open order ticket', request: { type: 'unhandled', payload: null } }]);
    expect(screen.getByRole('menuitem', { name: /Open order ticket/ })).toBeDisabled();
  });
});
