import { describe, expect, it } from 'vitest';
import type { PaperTradeJournalEntry } from './tradingPaperAnalyticsApi';
import { deriveAutomatedTradeReview } from './deriveAutomatedTradeReview';

function entry(overrides: Partial<PaperTradeJournalEntry> = {}): PaperTradeJournalEntry {
  return {
    trade_id: 'trade-1',
    account_id: 'paper-1',
    epoch_id: 'epoch-1',
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
    session_id: 'session-1',
    setup_id: 'setup-1',
    trade_intent_id: 'intent-1',
    risk_decision_id: 'risk-1',
    protection_id: 'protection-1',
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
    mae_r: '-0.2',
    mfe_r: '1.2',
    signal_to_executable_bps: '0',
    fill_slippage_bps: '0',
    implementation_shortfall_bps: '0',
    exit_reason: 'take_profit',
    setup_features: {},
    execution_features: {},
    outcome: 'win',
    automatic_observations: [],
    events: [],
    ...overrides,
  };
}

describe('deriveAutomatedTradeReview', () => {
  it('keeps a complete target exit routine and preserves the separate operator gate', () => {
    const review = deriveAutomatedTradeReview(entry());

    expect(review.version).toBe('trade-review-v1');
    expect(review.priority).toBe('routine');
    expect(review.findings.map((finding) => finding.code)).toContain('TARGET_EXIT');
    expect(review.requires_operator_review).toBe(true);
    expect(review.operator_review_state).toBe('pending');
  });

  it('raises deterministic attention for realized loss, 1R adverse excursion, and positive shortfall', () => {
    const review = deriveAutomatedTradeReview(entry({
      realized_pnl: '-100',
      r_result: '-1',
      mae_r: '-1.15',
      mfe_r: '0.35',
      implementation_shortfall_bps: '18',
      exit_reason: 'stop_loss',
      outcome: 'loss',
    }));

    expect(review.priority).toBe('attention');
    expect(review.findings.map((finding) => finding.code)).toEqual(expect.arrayContaining([
      'REALIZED_LOSS',
      'MAE_REACHED_INITIAL_RISK',
      'POSITIVE_IMPLEMENTATION_SHORTFALL',
      'MFE_EXCEEDED_REALIZED_R',
      'STOP_EXIT',
    ]));
  });

  it('treats missing canonical lifecycle or fills as high priority instead of inventing evidence', () => {
    const review = deriveAutomatedTradeReview(entry({
      protection_id: null,
      exit_fill_ids: [],
    }));

    expect(review.priority).toBe('high');
    expect(review.findings).toContainEqual(expect.objectContaining({
      code: 'CANONICAL_LIFECYCLE_INCOMPLETE',
      severity: 'high',
    }));
  });

  it('never replaces an existing operator review decision', () => {
    const review = deriveAutomatedTradeReview(entry({ review_state: 'reviewed' }));

    expect(review.requires_operator_review).toBe(false);
    expect(review.operator_review_state).toBe('reviewed');
  });
});
