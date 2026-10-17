import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TradingIndicatorManager } from './TradingIndicatorManager';
import { useTradingCommandDispatcher } from './commands/useTradingCommands';

describe('TradingIndicatorManager filters', () => {
  it('filters the catalog by market, category, and availability', () => {
    render(<TradingIndicatorManager indicators={[]} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Indicators' }));

    const marketFilter = screen.getByLabelText('Market filter');
    const categoryFilter = screen.getByLabelText('Category filter');
    const availabilityFilter = screen.getByLabelText('Availability filter');

    fireEvent.change(marketFilter, { target: { value: 'crypto' } });
    expect(screen.getByText('Active addresses with contracts')).toBeTruthy();
    expect(screen.getByText('Relative Strength Index (RSI)')).toBeTruthy();
    expect(screen.queryByText('Analyst price forecast')).toBeNull();

    fireEvent.change(categoryFilter, { target: { value: 'on-chain' } });
    expect(screen.getByText('Active addresses with contracts')).toBeTruthy();
    expect(screen.getByText('Hash Rate')).toBeTruthy();
    expect(screen.queryByText('Relative Strength Index (RSI)')).toBeNull();

    fireEvent.change(categoryFilter, { target: { value: 'all' } });
    fireEvent.change(marketFilter, { target: { value: 'stocks' } });
    expect(screen.getByText('Analyst price forecast')).toBeTruthy();
    expect(screen.queryByText('Active addresses with contracts')).toBeNull();

    fireEvent.change(availabilityFilter, { target: { value: 'ready' } });
    expect(screen.getByText('Analyst price forecast')).toBeTruthy();
    expect(screen.getByText('Dividend Yield')).toBeTruthy();
    // Market breadth is computed by Omnix (TVP-6.6).
    expect(screen.getByText('Advance/Decline Line')).toBeTruthy();

    // Every stock-specific series has its data now.
    fireEvent.change(availabilityFilter, { target: { value: 'data-required' } });
    expect(screen.queryByText('Analyst price forecast')).toBeNull();
    expect(screen.queryByText('Advance/Decline Line')).toBeNull();
    expect(screen.queryByText('Relative Strength Index (RSI)')).toBeNull();
  });

  it('makes the existing Fundamentals sidebar section useful', () => {
    render(<TradingIndicatorManager indicators={[]} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Indicators' }));
    fireEvent.click(screen.getByRole('button', { name: 'Fundamentals' }));

    expect(screen.getByText('Analyst price forecast')).toBeTruthy();
    expect(screen.getByText('Price target - indicator')).toBeTruthy();
    expect(screen.queryByText('Relative Strength Index (RSI)')).toBeNull();
  });
});

function WithShortcuts() {
  useTradingCommandDispatcher();
  return <TradingIndicatorManager indicators={[]} onToggle={vi.fn()} />;
}

describe('TradingIndicatorManager shortcut (TVP-2.1)', () => {
  it('opens the picker with /, but not while typing in a field', () => {
    render(<WithShortcuts />);
    const field = document.createElement('input');
    document.body.append(field);
    act(() => { fireEvent.keyDown(field, { key: '/' }); });
    expect(screen.queryByRole('dialog', { name: 'Indicators, metrics, and strategies' })).toBeNull();
    field.remove();
    act(() => { fireEvent.keyDown(document.body, { key: '/' }); });
    expect(screen.getByRole('dialog', { name: 'Indicators, metrics, and strategies' })).toBeTruthy();
  });
});
