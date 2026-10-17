import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const paperApi = vi.hoisted(() => ({
  accounts: vi.fn(),
  snapshot: vi.fn(),
  createAccount: vi.fn(),
  riskPreview: vi.fn(),
  placeRiskOrder: vi.fn(),
  placeOrder: vi.fn(),
  resetAccount: vi.fn(),
  archiveAccount: vi.fn(),
}));

const replayApi = vi.hoisted(() => ({
  advanceExecution: vi.fn(),
  placeExecutionOrder: vi.fn(),
}));

const tradingApi = vi.hoisted(() => ({
  quote: vi.fn(),
}));

vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));
vi.mock('./tradingReplayApi', () => ({ tradingReplayApi: replayApi }));
vi.mock('./tradingApi', () => ({ tradingApi }));

import { TradingPaperPanel } from './TradingPaperPanel';
import { requestPaperTicket } from './paperTicketRequests';
import { act } from '@testing-library/react';
import { useTradingReplayStore } from './tradingReplayStore';
import { useTradingStore } from './tradingStore';

const account = {
  account_id: 'paper-1',
  name: 'Paper Account 1',
  base_currency: 'USD',
  commission_bps: '0',
  enabled: true,
  revision: 1,
};

const accountSnapshot = () => ({
  account,
  balances: [{ currency: 'USD', available: '0', reserved: '100000' }],
  positions: [],
  open_orders: [],
  order_history: [],
  recent_fills: [],
  recent_ledger: [],
});

const riskPreview = () => ({
  allowed: true,
  policy_version: 'paper-risk-v1',
  reason_codes: [],
  limiting_reason_code: 'RISK_BUDGET',
  recommended_quantity: '3',
  account_equity: '100000',
  desired_risk_pct: '0.35',
  actual_risk_dollars: '350',
  actual_risk_pct: '0.35',
  estimated_notional: '226.86',
  buying_power_before: '100000',
  buying_power_after: '99773.14',
  aggregate_open_risk_dollars: '0',
  aggregate_open_risk_pct: '0',
  daily_realized_pnl: '0',
  daily_loss_remaining: '1500',
  spread_bps: '2.64',
  observation_age_seconds: '0.1',
  freshness_mode: 'polled',
  execution_eligible: true,
  unprotected_exposure_count: 0,
});

async function prepareRiskManagedBuy() {
  fireEvent.click(await screen.findByRole('switch', { name: 'Enable stop loss' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Stop loss price' }), { target: { value: '74.50' } });
  await waitFor(() => expect(paperApi.riskPreview).toHaveBeenCalledWith('paper-1', expect.objectContaining({
    instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
    entry_price: '75.62',
    stop_price: '74.5',
    desired_risk_pct: '0.35',
  })));
  await screen.findByRole('button', { name: /Buy 3 SOL\/USDT MARKET/ });
}

/** The services every TradingPaperPanel test starts from. */
function mockPaperPanelServices(): void {
  beforeEach(() => {
    useTradingStore.setState({ replayMode: false, replaySessionId: 0 });
    useTradingReplayStore.getState().clear();
    paperApi.accounts.mockResolvedValue([account]);
    paperApi.snapshot.mockResolvedValue(accountSnapshot());
    tradingApi.quote.mockResolvedValue({ price: '75.61', bid: '75.60', ask: '75.62' });
    paperApi.riskPreview.mockResolvedValue(riskPreview());
    paperApi.placeRiskOrder.mockRejectedValue(new Error('Paper Trading request failed (422): insufficient_paper_cash'));
    paperApi.placeOrder.mockRejectedValue(new Error('Paper Trading request failed (422): insufficient_paper_cash'));
    replayApi.advanceExecution.mockImplementation(async (snapshot) => snapshot);
    replayApi.placeExecutionOrder.mockImplementation(async (snapshot, order) => {
      const filled = {
        account_id: snapshot.account.account_id,
        ...order,
        status: 'filled',
        filled_quantity: order.quantity,
        average_fill_price: '101.35125',
        reserved_cash: '0',
      };
      return {
        snapshot: {
          ...snapshot,
          order_history: [...(snapshot.order_history ?? []), filled],
          open_orders: [],
        },
        order: filled,
      };
    });
  });

  afterEach(() => {
    useTradingStore.setState({ replayMode: false });
    useTradingReplayStore.getState().clear();
    vi.clearAllMocks();
  });
}

describe('TradingPaperPanel', () => {
  mockPaperPanelServices();

  it('fills the real ticket from a long position drawing: limit price, stop, target (TVP-3.6)', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await screen.findByRole('switch', { name: 'Enable stop loss' });
    act(() => requestPaperTicket({ instrumentId: 'crypto:BINANCE:spot:SOL-USDT', side: 'buy', orderType: 'limit' as const, entry: 75, stop: 74, target: 78, quantity: 40 }));
    expect(await screen.findByRole('textbox', { name: 'Limit price' })).toHaveValue('75');
    expect(screen.getByRole('textbox', { name: 'Stop loss price' })).toHaveValue('74');
    await waitFor(() => expect(paperApi.riskPreview).toHaveBeenCalledWith('paper-1', expect.objectContaining({ entry_price: '75', stop_price: '74' })));
    expect(paperApi.placeRiskOrder).not.toHaveBeenCalled();
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
  });

  it('fills a short position as a plain limit sell with its quantity and no protection (TVP-3.6)', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await screen.findByRole('switch', { name: 'Enable stop loss' });
    act(() => requestPaperTicket({ instrumentId: 'crypto:BINANCE:spot:SOL-USDT', side: 'sell', orderType: 'limit' as const, entry: 80, stop: 85, target: 70, quantity: 2 }));
    expect(await screen.findByRole('textbox', { name: 'Limit price' })).toHaveValue('80');
    expect(screen.getByRole('textbox', { name: 'Order quantity' })).toHaveValue('2');
    expect(screen.queryByRole('textbox', { name: 'Stop loss price' })).toBeNull();
    expect(screen.getByText(/turn shorting on in the account settings/)).toBeInTheDocument();
  });

  it('waits for the accounts before applying a pre-fill left before the panel opened (TVP-7.2a)', async () => {
    paperApi.accounts.mockResolvedValue([{ ...account, allow_short: true }]);
    requestPaperTicket({ instrumentId: 'crypto:BINANCE:spot:SOL-USDT', side: 'sell', orderType: 'limit' as const, entry: 80, stop: 85, target: 70, quantity: 2 });
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    // Known to be a shorting account by then: the sell is a short entry, and keeps its stop.
    expect(await screen.findByRole('textbox', { name: 'Stop loss price' })).toHaveValue('85');
  });

  it('on a shorting account, a sell with nothing long held is a risk-sized short entry (TVP-7.2a)', async () => {
    paperApi.accounts.mockResolvedValue([{ ...account, allow_short: true }]);
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await screen.findByRole('switch', { name: 'Enable stop loss' });
    act(() => requestPaperTicket({ instrumentId: 'crypto:BINANCE:spot:SOL-USDT', side: 'sell', orderType: 'limit' as const, entry: 80, stop: 85, target: 70, quantity: 2 }));
    expect(await screen.findByRole('textbox', { name: 'Stop loss price' })).toHaveValue('85');
    await waitFor(() => expect(paperApi.riskPreview).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      entry_price: '80', stop_price: '85', side: 'sell',
    })));
    expect(screen.getByText(/risk rule sizes the quantity/)).toBeInTheDocument();
  });

  it('a market buy hotkey fills the ticket and asks for the stop the risk rule needs (TVP-7.4)', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await screen.findByRole('switch', { name: 'Enable stop loss' });
    act(() => requestPaperTicket({ instrumentId: 'crypto:BINANCE:spot:SOL-USDT', side: 'buy', orderType: 'market', entry: null, stop: null, target: null, quantity: null, source: 'hotkey' }));
    expect(await screen.findByText(/Ticket filled from the trading hotkey. Set a stop loss/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Limit price' })).toBeNull();
    expect(paperApi.placeRiskOrder).not.toHaveBeenCalled();
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
  });

  it('shows the server rejection when a risk-sized order cannot be funded', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await prepareRiskManagedBuy();

    const quantity = screen.getByRole('textbox', { name: 'Order quantity' });
    expect(quantity).toHaveValue('3');
    expect(quantity).toHaveAttribute('readonly');
    fireEvent.click(screen.getByRole('button', { name: /Buy 3 SOL\/USDT MARKET/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Order not placed: insufficient available paper cash. Check reserved funds or wait for an open order to fill.',
    );
    expect(paperApi.placeRiskOrder).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      stop_loss: '74.50',
      desired_risk_pct: '0.35',
    }));
    expect(paperApi.placeRiskOrder.mock.calls[0][1]).not.toHaveProperty('quantity');
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
  });

  it('shows a confirmation after the server accepts a risk-sized paper entry', async () => {
    paperApi.placeRiskOrder.mockResolvedValue({
      preview: riskPreview(),
      order: {
        status: 'filled',
        order_id: 'risk-order',
        quantity: '3',
        average_fill_price: '75.63',
        limit_price: null,
        stop_price: null,
        reference_price: '75.62',
      },
      protection: { entry_order_id: 'risk-order', stop_loss: '74.50' },
    });

    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await prepareRiskManagedBuy();
    fireEvent.click(screen.getByRole('button', { name: /Buy 3 SOL\/USDT MARKET/ }));

    const confirmation = await screen.findByRole('status');
    expect(confirmation).toHaveClass('trading-paper-confirmation-toast');
    expect(confirmation).toHaveTextContent('Market order executed on');
    expect(confirmation).toHaveTextContent('BINANCE:SOLUSDT');
    expect(confirmation).toHaveTextContent('Buy 3');
    expect(paperApi.placeRiskOrder.mock.calls[0][1]).not.toHaveProperty('quantity');
  });

  it('leaves an accepted risk-sized market order to server-authoritative execution', async () => {
    paperApi.placeRiskOrder.mockResolvedValue({
      preview: riskPreview(),
      order: {
        status: 'open',
        order_id: 'risk-order',
        quantity: '3',
        reference_price: '75.62',
        average_fill_price: null,
        limit_price: null,
        stop_price: null,
      },
      protection: { entry_order_id: 'risk-order', stop_loss: '74.50' },
    });

    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await prepareRiskManagedBuy();
    fireEvent.click(screen.getByRole('button', { name: /Buy 3 SOL\/USDT MARKET/ }));

    expect(await screen.findByRole('status')).toHaveTextContent('Market order submitted on');
    expect(paperApi.placeRiskOrder).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      instrument_id: 'crypto:BINANCE:spot:SOL-USDT',
      order_type: 'market',
      trigger_price: null,
      stop_loss: '74.50',
    }));
    expect(paperApi.placeRiskOrder.mock.calls[0][1]).not.toHaveProperty('quantity');
  });
});

describe('TradingPaperPanel', () => {
  mockPaperPanelServices();

  it('sends a stop-limit entry with time in force and a trailing stop-loss leg', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Stop limit' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Stop price' }), { target: { value: '76' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Limit price' }), { target: { value: '76.40' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Time in force' }), { target: { value: 'day' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Enable stop loss' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Stop loss price' }), { target: { value: '74.50' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Trailing stop loss' }));
    // A stop-limit entry is sized at its limit price.
    await waitFor(() => expect(paperApi.riskPreview).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      entry_price: '76.4',
      stop_price: '74.5',
    })));
    fireEvent.click(await screen.findByRole('button', { name: /Buy 3 SOL\/USDT STOP LIMIT/ }));

    await waitFor(() => expect(paperApi.placeRiskOrder).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      order_type: 'stop_limit',
      trigger_price: '76',
      limit_price: '76.40',
      time_in_force: 'day',
      expires_at: null,
      trailing_stop_loss: true,
    })));
  });

  it('offers trailing stops for exits and sends the trail and expiry', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    expect(await screen.findByRole('tab', { name: 'Stop limit' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Trailing' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Sell/ }));
    fireEvent.click(await screen.findByRole('tab', { name: 'Trailing' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Trail distance' }), { target: { value: '2.5' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Trail unit' }), { target: { value: 'percent' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Time in force' }), { target: { value: 'gtd' } });
    fireEvent.change(screen.getByLabelText('Order expiry'), { target: { value: '2030-01-02T15:30' } });
    fireEvent.click(screen.getByRole('button', { name: /Sell 1 SOL\/USDT TRAILING STOP/ }));

    await waitFor(() => expect(paperApi.placeOrder).toHaveBeenCalledWith('paper-1', expect.objectContaining({
      side: 'sell',
      order_type: 'trailing_stop',
      trail_amount: null,
      trail_percent: '2.5',
      limit_price: null,
      stop_price: null,
      reference_price: null,
      time_in_force: 'gtd',
      expires_at: new Date('2030-01-02T15:30').toISOString(),
    })));
  });

  it('warns when a stop or stop-limit is already through the market', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Stop' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Stop price' }), { target: { value: '76' } });
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Stop price' }), { target: { value: '75' } });
    expect(screen.getByRole('note')).toHaveTextContent('The stop is already at or below the ask');

    fireEvent.click(screen.getByRole('tab', { name: 'Stop limit' }));
    expect(screen.getByRole('note')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Sell/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Stop price' }), { target: { value: '76' } });
    expect(screen.getByRole('note')).toHaveTextContent('The stop is already at or above the bid');
  });

  it('refuses a good-till-date expiry in the past', async () => {
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    fireEvent.click(await screen.findByRole('button', { name: /Sell/ }));
    fireEvent.click(await screen.findByRole('tab', { name: 'Limit' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Limit price' }), { target: { value: '80' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Time in force' }), { target: { value: 'gtd' } });
    fireEvent.change(screen.getByLabelText('Order expiry'), { target: { value: '2020-01-02T15:30' } });
    fireEvent.click(screen.getByRole('button', { name: /Sell 1 SOL\/USDT LIMIT/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('expiry');
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
  });

  it('keeps replay tickets to market, limit and stop orders', async () => {
    useTradingStore.setState({ replayMode: true, replaySessionId: 3 });
    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);
    await screen.findByRole('textbox', { name: 'Order quantity' });
    const types = screen.getAllByRole('tab').map((tab) => tab.textContent);
    expect(types).toEqual(expect.arrayContaining(['Market', 'Limit', 'Stop']));
    expect(types).not.toContain('Stop limit');
    expect(types).not.toContain('Trailing');
    fireEvent.click(screen.getByRole('tab', { name: 'Limit' }));
    expect(screen.queryByRole('combobox', { name: 'Time in force' })).not.toBeInTheDocument();
  });

  it('uses the replay bar through the shared server kernel without creating a persisted paper order', async () => {
    useTradingStore.setState({ replayMode: true, replaySessionId: 7 });
    useTradingReplayStore.getState().setBar({
      instrument_id: 'crypto:BINANCE:spot:SOL-USDT', interval: '1h',
      start_time: '2024-01-02T10:00:00Z', end_time: '2024-01-02T11:00:00Z',
      open: '100', high: '103', low: '99', close: '101.25', volume: '10', is_final: true,
      adjustment_mode: 'raw', session: '24x7', provider: 'replay-test',
      provider_event_id: null, provider_sequence: null, ingestion_revision: 1,
      received_at: '2024-01-02T11:00:01Z',
    });

    render(<TradingPaperPanel instrumentId="crypto:BINANCE:spot:SOL-USDT" bindingId={null} />);

    await screen.findByRole('textbox', { name: 'Order quantity' });
    expect(await screen.findByText('Replay only')).toBeInTheDocument();
    expect(screen.getAllByText('101.25').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Buy 1 SOL\/USDT MARKET/ }));

    expect(await screen.findByRole('status')).toHaveTextContent('Market order executed on');
    expect(paperApi.placeOrder).not.toHaveBeenCalled();
    expect(paperApi.placeRiskOrder).not.toHaveBeenCalled();
    expect(replayApi.placeExecutionOrder).toHaveBeenCalledWith(
      expect.objectContaining({ account: expect.objectContaining({ account_id: 'paper-1' }) }),
      expect.objectContaining({ quantity: '1', reference_price: '101.25' }),
      expect.objectContaining({ close: '101.25' }),
      // No bar has been advanced yet (no start bar chosen), so the order's bar is applied once here.
      true,
    );
    await waitFor(() => expect(useTradingReplayStore.getState().snapshot?.order_history).toHaveLength(1));
    expect(useTradingReplayStore.getState().snapshot?.order_history?.[0]).toMatchObject({
      status: 'filled', average_fill_price: '101.35125',
    });
  });
});