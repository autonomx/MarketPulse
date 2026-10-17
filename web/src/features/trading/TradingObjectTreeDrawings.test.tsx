import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingDrawing } from './drawings/drawingCommands';
import { drawingDisplayName } from './drawings/tools/registry';
import { TradingObjectTreeDrawings } from './TradingObjectTreeDrawings';

const TOOL = drawingDisplayName({ toolType: 'trend-line' });

afterEach(cleanup);

const drawing = (drawingId: string, extra: Partial<TradingDrawing> = {}): TradingDrawing => ({
  drawingId, instrumentId: 'equity:NASDAQ:AAPL', toolType: 'trend-line', selected: false, revision: 1,
  points: [{ time: '2026-10-01T00:00:00Z', price: 100 }, { time: '2026-10-02T00:00:00Z', price: 101 }], ...extra,
});

function tree(drawings: TradingDrawing[], selectedIds: string[] = []) {
  const api = {
    state: { drawings, selectedId: selectedIds.at(-1) ?? null, selectedIds, history: [], future: [] },
    select: vi.fn(), toggleSelect: vi.fn(), rename: vi.fn(), setGroup: vi.fn(), renameGroup: vi.fn(), reorder: vi.fn(),
    moveTo: vi.fn(), setHidden: vi.fn(), removeMany: vi.fn(), remove: vi.fn(), updateSelected: vi.fn(),
  };
  render(<TradingObjectTreeDrawings drawings={api as never} eyeIcon={(hidden) => (hidden ? 'show' : 'hide')} trashIcon="x" drawingIcon={() => null} />);
  return api;
}

describe('object tree drawings (TVP-3.8)', () => {
  it('lists the top-most drawing first, under its group, with its own name', () => {
    tree([drawing('a', { name: 'Support' }), drawing('b', { group: 'Levels' }), drawing('c', { group: 'Levels', name: 'Resistance' })]);
    const rows = screen.getAllByRole('listitem');
    expect(rows[0].textContent).toContain('Levels');
    expect(within(rows[0]).getAllByRole('button', { name: /^Rename (?!group)/ }).map((button) => button.getAttribute('aria-label'))).toEqual(['Rename Resistance', `Rename ${TOOL}`]);
    expect(rows.at(-1)?.textContent).toContain('Support');
  });

  it('renames a drawing from the pencil or a double-click', () => {
    const api = tree([drawing('a')]);
    fireEvent.click(screen.getByRole('button', { name: `Rename ${TOOL}` }));
    const input = screen.getByLabelText(`Name of ${TOOL}`);
    fireEvent.change(input, { target: { value: 'Breakout' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(api.rename).toHaveBeenCalledWith('a', 'Breakout');
  });

  it('groups the selected drawings and acts on a whole group', () => {
    const api = tree([drawing('a'), drawing('b', { group: 'Group 1' })], ['a']);
    fireEvent.click(screen.getByRole('button', { name: 'Group selected (1)' }));
    expect(api.setGroup).toHaveBeenCalledWith(['a'], 'Group 2');
    fireEvent.click(screen.getByRole('button', { name: 'Hide group Group 1' }));
    expect(api.setHidden).toHaveBeenCalledWith(['b'], true);
    fireEvent.click(screen.getByRole('button', { name: 'Ungroup Group 1' }));
    expect(api.setGroup).toHaveBeenLastCalledWith(['b'], null);
    fireEvent.click(screen.getByRole('button', { name: 'Delete group Group 1' }));
    expect(api.removeMany).toHaveBeenCalledWith(['b']);
  });

  it('moves a drawing forward or backward, and Ctrl+click adds to the selection', () => {
    const api = tree([drawing('a'), drawing('b')]);
    const [top, bottom] = screen.getAllByRole('listitem');
    expect(within(top).getByRole('button', { name: /Bring .* forward/ })).toBeDisabled();
    fireEvent.click(within(bottom).getByRole('button', { name: /Bring .* forward/ }));
    expect(api.reorder).toHaveBeenCalledWith('a', 'up');
    fireEvent.click(within(top).getByRole('button', { name: /Send .* backward/ }));
    expect(api.reorder).toHaveBeenCalledWith('b', 'down');
    fireEvent.click(within(top).getAllByRole('button')[0], { ctrlKey: true });
    expect(api.toggleSelect).toHaveBeenCalledWith('b');
  });
});
