import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const analyticsApi = vi.hoisted(() => ({ journal: vi.fn() }));
vi.mock('./tradingPaperAnalyticsApi', () => ({ tradingPaperAnalyticsApi: analyticsApi }));

import { TradingTradeJournal } from './TradingTradeJournal';

const osrh = 'equity:NASDAQ:OSRH';
const xyz = 'equity:NASDAQ:XYZ';

const response = {
  account_id: 'paper-1',
  entries: [
    {
      trade_id: 'trade-osrh-1',
      account_id: 'paper-1',
      epoch_id: 'epoch-0001',
      strategy_id: 'gap-v2',
      strategy_version: '2.0.0',
      strategy_revision: 12,
      strategy_run_id: 'run-1',
      profile_fingerprint: 'profile-1',
      universe_id: 'universe-1',
      instrument_id: osrh,
      session_date: '2026-08-24',
      entry_time: '2026-08-24T13:50:00Z',
      exit_time: '2026-08-24T14:06:00Z',
      holding_seconds: 960,
      entry_signal_event_id: 'signal-event-1',
      entry_order_id: 'entry-order-1',
      exit_order_id: 'exit-order-1',
      entry_fill_ids: ['entry-fill-1'],
      exit_fill_ids: ['exit-fill-1'],
      session_id: 'session-123456789012345678901234',
      setup_id: 'setup-123456789012345678901234',
      trade_intent_id: 'intent-123456789012345678901234',
      risk_decision_id: 'risk-123456789012345678901234',
      protection_id: 'protection-123456789012345678901234',
      lifecycle_state: 'closed',
      review_state: 'pending',
      average_entry_price: '0.60',
      average_exit_price: '0.72',
      quantity: '1000',
      initial_risk_dollars: '100',
      initial_stop: '0.54',
      initial_target: '0.72',
      realized_pnl: '120',
      r_result: '1.2',
      mae_r: '-0.3',
      mfe_r: '1.4',
      signal_to_executable_bps: '10',
      fill_slippage_bps: '4',
      implementation_shortfall_bps: '14',
      exit_reason: 'take_profit',
      setup_features: { quality_score: 8 },
      execution_features: { provider: 'alpaca_iex' },
      outcome: 'win',
      automatic_observations: [
        'Outcome: win; realized P&L +120.00; +1.200R',
        'Excursion: MAE -0.300R; MFE +1.400R',
      ],
      events: [
        {
          event_id: 'signal-event-1',
          run_id: 'run-1',
          event_type: 'entry_order_submitted',
          state: 'entry_ready',
          reason_code: 'RISK_ACCEPTED',
          observed_at: '2026-08-24T13:49:59Z',
        },
      ],
    },
    {
      trade_id: 'trade-xyz-1',
      account_id: 'paper-1',
      epoch_id: 'epoch-0001',
      strategy_id: 'gap-v2',
      strategy_version: '2.0.0',
      strategy_revision: 12,
      instrument_id: xyz,
      session_date: '2026-08-23',
      entry_time: '2026-08-23T14:00:00Z',
      exit_time: '2026-08-23T14:10:00Z',
      holding_seconds: 600,
      entry_order_id: 'entry-order-2',
      exit_order_id: 'exit-order-2',
      entry_fill_ids: ['entry-fill-2'],
      exit_fill_ids: ['exit-fill-2'],
      lifecycle_state: 'closed',
      review_state: 'reviewed',
      average_entry_price: '2.00',
      average_exit_price: '1.80',
      quantity: '100',
      realized_pnl: '-20',
      r_result: '-1',
      setup_features: {},
      execution_features: {},
      outcome: 'loss',
      automatic_observations: ['Outcome: loss; realized P&L -20.00; -1.000R'],
      events: [],
    },
  ],
};

describe('TradingTradeJournal', () => {
  beforeEach(() => {
    analyticsApi.journal.mockClear();
    analyticsApi.journal.mockResolvedValue(response);
  });
  afterEach(() => vi.clearAllMocks());

  it('renders canonical trade evidence and deterministic observations', async () => {
    render(<TradingTradeJournal accountId="paper-1" instrumentId={osrh} />);

    expect(await screen.findByText('Automatic Journal')).toBeInTheDocument();
    await waitFor(() => expect(analyticsApi.journal).toHaveBeenCalledWith({ accountId: 'paper-1', limit: 100 }));
    expect(screen.getByText('OSRH')).toBeInTheDocument();
    expect(screen.getByText('+1.20R')).toBeInTheDocument();
    expect(screen.getByText('Outcome: win; realized P&L +120.00; +1.200R')).toBeInTheDocument();
    expect(screen.getByText(/entry-fill-1/)).toBeInTheDocument();
    expect(screen.getByText(/setup-12345/)).toBeInTheDocument();
    expect(screen.getByText('entry order submitted')).toBeInTheDocument();
    expect(screen.getByText(/Operator gate unchanged/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only projection/)).toBeInTheDocument();
  });

  it('can scope the journal to the selected symbol without changing server evidence or retaining an out-of-scope detail', async () => {
    render(<TradingTradeJournal accountId="paper-1" instrumentId={osrh} />);
    await screen.findByText('OSRH');

    fireEvent.click(screen.getByRole('button', { name: /XYZ/ }));
    expect(screen.getByRole('article', { name: /Journal detail for XYZ/ })).toBeInTheDocument();

    const callsBeforeScopeChange = analyticsApi.journal.mock.calls.length;
    fireEvent.click(screen.getByRole('checkbox', { name: /Only OSRH/ }));

    expect(screen.queryByText('XYZ')).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /Journal detail for XYZ/ })).not.toBeInTheDocument();
    expect(screen.getByRole('article', { name: /Journal detail for OSRH/ })).toBeInTheDocument();
    expect(analyticsApi.journal.mock.calls.length).toBe(callsBeforeScopeChange);
  });

  it('fails closed when no paper account is selected', () => {
    analyticsApi.journal.mockClear();
    render(<TradingTradeJournal accountId={null} instrumentId={osrh} />);

    expect(screen.getByText(/Select a paper account/)).toBeInTheDocument();
    expect(analyticsApi.journal).not.toHaveBeenCalled();
  });
});
