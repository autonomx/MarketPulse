import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TradingChartTypeMenu } from './TradingChartTypeMenu';

describe('TradingChartTypeMenu', () => {
  beforeEach(() => window.localStorage.clear());

  it('renders grouped chart types with the active option and favorites', () => {
    render(<TradingChartTypeMenu value="candlestick" onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chart type' }));

    expect(screen.getByRole('listbox', { name: 'TradingView chart types' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Candles' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('option', { name: 'Heikin Ashi' })).toBeInTheDocument();
  });

  it('persists a selected chart type favorite', () => {
    render(<TradingChartTypeMenu value="candlestick" onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chart type' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add Heikin Ashi to chart favorites' }));

    expect(JSON.parse(window.localStorage.getItem('omnix.trading.chart-type-favorites') ?? '[]')).toEqual(['heikin-ashi']);
  });
});
