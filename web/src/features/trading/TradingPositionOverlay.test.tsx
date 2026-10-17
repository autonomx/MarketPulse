import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';

const paperApi = vi.hoisted(() => ({ snapshot: vi.fn(), placeOrder: vi.fn() }));

vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));

import { TradingPositionOverlay } from './TradingPositionOverlay';
import { readPaperPositionProtection } from './paperPositionProtection';

const adapter = {
  onVisibleRange: vi.fn(() => () => undefined),
  onCrosshair: vi.fn(() => () => undefined),
  priceToCoordinate: (price: number) => 120 - price,
  priceFromCoordinate: (coordinate: number) => 120 - coordinate,
} as unknown as TradingChartAdapter;

describe('TradingPositionOverlay', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('ResizeObserver', class {
      constructor(private readonly callback: (entries: Array<{ contentRect: { width: number; height: number } }>) => void) {}
      observe() { this.callback([{ contentRect: { width: 500, height: 300 } }]); }
      disconnect() {}
    });
    paperApi.snapshot.mockResolvedValue({
      positions: [],
      open_orders: [{
        instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
        quantity: '5',
        reference_price: '74.57',
        limit_price: null,
        stop_price: null,
        side: 'buy',
      }],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('projects a working order before a filled position exists', async () => {
    render(<TradingPositionOverlay adapter={adapter} accountId="paper-1" instrumentId="crypto:BINANCE:spot:SOL-USDT" />);

    await waitFor(() => expect(screen.getByLabelText('crypto:BINANCE:spot:SOL-USDT paper position')).toBeInTheDocument());
    expect(screen.getByText('Working')).toBeInTheDocument();
    // The working order's own line (TradingOrderLinesOverlay) shows its price.
    expect(screen.queryByText('74.57')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drag to add Take profit' })).toBeInTheDocument();
  });

  it('previews and confirms a dragged take-profit level', async () => {
    render(<TradingPositionOverlay adapter={adapter} accountId="paper-1" instrumentId="crypto:BINANCE:spot:SOL-USDT" />);

    const overlay = await screen.findByLabelText('crypto:BINANCE:spot:SOL-USDT paper position');
    vi.spyOn(overlay, 'getBoundingClientRect').mockReturnValue({ top: 0, left: 0, right: 500, bottom: 300, width: 500, height: 300, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
    const button = await screen.findByRole('button', { name: 'Drag to add Take profit' });
    const pointerEvent = (type: string, target: EventTarget, clientY: number) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperty(event, 'clientY', { value: clientY });
      target.dispatchEvent(event);
    };
    await act(async () => {
      pointerEvent('pointerdown', button, 20);
      pointerEvent('pointermove', window, 30);
      pointerEvent('pointerup', window, 30);
    });

    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
    expect(document.querySelector('.trading-position-zone')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(readPaperPositionProtection('paper-1', 'crypto:BINANCE:spot:SOL-USDT').takeProfit).toBe(90));
    expect(screen.getByRole('button', { name: 'Drag to add Take profit' })).toHaveTextContent('TP 90');
    expect(document.querySelector('.trading-position-zone')).not.toBeInTheDocument();
  });

  it('confirms and executes a full position close', async () => {
    paperApi.snapshot.mockResolvedValue({
      positions: [{
        instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
        quantity: '5',
        average_cost: '75.17',
        last_price: '75.17',
        realized_pnl: '0',
        unrealized_pnl: '0',
      }],
      open_orders: [],
    });
    paperApi.placeOrder.mockResolvedValue({ status: 'filled' });

    render(<TradingPositionOverlay adapter={adapter} accountId="paper-1" instrumentId="crypto:BINANCE:spot:SOL-USDT" />);
    await screen.findByRole('button', { name: 'Close paper position' });
    fireEvent.click(screen.getByRole('button', { name: 'Close paper position' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Close position');
    fireEvent.click(screen.getByRole('button', { name: 'Close position' }));

    await waitFor(() => expect(paperApi.placeOrder).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      side: 'sell',
      order_type: 'market',
      quantity: '5',
    })));
  });

  it('capability-gates reverse while paper short positions are unsupported', async () => {
    paperApi.snapshot.mockResolvedValue({
      positions: [{
        instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
        quantity: '5',
        average_cost: '75.17',
        last_price: '75.17',
        realized_pnl: '0',
        unrealized_pnl: '0',
      }],
      open_orders: [],
    });

    render(<TradingPositionOverlay adapter={adapter} accountId="paper-1" instrumentId="crypto:BINANCE:spot:SOL-USDT" />);
    const reverse = await screen.findByRole('button', { name: 'Reverse paper position' });
    expect(reverse).toBeDisabled();
    expect(reverse).toHaveAttribute('title', 'Reverse requires short-position support');
    fireEvent.click(reverse);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
  });
});