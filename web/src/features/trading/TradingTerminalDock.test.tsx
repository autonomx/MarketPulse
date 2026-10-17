import { render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const paperApi = vi.hoisted(() => ({
  accounts: vi.fn(),
  snapshot: vi.fn(),
  createAccount: vi.fn(),
  placeOrder: vi.fn(),
}));

vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));
vi.mock('./TradingPaperDashboard', () => ({ TradingPaperDashboard: () => <div>Paper trading dashboard view</div> }));

import { TradingTerminalDock } from './TradingTerminalDock';

/** The services every TradingTerminalDock test starts from. */
function mockDockServices(): void {
  beforeEach(() => {
    paperApi.accounts.mockResolvedValue([]);
    paperApi.snapshot.mockResolvedValue(null);
  });

  afterEach(() => vi.clearAllMocks());
}

describe('TradingTerminalDock', () => {
  mockDockServices();

  it('minimizes the dock while keeping the restore control visible', async () => {
    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:BTC-USDT" bindingId={null} />);
    expect(screen.queryByText('No paper account')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restore paper trading panel' })).toHaveAttribute('aria-expanded', 'false');

    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });
    await waitFor(() => expect(screen.getByText('No paper account')).toBeInTheDocument());
    expect(screen.queryByRole('complementary', { name: 'Paper order ticket' })).not.toBeInTheDocument();

    await act(async () => {
      screen.getByRole('button', { name: 'Minimize paper trading panel' }).click();
    });

    expect(screen.queryByText('No paper account')).not.toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Paper order ticket' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restore paper trading panel' })).toHaveAttribute('aria-expanded', 'false');

    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });
    expect(screen.getByText('No paper account')).toBeInTheDocument();
  });

  it('opens the dedicated dashboard from the paper trading dock', async () => {
    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:BTC-USDT" bindingId={null} />);

    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });
    await act(async () => {
      screen.getByRole('tab', { name: 'Dashboard' }).click();
    });

    expect(screen.getByText('Paper trading dashboard view')).toBeInTheDocument();
    expect(screen.queryByText('No paper account')).not.toBeInTheDocument();
  });

  it('toggles the paper trading dock into fullscreen mode', async () => {
    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:BTC-USDT" bindingId={null} />);

    const dock = screen.getByRole('region', { name: 'Paper trading activity' });
    const fullscreen = screen.getByRole('button', { name: 'Fullscreen paper trading panel' });
    expect(dock).not.toHaveClass('is-fullscreen');

    await act(async () => fullscreen.click());
    expect(dock).toHaveClass('is-fullscreen');
    expect(screen.getByRole('button', { name: 'Exit fullscreen paper trading panel' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Minimize paper trading panel' })).toHaveAttribute('aria-expanded', 'true');

    await act(async () => screen.getByRole('button', { name: 'Exit fullscreen paper trading panel' }).click());
    expect(dock).not.toHaveClass('is-fullscreen');
  });

  it('projects a working order into the open positions view', async () => {
    const account = {
      account_id: 'paper-1', name: 'Paper account', base_currency: 'USD', commission_bps: '0',
      enabled: true, revision: 1,
    };
    const workingOrder = {
      account_id: account.account_id, order_id: 'order-1', instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
      binding_id: null, side: 'buy', order_type: 'market', quantity: '3', limit_price: null,
      stop_price: null, reference_price: '75.17', status: 'open', filled_quantity: '0',
      average_fill_price: null, idempotency_key: 'key-1', rejection_reason: null, reserved_cash: '0',
    };
    paperApi.accounts.mockResolvedValue([account]);
    paperApi.snapshot.mockResolvedValue({
      account,
      balances: [{ currency: 'USD', available: '100000', reserved: '0' }],
      positions: [],
      open_orders: [workingOrder],
      order_history: [workingOrder],
      recent_fills: [],
      recent_ledger: [],
    });

    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await waitFor(() => expect(paperApi.snapshot).toHaveBeenCalledWith(account.account_id));

    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });

    expect(screen.getByRole('tab', { name: 'Positions 1' })).toBeInTheDocument();
    expect(screen.getByText('BINANCE:SOLUSDT')).toBeInTheDocument();
    expect(screen.getByText('Working')).toBeInTheDocument();
  });

  it('shows the rejection reason in an order status tooltip', async () => {
    const account = {
      account_id: 'paper-1', name: 'Paper account', base_currency: 'USD', commission_bps: '0',
      enabled: true, revision: 1,
    };
    const rejectedOrder = {
      account_id: account.account_id, order_id: 'order-rejected', instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
      binding_id: null, side: 'buy', order_type: 'market', quantity: '5', limit_price: null,
      stop_price: null, reference_price: '74.55', status: 'rejected', filled_quantity: '0',
      average_fill_price: null, idempotency_key: 'key-rejected', rejection_reason: 'insufficient_paper_cash',
      reserved_cash: '0',
    };
    paperApi.accounts.mockResolvedValue([account]);
    paperApi.snapshot.mockResolvedValue({
      account,
      balances: [{ currency: 'USD', available: '100000', reserved: '0' }],
      positions: [],
      open_orders: [],
      order_history: [rejectedOrder],
      recent_fills: [],
      recent_ledger: [],
    });

    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await waitFor(() => expect(paperApi.snapshot).toHaveBeenCalledWith(account.account_id));
    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });
    await act(async () => {
      screen.getByRole('tab', { name: 'Orders' }).click();
    });

    expect(screen.getByRole('tooltip')).toHaveTextContent('Insufficient available paper cash at the fill price.');
    expect(screen.getByTitle('Insufficient available paper cash at the fill price.')).toBeInTheDocument();
  });
});

describe('TradingTerminalDock', () => {
  mockDockServices();

  it('labels the new order types and filters expired orders', async () => {
    const account = {
      account_id: 'paper-1', name: 'Paper account', base_currency: 'USD', commission_bps: '0',
      enabled: true, revision: 1,
    };
    const base = {
      account_id: account.account_id, instrument_id: 'crypto:BINANCE:spot:SOL-USDT', binding_id: null,
      quantity: '2', reference_price: null, filled_quantity: '0', average_fill_price: null,
      rejection_reason: null, reserved_cash: '0', expires_at: null, trail_amount: null, trail_percent: null,
      trail_water_mark: null, stop_triggered_at: null,
    };
    const trailing = {
      ...base, order_id: 'trail-1', idempotency_key: 'trail-1', side: 'sell', order_type: 'trailing_stop',
      limit_price: null, stop_price: '72.5', status: 'open', time_in_force: 'gtc', trail_percent: '2.5',
      trail_water_mark: '74.36',
    };
    const expired = {
      ...base, order_id: 'stop-limit-1', idempotency_key: 'stop-limit-1', side: 'buy', order_type: 'stop_limit',
      limit_price: '76', stop_price: '75.5', status: 'expired', time_in_force: 'day',
      expires_at: '2026-10-08T00:00:00Z', rejection_reason: 'time_in_force_expired',
    };
    paperApi.accounts.mockResolvedValue([account]);
    paperApi.snapshot.mockResolvedValue({
      account,
      balances: [{ currency: 'USD', available: '100000', reserved: '0' }],
      positions: [],
      open_orders: [trailing],
      order_history: [trailing, expired],
      recent_fills: [],
      recent_ledger: [],
    });

    render(<TradingTerminalDock instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await waitFor(() => expect(paperApi.snapshot).toHaveBeenCalledWith(account.account_id));
    await act(async () => {
      screen.getByRole('button', { name: 'Restore paper trading panel' }).click();
    });
    await act(async () => {
      screen.getByRole('tab', { name: 'Orders' }).click();
    });

    expect(screen.getByText('Trailing stop')).toBeInTheDocument();
    expect(screen.getByText('GTC · trail 2.5%')).toBeInTheDocument();
    expect(screen.getByText('Stop limit')).toBeInTheDocument();
    await act(async () => {
      screen.getByRole('tab', { name: 'Expired 1' }).click();
    });
    expect(screen.queryByText('Trailing stop')).not.toBeInTheDocument();
    const row = screen.getByRole('row', { name: /Stop limit/ });
    expect(row).toHaveTextContent('DAY');
    expect(row).toHaveTextContent('Expired');
  });
});
