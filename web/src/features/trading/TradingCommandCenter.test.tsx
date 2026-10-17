import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const operationsApi = vi.hoisted(() => ({
  health: vi.fn(),
  status: vi.fn(),
}));

const strategyApi = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('./tradingStrategyOperationsApi', () => ({ tradingStrategyOperationsApi: operationsApi }));
vi.mock('./tradingStrategyApi', () => ({ tradingStrategyApi: strategyApi }));

import { TradingCommandCenter } from './TradingCommandCenter';

const monitor = {
  configured_enabled: true,
  registered: true,
  running: true,
  interval_seconds: 1,
  last_run_at: '2026-08-24T14:00:00Z',
  last_error: null,
  counters: {},
};

const health = {
  observed_at: '2026-08-24T14:00:01Z',
  state: 'healthy',
  reason_codes: ['INSTRUMENT_NOT_SELECTED'],
  risk: {
    state: 'healthy',
    reason_codes: [],
    account_id: 'paper-1',
    policy_source: 'active_strategy',
    equity: '100000',
    buying_power: '96500',
    open_risk_dollars: '350',
    open_risk_pct: '0.35',
    max_open_risk_pct: '1.0',
    daily_realized_pnl: '125.50',
    daily_loss_limit_dollars: '1500',
    daily_loss_remaining: '1625.50',
    max_daily_loss_pct: '1.5',
    unprotected_exposure_count: 0,
    position_count: 1,
    open_order_count: 0,
    active_protection_count: 1,
  },
  execution: {
    state: 'unknown',
    reason_codes: ['INSTRUMENT_NOT_SELECTED'],
    instrument_id: null,
    requested_binding_id: null,
    resolved_binding_id: null,
    provider: null,
    policy_version: null,
    execution_eligible: false,
    source_time: null,
    observation_age_ms: null,
    spread_bps: null,
    freshness_mode: null,
    session: null,
    halted: null,
  },
  paper_only: true,
  live_broker_enabled: false,
  ai_order_placement_enabled: false,
};

const strategy = {
  strategy_id: 'gap-v2',
  account_id: 'paper-1',
  strategy_kind: 'gap_pullback_v1',
  strategy_version: '2.0.0',
  mode: 'auto_paper',
  active_universe_id: 'u-1',
  config: { strategy_version: '2.0.0' },
  risk: { kill_switch: false },
  enabled: true,
  archived_at: null,
  revision: 9,
};

describe('TradingCommandCenter', () => {
  beforeEach(() => {
    operationsApi.health.mockResolvedValue(health);
    operationsApi.status.mockResolvedValue({
      observed_at: '2026-08-24T14:00:01Z',
      paper_monitor: monitor,
      strategy_monitor: monitor,
      deep_recovery_shadow_monitor: monitor,
      prospective_economic_monitor: monitor,
      universe_archive_monitor: monitor,
      v2_qualification_monitor: monitor,
      alpaca_status_monitor: monitor,
      execution_authority: false,
    });
    strategyApi.get.mockResolvedValue(strategy);
  });

  afterEach(() => vi.clearAllMocks());

  it('renders server-authoritative daily risk and runtime state', async () => {
    render(<TradingCommandCenter accountId="paper-1" strategyId="gap-v2" currency="USD" />);

    expect(await screen.findByText('Daily Command Center')).toBeInTheDocument();
    await waitFor(() => expect(operationsApi.health).toHaveBeenCalledWith('paper-1'));
    expect(screen.getByText('+125.50 USD')).toBeInTheDocument();
    expect(screen.getByText('350.00 USD · 0.35%')).toBeInTheDocument();
    expect(screen.getByText('96,500.00 USD')).toBeInTheDocument();
    expect(screen.getByText('1 positions · 0 orders')).toBeInTheDocument();
    expect(screen.getByText('AUTO PAPER')).toBeInTheDocument();
    expect(screen.getByText('Paper only')).toBeInTheDocument();
    expect(screen.getByText(/Live broker OFF · AI order placement OFF/)).toBeInTheDocument();
    expect(screen.getByText('Account checks clear')).toBeInTheDocument();
  });

  it('surfaces server blocks without recomputing risk in the browser', async () => {
    operationsApi.health.mockResolvedValue({
      ...health,
      state: 'blocked',
      reason_codes: ['UNPROTECTED_OPEN_EXPOSURE', 'INSTRUMENT_NOT_SELECTED'],
      risk: {
        ...health.risk,
        state: 'blocked',
        reason_codes: ['UNPROTECTED_OPEN_EXPOSURE'],
        unprotected_exposure_count: 1,
      },
    });

    render(<TradingCommandCenter accountId="paper-1" strategyId="gap-v2" currency="USD" />);

    expect(await screen.findByText('BLOCKED')).toBeInTheDocument();
    expect(screen.getByText(/unprotected open exposure/)).toBeInTheDocument();
    expect(screen.getByText('1 protections · 1 unprotected')).toBeInTheDocument();
  });
});
