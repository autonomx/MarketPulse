import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const strategyApi = vi.hoisted(() => ({
  list: vi.fn(),
  universe: vi.fn(),
  events: vi.fn(),
  protections: vi.fn(),
}));
const operationsApi = vi.hoisted(() => ({ health: vi.fn() }));

vi.mock('./tradingStrategyApi', () => ({ tradingStrategyApi: strategyApi }));
vi.mock('./tradingStrategyOperationsApi', () => ({ tradingStrategyOperationsApi: operationsApi }));

import { TradingSymbolIntelligence } from './TradingSymbolIntelligence';

const instrumentId = 'equity:NASDAQ:OSRH';
const bindingId = 'alpaca:iex:equity:NASDAQ:OSRH';

const strategy = {
  strategy_id: 'gap-v2',
  account_id: 'paper-1',
  strategy_kind: 'gap_pullback_v1',
  strategy_version: '2.0.0',
  mode: 'shadow',
  active_universe_id: 'universe-2026-08-24',
  config: { strategy_version: '2.0.0' },
  risk: { kill_switch: false },
  enabled: true,
  archived_at: null,
  revision: 12,
};

const universe = {
  universe_id: 'universe-2026-08-24',
  session_date: '2026-08-24',
  evaluation_time: '2026-08-24T13:20:00Z',
  discovery_source: 'provider',
  source_fingerprint: 'frozen-universe',
  candidates: [{
    instrument_id: instrumentId,
    binding_id: bindingId,
    observed_at: '2026-08-24T13:19:50Z',
    previous_close: '0.31',
    premarket_price: '0.59',
    gap_pct: '90.32',
    premarket_volume: '12000000',
    premarket_dollar_volume: '6500000',
    tod_rvol: '18.4',
    float_shares: '19420000',
    spread_bps: '82',
    catalyst_evidence_ids: ['evidence-company-1', 'evidence-news-2'],
    dilution_flags: [],
    discovery_rank: 2,
  }],
};

const event = {
  strategy_id: 'gap-v2',
  event_id: 'event-1',
  run_id: 'run-1',
  instrument_id: instrumentId,
  event_type: 'state',
  state: 'higher_low_confirmed',
  reason_code: 'HIGHER_LOW_CONFIRMED',
  observed_at: '2026-08-24T13:49:00Z',
  idempotency_key: 'idem-1',
  payload: {
    strategy_revision: 12,
    session_id: 'session-123456789012345678901234',
    setup_id: 'setup-123456789012345678901234',
    trade_intent_id: 'intent-123456789012345678901234',
    risk_decision_id: 'risk-123456789012345678901234',
    features: {
      quality_score: 8,
      spread_bps: '82',
      opening_impulse_pct: '19.2',
      l1: '0.51',
      b1: '0.61',
      l2: '0.55',
      second_pullback_depth_pct: '9.8',
      session_vwap: '0.565',
      breakout_volume_ratio: '1.42',
    },
  },
};

const protection = {
  strategy_id: 'gap-v2',
  protection_id: 'protection-1',
  account_id: 'paper-1',
  instrument_id: instrumentId,
  entry_order_id: 'order-1',
  stop_price: '0.54',
  target_price: '0.72',
  quantity: '1000',
  status: 'active',
  revision: 3,
};

const health = {
  observed_at: '2026-08-24T13:49:01Z',
  state: 'healthy',
  reason_codes: [],
  risk: {
    state: 'healthy', reason_codes: [], account_id: 'paper-1', policy_source: 'active_strategy',
    equity: '100000', buying_power: '98000', open_risk_dollars: '0', open_risk_pct: '0',
    max_open_risk_pct: '1', daily_realized_pnl: '0', daily_loss_limit_dollars: '1500',
    daily_loss_remaining: '1500', max_daily_loss_pct: '1.5', unprotected_exposure_count: 0,
    position_count: 0, open_order_count: 0, active_protection_count: 0,
  },
  execution: {
    state: 'healthy', reason_codes: [], instrument_id: instrumentId,
    requested_binding_id: bindingId, resolved_binding_id: bindingId, provider: 'alpaca_iex',
    policy_version: 'execution-data-v1', execution_eligible: true,
    source_time: '2026-08-24T13:49:00.800Z', observation_age_ms: '200', spread_bps: '82',
    freshness_mode: 'live', session: 'regular', halted: false,
  },
  paper_only: true,
  live_broker_enabled: false,
  ai_order_placement_enabled: false,
};

describe('TradingSymbolIntelligence', () => {
  beforeEach(() => {
    strategyApi.list.mockResolvedValue([strategy]);
    strategyApi.universe.mockResolvedValue(universe);
    strategyApi.events.mockResolvedValue([event]);
    strategyApi.protections.mockResolvedValue([protection]);
    operationsApi.health.mockResolvedValue(health);
  });

  afterEach(() => vi.clearAllMocks());

  it('correlates frozen discovery, deterministic state, protection and execution health', async () => {
    render(<TradingSymbolIntelligence instrumentId={instrumentId} bindingId={bindingId} accountId="paper-1" />);

    expect(await screen.findByText('OSRH · Symbol Intelligence')).toBeInTheDocument();
    await waitFor(() => expect(operationsApi.health).toHaveBeenCalledWith('paper-1', {
      instrumentId,
      bindingId,
    }));
    expect(strategyApi.universe).toHaveBeenCalledWith('universe-2026-08-24');
    expect(strategyApi.events).toHaveBeenCalledWith('gap-v2', 500);
    expect(screen.getByText('90.32%')).toBeInTheDocument();
    expect(screen.getAllByText('higher low confirmed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Eligible')).toBeInTheDocument();
    expect(screen.getByText('82 bps')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText(/setup-1234/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only intelligence/)).toBeInTheDocument();
  });

  it('fails closed when no paper account is selected', () => {
    render(<TradingSymbolIntelligence instrumentId={instrumentId} bindingId={bindingId} accountId={null} />);

    expect(screen.getByText('Symbol Intelligence')).toBeInTheDocument();
    expect(screen.getByText(/Select a paper account/)).toBeInTheDocument();
    expect(strategyApi.list).not.toHaveBeenCalled();
    expect(operationsApi.health).not.toHaveBeenCalled();
  });
});
