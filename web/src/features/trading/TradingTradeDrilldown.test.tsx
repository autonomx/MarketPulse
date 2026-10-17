import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const analyticsApi = vi.hoisted(() => ({ journal: vi.fn() }));
vi.mock('./tradingPaperAnalyticsApi', () => ({ tradingPaperAnalyticsApi: analyticsApi }));

import { TradingTradeDrilldown } from './TradingTradeDrilldown';

const autoTrade = {
  trade_id: 'trade-osrh-1',
  source: 'auto_paper' as const,
  strategy_id: 'gap-v2',
  strategy_version: '2.0.0',
  profile_fingerprint: 'profile-1',
  epoch_id: 'epoch-0001',
  universe_id: 'universe-1',
  instrument_id: 'equity:NASDAQ:OSRH',
  session_date: '2026-08-24',
  entry_time: '2026-08-24T13:50:00Z',
  exit_time: '2026-08-24T14:06:00Z',
  exit_reason: 'take_profit',
  quantity: '1000',
  realized_pnl: '120',
  r_result: '1.2',
  mae_r: '-0.3',
  mfe_r: '1.4',
  signal_to_executable_bps: '10',
  fill_slippage_bps: '4',
  implementation_shortfall_bps: '14',
  initial_stop: '0.54',
  initial_target: '0.72',
  setup_features: { quality_score: 8 },
};

const journalEntry = {
  trade_id: 'trade-osrh-1',
  account_id: 'paper-1',
  epoch_id: 'epoch-0001',
  strategy_id: 'gap-v2',
  strategy_version: '2.0.0',
  strategy_revision: 12,
  strategy_run_id: 'run-1',
  profile_fingerprint: 'profile-1',
  universe_id: 'universe-1',
  instrument_id: 'equity:NASDAQ:OSRH',
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
  outcome: 'win' as const,
  automatic_observations: ['Outcome: win; realized P&L +120.00; +1.200R'],
  events: [{
    event_id: 'signal-event-1',
    run_id: 'run-1',
    event_type: 'entry_order_submitted',
    state: 'entry_ready',
    reason_code: 'RISK_ACCEPTED',
    observed_at: '2026-08-24T13:49:59Z',
  }],
};

describe('TradingTradeDrilldown', () => {
  beforeEach(() => {
    analyticsApi.journal.mockReset();
    analyticsApi.journal.mockResolvedValue({ account_id: 'paper-1', entries: [journalEntry] });
  });

  it('resolves the exact canonical AUTO PAPER trade, focuses the dialog controls, and renders lifecycle evidence', async () => {
    render(<TradingTradeDrilldown trade={autoTrade} accountId="paper-1" currency="USD" onClose={vi.fn()} />);

    expect(screen.getByRole('dialog', { name: /OSRH/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close trade drill-down' })).toHaveFocus();
    await waitFor(() => expect(analyticsApi.journal).toHaveBeenCalledWith({
      accountId: 'paper-1',
      strategyId: 'gap-v2',
      epochId: 'epoch-0001',
      startDate: '2026-08-24',
      endDate: '2026-08-24',
      limit: 200,
    }));
    expect(await screen.findByText('Canonical lifecycle')).toBeInTheDocument();
    expect(screen.getByText('Outcome: win; realized P&L +120.00; +1.200R')).toBeInTheDocument();
    expect(screen.getByText(/Operator gate unchanged/)).toBeInTheDocument();
    expect(screen.getByText(/entry-fill-1/)).toBeInTheDocument();
    expect(screen.getByText('entry order submitted')).toBeInTheDocument();
    expect(screen.getByText(/cannot place, modify, cancel, or authorize/)).toBeInTheDocument();
  });

  it('keeps SHADOW drill-down prospective and does not request canonical order evidence', () => {
    const shadowTrade = {
      ...autoTrade,
      trade_id: 'shadow-osrh-1',
      source: 'shadow_replay' as const,
      realized_pnl: null,
      setup_features: { quality_score: 8, recovery_pct: 0.42 },
    };
    render(<TradingTradeDrilldown trade={shadowTrade} accountId="paper-1" currency="USD" onClose={vi.fn()} />);

    expect(screen.getByText('Prospective SHADOW outcome')).toBeInTheDocument();
    expect(screen.getByText(/No paper order, fill, protection, or risk-decision identity is implied/)).toBeInTheDocument();
    expect(screen.getByText('quality score')).toBeInTheDocument();
    expect(analyticsApi.journal).not.toHaveBeenCalled();
  });

  it('closes with Escape', () => {
    const onClose = vi.fn();
    render(<TradingTradeDrilldown trade={{ ...autoTrade, source: 'shadow_replay' as const }} accountId="paper-1" currency="USD" onClose={onClose} />);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
