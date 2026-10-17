import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { fuzzyFilter, fuzzyScore } from './fuzzyMatch';
import { TradingCommandPalette, type TradingPaletteItem } from './TradingCommandPalette';

function items(): Array<TradingPaletteItem & { run: ReturnType<typeof vi.fn<() => void>> }> {
  return [
    { id: 'reset', label: 'Reset chart view', group: 'Chart', keys: ['Alt+R'], run: vi.fn<() => void>() },
    { id: 'log', label: 'Logarithmic price scale', group: 'Chart', keys: ['Alt+L'], run: vi.fn<() => void>() },
    { id: 'date', label: 'Go to date', group: 'Chart', disabled: true, run: vi.fn<() => void>() },
    { id: 'btc', label: 'BTC/USDT · BINANCE', group: 'Symbol', run: vi.fn<() => void>() },
  ];
}

describe('fuzzy search', () => {
  it('matches characters in order and ranks direct and word-start matches first', () => {
    expect(fuzzyScore('lps', 'Logarithmic price scale')).not.toBeNull();
    expect(fuzzyScore('xyz', 'Logarithmic price scale')).toBeNull();
    const ranked = fuzzyFilter(['Chart snapshot', 'Reset chart view', 'Search'], 'chart', (text) => text);
    expect(ranked).toEqual(['Chart snapshot', 'Reset chart view']);
  });
});

describe('TradingCommandPalette', () => {
  it('filters by typing and runs the highlighted item with Enter', () => {
    const list = items();
    const onClose = vi.fn();
    render(<TradingCommandPalette open title="Command palette" placeholder="Search" items={list} onClose={onClose} />);
    const input = screen.getByRole('combobox', { name: 'Command palette' });
    expect(input).toHaveFocus();
    expect(screen.getAllByRole('option')).toHaveLength(4);
    expect(screen.getByRole('option', { name: /Reset chart view.*Alt\+R/ })).toBeInTheDocument();

    fireEvent.change(input, { target: { value: 'log scale' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onClose).toHaveBeenCalled();
    expect(list[1].run).toHaveBeenCalledTimes(1);
  });

  it('moves with the arrow keys and does not run unavailable items', () => {
    const list = items();
    render(<TradingCommandPalette open title="Command palette" placeholder="Search" items={list} onClose={vi.fn()} />);
    const input = screen.getByRole('combobox', { name: 'Command palette' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('Go to date');
    expect(input).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { selected: true }).id);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(list[2].run).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('BTC/USDT');
  });

  it('closes with Escape and shows an empty state', () => {
    const onClose = vi.fn();
    render(<TradingCommandPalette open title="Command palette" placeholder="Search" items={items()} onClose={onClose} />);
    const input = screen.getByRole('combobox', { name: 'Command palette' });
    fireEvent.change(input, { target: { value: 'qqqq' } });
    expect(screen.getByText('No matching commands')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
