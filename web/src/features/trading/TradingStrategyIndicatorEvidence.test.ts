import { describe, expect, it } from 'vitest';
import { collectProspectiveIndicatorEvidence } from './TradingStrategyIndicatorEvidence';
import type { StrategyEvent } from './tradingStrategyTypes';
import { fixture } from '../../test/fixture';

function event(execution: Record<string, unknown>): StrategyEvent {
  return fixture({
    event_id: 'event-1',
    strategy_id: 'strategy-1',
    event_type: 'shadow_execution',
    instrument_id: 'equity:NASDAQ:TEST',
    observed_at: '2026-08-24T13:45:00Z',
    state: 'entry_ready',
    reason_code: 'FAILED_SELL_OFF_CONFIRMED',
    idempotency_key: 'strategy-1:event-1',
    payload: { execution },
  });
}

describe('prospective indicator entry evidence', () => {
  it('extracts the persisted frozen verdict, feature row, and multi-timeframe snapshot', () => {
    const values = collectProspectiveIndicatorEvidence([
      event({
        indicator_context_source: 'alpaca_iex_same_day_1m',
        indicator_context_partial_market: true,
        indicator_context_cutoff: '2026-08-24T13:44:30Z',
        indicator_context_bar_count: 344,
        indicator_context_full_warmup: true,
        indicator_entry_confirmed: false,
        indicator_entry_reason_codes: ['INDICATOR_1M_MACD_BEARISH'],
        indicator_context_error: null,
        prospective_signal_features: {
          schema_version: 'v2-prospective-signal-features-1',
          immutable_fingerprint: 'abc123',
          completeness: {
            premarket_available: true,
            research_available: true,
            halt_history_complete: true,
            momentum_full_warmup: true,
            all_core_available: true,
          },
          premarket: {
            range_pct: '18.5',
            close_vs_high_pct: '-2.5',
            last_30m_return_pct: '3.2',
          },
          research: {
            catalyst: {
              catalyst_type: 'earnings',
              primary_confirmed: true,
            },
            supply: {
              resolution_status: 'clear',
              immediate_supply_risk: false,
            },
          },
          halt_history: {
            halt_event_count: 1,
            halted_at_decision: false,
          },
        },
        indicator_context: {
          one_minute: {
            close: '10.10',
            ema9: '10.05',
            ema20: '9.95',
            macd: '0.04',
            macd_signal: '0.05',
            macd_histogram: '-0.01',
            stochastic_rsi_k: '70',
            stochastic_rsi_d: '65',
          },
          five_minute: {
            close: '10.10',
            ema9: '9.90',
            ema20: '9.70',
            macd: '0.20',
            macd_signal: '0.15',
            macd_histogram: '0.05',
            stochastic_rsi_k: '82',
            stochastic_rsi_d: '77',
          },
        },
      }),
    ]);

    expect(values).toHaveLength(1);
    expect(values[0]).toMatchObject({
      instrumentId: 'equity:NASDAQ:TEST',
      cutoff: '2026-08-24T13:44:30Z',
      source: 'alpaca_iex_same_day_1m',
      partialMarket: true,
      barCount: 344,
      fullWarmup: true,
      confirmed: false,
      reasonCodes: ['INDICATOR_1M_MACD_BEARISH'],
    });
    expect(values[0].oneMinute.macdHistogram).toBe('-0.01');
    expect(values[0].fiveMinute.stochasticRsiK).toBe('82');
    expect(values[0].prospective).toMatchObject({
      fingerprint: 'abc123',
      allCoreAvailable: true,
      premarketAvailable: true,
      premarketRangePct: '18.5',
      premarketCloseVsHighPct: '-2.5',
      premarketLast30mReturnPct: '3.2',
      researchAvailable: true,
      catalystType: 'earnings',
      primaryCatalystConfirmed: true,
      supplyResolution: 'clear',
      immediateSupplyRisk: false,
      haltHistoryComplete: true,
      haltEventCount: 1,
      haltedAtDecision: false,
    });
  });

  it('ignores old shadow events that predate the frozen persisted verdict', () => {
    const values = collectProspectiveIndicatorEvidence([
      event({ execution_eligible: true, indicator_context: null }),
    ]);

    expect(values).toEqual([]);
  });

  it('preserves unavailable telemetry without pretending it was a veto', () => {
    const values = collectProspectiveIndicatorEvidence([
      event({
        indicator_context_source: 'alpaca_iex_same_day_1m',
        indicator_context_partial_market: true,
        indicator_context_bar_count: 0,
        indicator_context_full_warmup: false,
        indicator_entry_confirmed: null,
        indicator_entry_reason_codes: [],
        indicator_context_error: 'ProviderDataUnavailableError: no bars',
        indicator_context: null,
      }),
    ]);

    expect(values[0].confirmed).toBeNull();
    expect(values[0].fullWarmup).toBe(false);
    expect(values[0].error).toContain('ProviderDataUnavailableError');
    expect(values[0].reasonCodes).toEqual([]);
    expect(values[0].prospective.allCoreAvailable).toBe(false);
    expect(values[0].prospective.researchAvailable).toBe(false);
  });
});
