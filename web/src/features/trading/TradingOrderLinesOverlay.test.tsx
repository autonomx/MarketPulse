import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { order, pendingStop } from '../../test/paperOrders';

const paperApi = vi.hoisted(() => ({
  snapshot: vi.fn(), protections: vi.fn(), moveRiskEntry: vi.fn(), replaceOrder: vi.fn(), cancelOrder: vi.fn(),
}));
vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));

import { TradingOrderLinesOverlay } from './TradingOrderLinesOverlay';

const INSTRUMENT = 'equity:NYSE:TEST';
const adapter = {
  onVisibleRange: vi.fn(() => () => undefined),
  onViewportChange: vi.fn(() => () => undefined),
  onCrosshair: vi.fn(() => () => undefined),
  priceToCoordinate: (price: number) => 200 - price * 10,
  priceFromCoordinate: (y: number) => (200 - y) / 10,
} as unknown as TradingChartAdapter;

beforeEach(() => {
  paperApi.snapshot.mockResolvedValue({
    open_orders: [order(), order({ order_id: 'exit-1', side: 'sell', order_type: 'limit', limit_price: '12', quantity: '50' })],
  });
  paperApi.protections.mockResolvedValue([pendingStop]);
  paperApi.moveRiskEntry.mockResolvedValue({});
  paperApi.replaceOrder.mockResolvedValue({});
  paperApi.cancelOrder.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function mount() {
  render(<TradingOrderLinesOverlay adapter={adapter} accountId="paper-1" instrumentId={INSTRUMENT} tickSize={0.01} />);
  const root = await screen.findByRole('group', { name: `${INSTRUMENT} working orders` });
  vi.spyOn(root, 'getBoundingClientRect').mockReturnValue({ top: 0, left: 0, right: 500, bottom: 300, width: 500, height: 300, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
  return root;
}

function drag(handle: HTMLElement, toY: number) {
  fireEvent.pointerDown(handle, { button: 0, clientY: 100 });
  act(() => {
    window.dispatchEvent(new MouseEvent('pointermove', { clientY: toY }));
  });
  act(() => {
    window.dispatchEvent(new MouseEvent('pointerup', { clientY: toY }));
  });
}

describe('TradingOrderLinesOverlay', () => {
  it('draws each working order as a labelled line', async () => {
    await mount();
    expect(screen.getByRole('button', { name: 'BUY LMT 100 at 10: drag to move' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SELL LMT 50 at 12: drag to move' })).toBeInTheDocument();
  });

  it('moves a risk entry only after confirmation, through the server re-pricing', async () => {
    await mount();
    drag(screen.getByRole('button', { name: 'BUY LMT 100 at 10: drag to move' }), 105);
    expect(paperApi.moveRiskEntry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Move BUY LMT 100 to 9.5' }));
    await waitFor(() => expect(paperApi.moveRiskEntry).toHaveBeenCalledWith('paper-1', 'entry-1', expect.objectContaining({ trigger_price: '9.5' })));
    expect(paperApi.replaceOrder).not.toHaveBeenCalled();
  });

  it('replaces a moved exit, and keeps it where it was on Keep', async () => {
    await mount();
    drag(screen.getByRole('button', { name: 'SELL LMT 50 at 12: drag to move' }), 70);
    fireEvent.click(screen.getByRole('button', { name: 'Keep the order where it was' }));
    expect(screen.getByRole('button', { name: 'SELL LMT 50 at 12: drag to move' })).toBeInTheDocument();
    drag(screen.getByRole('button', { name: 'SELL LMT 50 at 12: drag to move' }), 70);
    fireEvent.click(screen.getByRole('button', { name: 'Move SELL LMT 50 to 13' }));
    await waitFor(() => expect(paperApi.replaceOrder).toHaveBeenCalledWith('paper-1', 'exit-1', expect.objectContaining({ limit_price: '13', quantity: '50', side: 'sell' })));
  });

  it('cancels from the line and shows a refused move', async () => {
    paperApi.moveRiskEntry.mockRejectedValue(new Error('(409): paper_order_not_open'));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel SELL LMT 50' }));
    await waitFor(() => expect(paperApi.cancelOrder).toHaveBeenCalledWith('paper-1', 'exit-1'));
    drag(screen.getByRole('button', { name: 'BUY LMT 100 at 10: drag to move' }), 105);
    fireEvent.click(screen.getByRole('button', { name: 'Move BUY LMT 100 to 9.5' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer working');
  });

  it('stops dragging when the line goes away mid-drag', async () => {
    await mount();
    const handle = screen.getByRole('button', { name: 'BUY LMT 100 at 10: drag to move' });
    fireEvent.pointerDown(handle, { button: 0, clientY: 100 });
    paperApi.snapshot.mockResolvedValue({ open_orders: [] });
    cleanup();
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientY: 105 }));
      window.dispatchEvent(new MouseEvent('pointerup', { clientY: 105 }));
    });
    expect(paperApi.moveRiskEntry).not.toHaveBeenCalled();
  });

  it('draws nothing in replay', () => {
    render(<TradingOrderLinesOverlay adapter={adapter} accountId="paper-1" instrumentId={INSTRUMENT} tickSize={0.01} disabled />);
    expect(paperApi.snapshot).not.toHaveBeenCalled();
    expect(screen.queryByRole('group')).toBeNull();
  });
});
