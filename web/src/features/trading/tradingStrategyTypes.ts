import type { components } from './api/generated';
export type StrategyMode = 'off' | 'shadow' | 'auto_paper';
export type FloatPreferenceMode = 'ignore' | 'score' | 'require';
export type StrategyBarInterval = '1m' | '5m';
export type HistoricalUniverseMode = 'captured_only' | 'captured_or_reconstructed' | 'reconstructed_only';
export type HistoricalUniverseOrigin = 'captured' | 'reconstructed';
export type BacktestResultQuality = 'exact' | 'mixed' | 'approximate' | 'unavailable';

export type GapPullbackConfig = {
  strategy_id: 'gap_pullback_v1';
  strategy_version: '1.0.0' | '1.1.0' | '1.2.0' | '2.0.0';
  structure_interval: StrategyBarInterval;
  execution_interval: StrategyBarInterval;
  universe_scan_time_et?: string;
  universe_discovery_source?: 'yahoo' | 'finviz';
  auto_archive_daily_universe?: boolean;
  universe_archive_grace_minutes?: number;
  universe_discovery_count?: number;
  minimum_gap_pct: string | number;
  minimum_price: string | number;
  maximum_price: string | number;
  minimum_premarket_dollar_volume: string | number;
  minimum_tod_rvol: string | number;
  allow_missing_tod_rvol: boolean;
  maximum_spread_bps: string | number;
  preferred_float_min_shares: string | number;
  preferred_float_max_shares: string | number;
  float_preference_mode: FloatPreferenceMode;
  require_catalyst_evidence: boolean;
  reject_dilution_flags: string[];
  opening_impulse_min_pct: string | number;
  pullback_min_pct: string | number;
  pullback_max_pct: string | number;
  pullback_volume_max_ratio: string | number;
  higher_low_buffer_bps: string | number;
  breakout_volume_ratio: string | number;
  pivot_left_bars: number;
  pivot_right_bars: number;
  volume_lookback_bars: number;
  require_breakout_hold: boolean;
  breakout_hold_bars: number;
  breakout_hold_tolerance_bps: string | number;
  minimum_quality_score: number;
  // Version 2.0.0-only fields. Backend defaults populate these on persisted
  // documents, but they stay optional here so literal 1.x presets remain valid.
  v2_recovery_min_pct?: string | number;
  v2_second_pullback_min_pct?: string | number;
  v2_minimum_l1_to_b1_minutes?: number;
  v2_maximum_l2_to_signal_minutes?: number;
  v2_minimum_breakout_volume_ratio?: string | number;
  v2_profit_protection_trigger_r?: string | number | null;
  v2_protected_stop_r?: string | number;
  v2_max_hold_minutes?: number;
  stop_buffer_bps: string | number;
  reward_multiple: string | number;
  exit_rsi_period: number;
  exit_rsi_threshold: string | number;
  entry_start_et: string;
  last_entry_et: string;
  intraday_learning_enabled?: boolean;
  stoch_trend_capture_enabled?: boolean;
  intraday_llm_enabled?: boolean;
  intraday_llm_top_n?: number;
  intraday_llm_interval_minutes?: number;
  /** Which task runs the configuration; never what it may trade. */
  execution_owner?: 'monitor' | 'runner_shadow' | 'runner';
};

export type StochRsi5mConfig = {
  strategy_id: 'stoch_rsi_5m_v1';
  strategy_version: '1.0.0';
  universe_scan_time_et?: string;
  universe_discovery_source?: 'yahoo' | 'finviz';
  auto_archive_daily_universe?: boolean;
  universe_archive_grace_minutes?: number;
  universe_discovery_count?: number;
  minimum_gap_pct: string | number;
  minimum_price: string | number;
  maximum_price: string | number;
  minimum_premarket_dollar_volume: string | number;
  minimum_tod_rvol: string | number;
  allow_missing_tod_rvol: boolean;
  maximum_spread_bps: string | number;
  preferred_float_min_shares: string | number;
  preferred_float_max_shares: string | number;
  float_preference_mode: FloatPreferenceMode;
  require_catalyst_evidence: boolean;
  reject_dilution_flags: string[];
  trade_selection?: 'sequential' | 'early_single';
  oversold_threshold: string | number;
  recovery_threshold: string | number;
  overbought_threshold: string | number;
  rsi_period: number;
  stochastic_period: number;
  k_smoothing_period: number;
  d_smoothing_period: number;
  entry_start_et: string;
  last_entry_et: string;
  force_flat_et?: string;
};

export type StrategyRiskProfile = {
  risk_per_trade_pct: string | number;
  max_daily_loss_pct: string | number;
  max_open_risk_pct: string | number;
  max_positions: number;
  max_trades_per_day: number;
  max_trade_value: string | number;
  one_trade_per_symbol_per_day: boolean;
  max_spread_bps: string | number;
  entry_start_et: string;
  last_entry_et: string;
  force_flat_et: string;
  kill_switch: boolean;
};

type TradingStrategyDocumentBase = {
  strategy_id: string;
  parent_strategy_id?: string | null;
  account_id: string;
  mode: StrategyMode;
  active_universe_id: string | null;
  risk: StrategyRiskProfile;
  enabled: boolean;
  archived_at?: string | null;
  archived_reason?: string | null;
  revision: number;
  created_at?: string | null;
  updated_at?: string | null;
};

export type GapPullbackTradingStrategyConfig = TradingStrategyDocumentBase & {
  strategy_kind: 'gap_pullback_v1';
  strategy_version: string;
  config: GapPullbackConfig;
};

export type StochRsi5mTradingStrategyConfig = TradingStrategyDocumentBase & {
  strategy_kind: 'stoch_rsi_5m_v1';
  strategy_version: '1.0.0';
  config: StochRsi5mConfig;
};

export type OvernightShadowConfig = components['schemas']['OvernightShadowConfig-Output'];

/** SHADOW-only overnight research group; the server provisions it and the UI shows it read-only. */
export type OvernightShadowTradingStrategyConfig = TradingStrategyDocumentBase & {
  strategy_kind: 'overnight_shadow_v1';
  strategy_version: '1.0.0';
  config: OvernightShadowConfig;
};

export type TradingStrategyConfig =
  | GapPullbackTradingStrategyConfig
  | StochRsi5mTradingStrategyConfig
  | OvernightShadowTradingStrategyConfig;

export type GapperCandidate = {
  instrument_id: string;
  binding_id?: string | null;
  observed_at?: string | null;
  evidence_observed_at?: Record<string, string>;
  previous_close: string | number;
  raw_previous_close?: string | number | null;
  split_adjustment_factor?: string | number;
  corporate_action_evidence_ids?: string[];
  premarket_price: string | number;
  gap_pct: string | number;
  premarket_volume?: string | number;
  premarket_dollar_volume?: string | number;
  premarket_bar_count?: number | null;
  tod_rvol?: string | number | null;
  market_data_complete?: boolean;
  data_quality_flags?: string[];
  market_cap?: string | number | null;
  float_shares?: string | number | null;
  spread_bps?: string | number | null;
  catalyst_evidence_ids?: string[];
  dilution_flags?: string[];
  discovery_rank?: number | null;
};

export type GapperUniverse = {
  universe_id: string;
  session_date: string;
  evaluation_time: string;
  discovery_source: 'manual' | 'import' | 'scanner' | 'provider' | 'finviz';
  source_locator?: string | null;
  source_candidate_symbols?: string[];
  candidates: GapperCandidate[];
  source_fingerprint: string;
};

export type GapperUniverseFreezeInput = Omit<GapperUniverse, 'source_fingerprint'>;

export type YahooGapperDiscoveryInput = {
  universe_id: string;
  evaluation_time: string;
  count: number;
  minimum_gap_pct: string | number;
  minimum_price: string | number;
  maximum_price: string | number;
};

export type FinvizGapperDiscoveryInput = YahooGapperDiscoveryInput;

export type StrategyEvent = components['schemas']['StrategyEvent'];

export type StrategyProtection = components['schemas']['StrategyProtection'];

export type V2QualificationThresholds = components['schemas']['V2QualificationThresholds'];

export type V2ProspectiveQualification = components['schemas']['V2ProspectiveQualification'];

export type ProspectiveEconomicMetrics = components['schemas']['ProspectiveEconomicMetrics'];

export type ProspectiveEconomicThresholds = components['schemas']['ProspectiveEconomicThresholds'];

export type ProspectiveEconomicStatus = components['schemas']['ProspectiveEconomicStatus'];

export type ProspectiveEconomicHoldoutReviewInput = {
  trade_count: number;
  win_rate: string | number;
  expectancy_r: string | number;
  one_sided_90_lcb_r?: string | number | null;
  max_drawdown_r: string | number;
  artifact_ref: string;
  review_note: string;
};

export type CatalystShadowClassification = components['schemas']['CatalystShadowClassification'];

export type StrategyResearchReview = components['schemas']['StrategyResearchReview'];

export type StrategyResearchReviewResponse = components['schemas']['StrategyResearchReviewResponse'];

export type StrategyCatalystCaptureResponse = components['schemas']['StrategyCatalystCaptureResponse'];

export type GapPullbackBacktestTrade = components['schemas']['GapPullbackBacktestTrade'];

export type GapPullbackBacktestSummary = components['schemas']['GapPullbackBacktestSummary'];

export type GapPullbackBacktestResult = components['schemas']['GapPullbackBacktestResult'];

export type StrategyRangeBacktestInput = {
  start_date: string;
  end_date: string;
  initial_cash: string | number;
  assumed_spread_bps: string | number;
  max_hold_minutes: number;
  universe_scan_time_et?: string | null;
  universe_cutoff_et?: string | null;
  universe_mode?: HistoricalUniverseMode;
  reconstruction_max_age_days?: number;
  max_sessions?: number;
};

export type StrategyRangeBacktestDay = components['schemas']['StrategyRangeBacktestDay'];

export type StrategyRangeBacktestResult = components['schemas']['StrategyRangeBacktestResult'];

export type StrategyRangeBacktestAccepted = {
  run_id: string;
  status: 'queued';
  total_sessions: number;
};

export type StrategyRangeBacktestProgress = {
  run_id: string;
  strategy_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  completed_sessions: number;
  total_sessions: number;
  percent: number;
  current_session: string | null;
  error: string | null;
  result: StrategyRangeBacktestResult | null;
};
