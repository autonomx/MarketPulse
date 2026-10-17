import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter, TradingCrosshairPoint } from './chart/chartAdapter';
import { takePaperTicketPrefill } from './paperTicketRequests';
import { priceScaleMenuItems, TradingPriceScalePlus } from './TradingPriceScalePlus';

afterEach(cleanup);

function fakeAdapter() {
  let listener: ((point: TradingCrosshairPoint | null) => void) | null = null;
  const adapter = {
    onCrosshair: (next: (point: TradingCrosshairPoint | null) => void) => { listener = next; return () => { listener = null; }; },
    priceToCoordinate: (price: number) => 200 - price,
    mainPriceScaleBox: () => ({ side: 'right' as const, scaleWidth: 60, paneHeight: 300 }),
  } as unknown as TradingChartAdapter;
  return { adapter, move: (point: TradingCrosshairPoint | null) => act(() => listener?.(point)) };
}

describe('price-scale "+" menu', () => {
  it('offers limit orders toward the market and stops away from it', () => {
    expect(priceScaleMenuItems(95, 100, '95').map((item) => item.label)).toEqual(['Buy limit at 95', 'Sell stop at 95', 'Add alert at 95']);
    expect(priceScaleMenuItems(105, 100, '105').map((item) => item.label)).toEqual(['Sell limit at 105', 'Buy stop at 105', 'Add alert at 105']);
  });

  it('follows the pointer price and fills the ticket with the chosen order', () => {
    const { adapter, move } = fakeAdapter();
    render(<TradingPriceScalePlus adapter={adapter} instrumentId="crypto:BTC" lastPrice={100} tickSize={0.5} onAddAlert={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Trade or add an alert/ })).toBeNull();
    // A synchronized or other-pane crosshair carries no pointer price.
    move({ time: 1 as never, price: 95 });
    expect(screen.queryByRole('button', { name: /Trade or add an alert/ })).toBeNull();
    move({ time: 1 as never, price: 95.2, pointer: true });
    fireEvent.click(screen.getByRole('button', { name: 'Trade or add an alert at 95' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Buy limit at 95' }));
    expect(takePaperTicketPrefill()).toMatchObject({ instrumentId: 'crypto:BTC', side: 'buy', orderType: 'limit', entry: 95, source: 'chart' });
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('opens the alert dialog at the price', () => {
    const { adapter, move } = fakeAdapter();
    const onAddAlert = vi.fn();
    render(<TradingPriceScalePlus adapter={adapter} instrumentId="crypto:BTC" lastPrice={100} tickSize={null} onAddAlert={onAddAlert} />);
    move({ time: 1 as never, price: 110, pointer: true });
    fireEvent.click(screen.getByRole('button', { name: 'Trade or add an alert at 110' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add alert at 110' }));
    expect(onAddAlert).toHaveBeenCalledWith(110, 90);
  });
});
