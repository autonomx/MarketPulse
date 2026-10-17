import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PaperTradeJournalEntry } from './tradingPaperAnalyticsApi';
import { TradingAutomatedReview } from './TradingAutomatedReview';

const entry: PaperTradeJournalEntry = {
  trade_id: 'trade-1', account_id: 'paper-1', epoch_id: 'epoch-1', strategy_id: 'gap-v2',
  instrument_id: 'equity:NASDAQ:OSRH', session_date: '2026-08-24',
  entry_time: '2026-08-24T13:50:00Z', exit_time: '2026-08-24T14:06:00Z', holding_seconds: 960,
  entry_order_id: 'entry-order-1', exit_order_id: 'exit-order-1', entry_fill_ids: ['entry-fill-1'], exit_fill_ids: ['exit-fill-1'],
  session_id: 'session-1', setup_id: 'setup-1', trade_intent_id: 'intent-1', risk_decision_id: 'risk-1', protection_id: 'protection-1',
  lifecycle_state: 'closed', review_state: 'pending', average_entry_price: '0.60', average_exit_price: '0.54', quantity: '1000',
  realized_pnl: '-60', r_result: '-1', mae_r: '-1.1', mfe_r: '0.2', implementation_shortfall_bps: '12',
  exit_reason: 'stop_loss', setup_features: {}, execution_features: {}, outcome: 'loss', automatic_observations: [], events: [],
  strategy_version: null, strategy_revision: null, strategy_run_id: null, profile_fingerprint: null, universe_id: null,
  entry_signal_event_id: null, initial_risk_dollars: null, initial_stop: null, initial_target: null,
  signal_to_executable_bps: null, fill_slippage_bps: null,
};

describe('TradingAutomatedReview', () => {
  it('renders deterministic findings while explicitly preserving operator authority', () => {
    render(<TradingAutomatedReview entry={entry} />);

    expect(screen.getByText('Automated review')).toBeInTheDocument();
    expect(screen.getByText('ATTENTION')).toBeInTheDocument();
    expect(screen.getByText('realized loss')).toBeInTheDocument();
    expect(screen.getByText(/Persisted operator review: pending/)).toBeInTheDocument();
    expect(screen.getByText(/cannot mark a trade reviewed or authorize AUTO PAPER/)).toBeInTheDocument();
  });
});
