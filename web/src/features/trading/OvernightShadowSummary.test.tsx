import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { OvernightShadowSummary } from './OvernightShadowSummary';
import type { OvernightShadowTradingStrategyConfig, StrategyEvent } from './tradingStrategyTypes';

const strategy = {
  strategy_id: 'overnight-trading-strategy-shadow',
  parent_strategy_id: null,
  account_id: 'omnix-overnight-shadow',
  strategy_kind: 'overnight_shadow_v1',
  strategy_version: '1.0.0',
  mode: 'shadow',
  active_universe_id: null,
  enabled: true,
  revision: 1,
  risk: {},
  config: {
    strategy_id: 'overnight_shadow_v1', strategy_version: '1.0.0',
    data_cutoff_et: '15:30:00', decision_et: '15:50:00', entry_et: '15:55:00', label_after_et: '10:05:00',
    minimum_price: '1', maximum_price: '20', max_picks_per_arm: 5, adv_sessions: 20, min_adv_sessions: 10,
    arms: [
      { arm_id: 'day2-runner', enabled: true, min_change: '0.30', max_change: null, min_volume_ratio: '5', min_close_location: '0.80' },
      { arm_id: 'strong-close', enabled: true, min_change: '0.10', max_change: '0.30', min_volume_ratio: '3', min_close_location: '0.90' },
    ],
  },
} as unknown as OvernightShadowTradingStrategyConfig;

function event(event_type: string, run_id: string, payload: Record<string, unknown>, event_id = event_type): StrategyEvent {
  return { strategy_id: strategy.strategy_id, event_id, run_id, instrument_id: 'x', event_type, state: 'recorded',
    observed_at: '2026-10-13T19:51:00Z', idempotency_key: event_id, payload } as unknown as StrategyEvent;
}

describe('OvernightShadowSummary', () => {
  it('shows the arm rules, latest picks and scorecards read-only', () => {
    render(<OvernightShadowSummary strategy={strategy} events={[
      event('overnight_pick', 'overnight:2026-10-12', { arm_id: 'day2-runner', candidate: { symbol: 'OLD', change: '0.5' } }, 'p0'),
      event('overnight_pick', 'overnight:2026-10-13', { arm_id: 'strong-close', candidate: { symbol: 'NEWX', change: '0.15' } }, 'p1'),
      event('overnight_scorecard', 'overnight:2026-10-12', {
        entry_session: '2026-10-12',
        arms: [{ arm_id: 'day2-runner', picks: 1, labelled: 1, mean_net_return: '0.05', median_net_return: '0.05', winners: 1, big_gap_ups: 0, big_gap_downs: 0 }],
        population_10pct: { arm_id: 'population-10pct', picks: 3, labelled: 3, mean_net_return: '-0.02', median_net_return: '-0.01', winners: 1, big_gap_ups: 0, big_gap_downs: 0 },
      }, 's1'),
    ]} />);
    expect(screen.getByText(/SHADOW research only — no orders are placed/)).toBeTruthy();
    expect(screen.getByText('day2-runner', { selector: 'strong' })).toBeTruthy();
    expect(screen.getByText(/≥ 10.00% and < 30.00%/)).toBeTruthy();
    expect(screen.getByText(/strong-close · NEWX · 15.00% today/)).toBeTruthy();
    expect(screen.queryByText(/OLD/)).toBeNull();
    expect(screen.getAllByText('5.00%', { selector: 'td' })).toHaveLength(2); // mean and median
    expect(screen.getByText('population-10pct')).toBeTruthy();
  });

  it('explains when nothing has been recorded yet', () => {
    render(<OvernightShadowSummary strategy={strategy} events={[]} />);
    expect(screen.getByText('No picks recorded yet.')).toBeTruthy();
    expect(screen.getByText(/first scorecard appears the morning after/)).toBeTruthy();
  });
});
