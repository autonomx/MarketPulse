import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { clearPaperNotifications, orderNotifications, usePaperNotifications, usePaperOrderNotifications } from './paperNotifications';
import type { PaperAccountSnapshot, PaperOrder } from './paperTypes';
import { TradingNotificationsLog } from './TradingNotificationsLog';
import { TradingOrderToastLayer } from './TradingOrderToastLayer';

const order = (id: string, status: PaperOrder['status'], extra: Partial<PaperOrder> = {}) => ({
  order_id: id, instrument_id: 'crypto:BINANCE:spot:BTC-USDT', side: 'buy', order_type: 'market', quantity: '2', status, updated_at: '2026-10-08T14:00:00Z', ...extra,
}) as PaperOrder;

const snapshot = (accountId: string, orders: PaperOrder[]) => ({
  account: { account_id: accountId }, balances: [], positions: [], open_orders: orders.filter((item) => item.status === 'open'), order_history: orders,
}) as unknown as PaperAccountSnapshot;

afterEach(() => act(() => clearPaperNotifications()));

describe('paper order notifications (TVP-7.4)', () => {
  it('reports fills, rejections, cancellations and expiries between snapshots', () => {
    const before = snapshot('a', [order('1', 'open'), order('2', 'open'), order('3', 'filled')]);
    const after = snapshot('a', [
      order('1', 'filled', { average_fill_price: '101.5' }),
      order('2', 'rejected', { rejection_reason: 'insufficient_paper_cash' }),
      order('3', 'filled'),
      order('4', 'expired', { side: 'sell', order_type: 'limit' }),
      order('5', 'open'),
    ]);
    expect(orderNotifications(before, after).map((item) => [item.kind, item.message])).toEqual([
      ['fill', 'Buy 2 BTC/USDT MARKET filled at 101.5'],
      ['reject', 'Buy 2 BTC/USDT MARKET rejected: insufficient paper cash'],
      ['expire', 'Sell 2 BTC/USDT LIMIT expired'],
    ]);
  });

  it('reports an order moved on the chart as modified, not cancelled (TVP-7.3)', () => {
    const before = snapshot('a', [order('entry', 'open', { order_type: 'limit', limit_price: '10' })]);
    const after = snapshot('a', [
      order('entry', 'cancelled', { order_type: 'limit', limit_price: '10' }),
      order('entry-moved-k1', 'open', { order_type: 'limit', limit_price: '9.5' }),
    ]);
    expect(orderNotifications(before, after).map((item) => [item.kind, item.message])).toEqual([
      ['modify', 'Buy 2 BTC/USDT LIMIT moved to 9.5'],
    ]);
  });

  it('reports partial fills and open orders that left the snapshot', () => {
    const before = snapshot('a', [order('1', 'open', { filled_quantity: '0' }), order('old', 'open')]);
    const after = snapshot('a', [order('1', 'open', { filled_quantity: '1' })]);
    expect(orderNotifications(before, after, '2026-10-08T15:00:00Z').map((item) => [item.kind, item.message])).toEqual([
      ['partial', 'Buy 2 BTC/USDT MARKET partly filled: 1 of 2'],
      ['closed', 'Buy 2 BTC/USDT MARKET is no longer open'],
    ]);
  });

  it('names orders placed by a margin call (TVP-7.2b)', () => {
    const id = 'paper-margin-call-acct-1';
    const before = snapshot('a', [order(id, 'open', { side: 'sell', filled_quantity: '0' })]);
    const partly = snapshot('a', [order(id, 'open', { side: 'sell', filled_quantity: '1' })]);
    const filled = snapshot('a', [order(id, 'filled', { side: 'sell', average_fill_price: '90' })]);
    expect(orderNotifications(before, partly).map((item) => item.message)).toEqual(['Margin call: Sell 2 BTC/USDT MARKET partly filled: 1 of 2']);
    expect(orderNotifications(partly, filled).map((item) => item.message)).toEqual(['Margin call: Sell 2 BTC/USDT MARKET filled at 90']);
  });

  it('switching between live and replay starts a new baseline', () => {
    const live = snapshot('a', [order('h1', 'filled')]);
    const { rerender } = renderHook(({ value, mode }) => usePaperOrderNotifications(value, mode), { initialProps: { value: live, mode: 'live' } });
    rerender({ value: snapshot('a', []), mode: 'replay:1' });
    rerender({ value: live, mode: 'live' });
    expect(renderHook(() => usePaperNotifications()).result.current).toEqual([]);
  });

  it('logs the newest first and never twice', () => {
    const before = snapshot('a', [order('old', 'open'), order('new', 'open')]);
    const after = snapshot('a', [order('new', 'filled', { updated_at: '2026-10-08T14:05:00Z' }), order('old', 'filled', { updated_at: '2026-10-08T14:01:00Z' })]);
    const { rerender } = renderHook(({ value }) => usePaperOrderNotifications(value), { initialProps: { value: before } });
    rerender({ value: after });
    rerender({ value: before });
    rerender({ value: after });
    expect(renderHook(() => usePaperNotifications()).result.current.map((item) => item.orderId)).toEqual(['new', 'old']);
  });

  it('starts from the first snapshot of an account, logs, toasts and clears', () => {
    const { rerender } = renderHook(({ value }) => usePaperOrderNotifications(value), { initialProps: { value: snapshot('a', [order('1', 'filled')]) } });
    const log = renderHook(() => usePaperNotifications());
    expect(log.result.current).toEqual([]);
    rerender({ value: snapshot('b', [order('9', 'filled')]) });
    expect(log.result.current).toEqual([]);
    rerender({ value: snapshot('b', [order('9', 'filled'), order('10', 'cancelled')]) });
    expect(log.result.current.map((item) => item.kind)).toEqual(['cancel']);
    render(<><TradingOrderToastLayer /><TradingNotificationsLog /></>);
    expect(screen.getByRole('status')).toHaveTextContent('Order cancelled');
    expect(screen.getByRole('table')).toHaveTextContent('Buy 2 BTC/USDT MARKET cancelled');
    fireEvent.click(screen.getByRole('button', { name: 'Clear notifications' }));
    expect(screen.getByText('No notifications yet')).toBeInTheDocument();
  });
});
