/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { tradingPaperApi } from './tradingPaperApi';
import { tradingHermesResearchApi } from './tradingHermesResearchApi';
import { OvernightShadowSummary } from './OvernightShadowSummary';
import { TradingStrategyBacktest } from './TradingStrategyBacktest';
import { TradingStrategyExecutionCredentials } from './TradingStrategyExecutionCredentials';
import { TRADING_STRATEGY_DEFINITIONS } from './tradingStrategyCatalog';
import { tradingStrategyApi } from './tradingStrategyApi';
import type {
  GapperCandidate,
  GapperUniverse,
  GapperUniverseFreezeInput,
  GapPullbackConfig,
  GapPullbackTradingStrategyConfig,
  StrategyEvent,
  StrategyMode,
  StrategyProtection,
  StrategyResearchReview,
  StochRsi5mConfig,
  StochRsi5mTradingStrategyConfig,
  TradingStrategyConfig,
  V2ProspectiveQualification,
} from './tradingStrategyTypes';
import './TradingStrategiesPanel.css';
import './TradingStrategyEnhancements.css';

const definition = TRADING_STRATEGY_DEFINITIONS.gap_pullback_v1;
const STRICT_DILUTION_FLAGS = ['registered_offering', 'atm', 'warrants', 'convertible', 'equity_line'];
const INTERDAY_TRADING_STRATEGY_ID = 'interday-trading-strategy-shadow';
const INTERDAY_SUBSTRATEGIES = [
  { key: 'deterministic-v2', label: 'A · Deterministic V2', description: 'Gap-as-impulse failed-selloff evaluator', strategyId: null },
  { key: 'stoch-trend-capture', label: 'B · Stoch trend capture', description: 'Embedded 3-minute trend-capture arm', strategyId: null },
  { key: 'ai-every-minute', label: 'C · AI every minute', description: 'Embedded stateful AI shadow policy', strategyId: null },
  { key: 'ai-event-driven', label: 'D · AI event-driven', description: 'Embedded event-driven AI shadow policy', strategyId: null },
  { key: 'stoch-rsi-5min', label: 'E · 5m Stoch RSI', description: 'Linked deterministic child strategy', strategyId: 'stoch-rsi-5min' },
  { key: 'stoch-rsi-5min-early-single', label: 'F · 5m Stoch RSI early-single', description: 'Linked child: first trade per symbol per session only', strategyId: 'stoch-rsi-5min-early-single' },
  { key: 'gap-pullback-v2-prospective-20260825', label: 'G · Gap pullback V2', description: 'Linked deterministic prospective child strategy', strategyId: 'gap-pullback-v2-prospective-20260825' },
] as const;

const strictV11Config = (): GapPullbackConfig => ({
  strategy_id: 'gap_pullback_v1',
  strategy_version: '1.1.0',
  structure_interval: '5m',
  execution_interval: '1m',
  universe_scan_time_et: '09:20:00',
  universe_discovery_source: 'finviz',
  auto_archive_daily_universe: true,
  universe_archive_grace_minutes: 10,
  universe_discovery_count: 20,
  minimum_gap_pct: '20',
  minimum_price: '0.50',
  maximum_price: '20',
  minimum_premarket_dollar_volume: '10000000',
  minimum_tod_rvol: '5',
  allow_missing_tod_rvol: false,
  maximum_spread_bps: '150',
  preferred_float_min_shares: '2000000',
  preferred_float_max_shares: '30000000',
  float_preference_mode: 'score',
  require_catalyst_evidence: true,
  reject_dilution_flags: STRICT_DILUTION_FLAGS,
  opening_impulse_min_pct: '8',
  pullback_min_pct: '15',
  pullback_max_pct: '55',
  pullback_volume_max_ratio: '0.70',
  higher_low_buffer_bps: '20',
  breakout_volume_ratio: '1.25',
  pivot_left_bars: 2,
  pivot_right_bars: 2,
  volume_lookback_bars: 10,
  require_breakout_hold: true,
  breakout_hold_bars: 1,
  breakout_hold_tolerance_bps: '25',
  minimum_quality_score: 7,
  stop_buffer_bps: '15',
  reward_multiple: '2',
  exit_rsi_period: 14,
  exit_rsi_threshold: '50',
  entry_start_et: '09:35:00',
  last_entry_et: '11:30:00',
  intraday_learning_enabled: true,
  stoch_trend_capture_enabled: false,
  intraday_llm_enabled: true,
  intraday_llm_top_n: 5,
  intraday_llm_interval_minutes: 10,
});

const frozenV2Config = (): GapPullbackConfig => ({
  strategy_id: 'gap_pullback_v1',
  strategy_version: '2.0.0',
  structure_interval: '1m',
  execution_interval: '1m',
  universe_scan_time_et: '09:20:00',
  universe_discovery_source: 'yahoo',
  auto_archive_daily_universe: true,
  universe_archive_grace_minutes: 10,
  universe_discovery_count: 50,
  minimum_gap_pct: '20',
  minimum_price: '0.50',
  maximum_price: '20',
  minimum_premarket_dollar_volume: '100000',
  minimum_tod_rvol: '3',
  allow_missing_tod_rvol: false,
  maximum_spread_bps: '150',
  preferred_float_min_shares: '2000000',
  preferred_float_max_shares: '30000000',
  float_preference_mode: 'ignore',
  // The V11 reconstructed evidence did not contain point-in-time catalyst or
  // dilution data. Keep those visible for research, but do not pretend the
  // historical edge validated them as deterministic execution gates.
  require_catalyst_evidence: false,
  reject_dilution_flags: [],
  opening_impulse_min_pct: '0',
  pullback_min_pct: '8',
  pullback_max_pct: '25',
  pullback_volume_max_ratio: '5',
  higher_low_buffer_bps: '50',
  breakout_volume_ratio: '1.25',
  pivot_left_bars: 1,
  pivot_right_bars: 1,
  volume_lookback_bars: 5,
  require_breakout_hold: false,
  breakout_hold_bars: 1,
  breakout_hold_tolerance_bps: '25',
  minimum_quality_score: 0,
  v2_recovery_min_pct: '5',
  v2_second_pullback_min_pct: '2',
  v2_minimum_l1_to_b1_minutes: 4,
  v2_maximum_l2_to_signal_minutes: 8,
  v2_minimum_breakout_volume_ratio: '0',
  v2_profit_protection_trigger_r: '0.75',
  v2_protected_stop_r: '0.25',
  v2_max_hold_minutes: 60,
  stop_buffer_bps: '15',
  reward_multiple: '1.5',
  exit_rsi_period: 14,
  exit_rsi_threshold: '50',
  entry_start_et: '09:35:00',
  last_entry_et: '11:30:00',
  intraday_learning_enabled: false,
  stoch_trend_capture_enabled: false,
  intraday_llm_enabled: false,
  intraday_llm_top_n: 5,
  intraday_llm_interval_minutes: 10,
});

const finvizLearningV2Config = (): GapPullbackConfig => ({
  ...frozenV2Config(),
  universe_scan_time_et: '09:15:00',
  universe_discovery_source: 'finviz',
  universe_discovery_count: 5,
  intraday_learning_enabled: true,
  stoch_trend_capture_enabled: true,
  intraday_llm_enabled: true,
  intraday_llm_top_n: 5,
  intraday_llm_interval_minutes: 10,
});

const defaultStrategy = (accountId: string): GapPullbackTradingStrategyConfig => ({
  strategy_id: `gap-pullback-${Date.now()}`,
  account_id: accountId,
  strategy_kind: 'gap_pullback_v1',
  strategy_version: '1.1.0',
  mode: 'shadow',
  active_universe_id: null,
  enabled: true,
  revision: 1,
  config: strictV11Config(),
  risk: {
    risk_per_trade_pct: '0.35',
    max_daily_loss_pct: '1.5',
    max_open_risk_pct: '1',
    max_positions: 3,
    max_trades_per_day: 5,
    max_trade_value: '25000',
    one_trade_per_symbol_per_day: true,
    max_spread_bps: '150',
    entry_start_et: '09:35:00',
    last_entry_et: '11:30:00',
    force_flat_et: '15:55:00',
    kill_switch: false,
  },
});

const defaultStochRsi5mStrategy = (accountId: string): StochRsi5mTradingStrategyConfig => ({
  strategy_id: `stoch-rsi-5min-${Date.now()}`,
  account_id: accountId,
  strategy_kind: 'stoch_rsi_5m_v1',
  strategy_version: '1.0.0',
  mode: 'shadow',
  active_universe_id: null,
  enabled: true,
  revision: 1,
  config: defaultStochRsi5mConfig(),
  risk: {
    risk_per_trade_pct: '0.35',
    max_daily_loss_pct: '1.5',
    max_open_risk_pct: '1',
    max_positions: 3,
    max_trades_per_day: 5,
    max_trade_value: '25000',
    one_trade_per_symbol_per_day: true,
    max_spread_bps: '150',
    entry_start_et: '09:35:00',
    last_entry_et: '15:50:00',
    force_flat_et: '15:55:00',
    kill_switch: false,
  },
});

/** A preset changes the strategy, not which task runs it: the preset with the current execution owner. */
function keepingOwner<T extends GapPullbackConfig>(preset: T, current: GapPullbackConfig): T {
  return { ...preset, execution_owner: current.execution_owner };
}

function eventTone(event: StrategyEvent): string {
  if (event.event_type === 'rejection' || event.state === 'rejected' || event.state === 'vetoed' || event.state === 'research_error') return 'rejected';
  if (event.event_type === 'stoch_trend_capture_entry' && eventStochEntryAllowed(event) === false) return 'rejected';
  if (event.event_type === 'entry_order_submitted' || event.state === 'entry_ready' || event.state === 'research_reviewed') return 'ready';
  return 'working';
}

function universeImport(raw: string, fallbackUniverseId: string | null): GapperUniverseFreezeInput {
  const parsed: unknown = JSON.parse(raw);
  const object: Record<string, unknown> | null = Array.isArray(parsed)
    ? { candidates: parsed }
    : parsed && typeof parsed === 'object'
      ? parsed as Record<string, unknown>
      : null;
  if (!object || !Array.isArray(object.candidates) || object.candidates.length === 0) {
    throw new Error('Universe JSON must be a candidate array or an object with a non-empty candidates array.');
  }
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const requestedId = typeof object.universe_id === 'string' && object.universe_id.trim()
    ? object.universe_id.trim()
    : fallbackUniverseId?.trim() || `gappers-${today}`;
  const discovery = typeof object.discovery_source === 'string' ? object.discovery_source : 'import';
  if (!['manual', 'import', 'scanner', 'provider', 'finviz'].includes(discovery)) {
    throw new Error('discovery_source must be manual, import, scanner, provider, or finviz.');
  }
  return {
    universe_id: requestedId,
    session_date: typeof object.session_date === 'string' ? object.session_date : today,
    evaluation_time: typeof object.evaluation_time === 'string' ? object.evaluation_time : now.toISOString(),
    discovery_source: discovery as GapperUniverseFreezeInput['discovery_source'],
    source_locator: typeof object.source_locator === 'string' ? object.source_locator : null,
    source_candidate_symbols: Array.isArray(object.source_candidate_symbols)
      ? object.source_candidate_symbols.map((item) => String(item))
      : [],
    candidates: object.candidates as GapperCandidate[],
  };
}

function numberValue(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compact(value: string | number | null | undefined): string {
  const parsed = numberValue(value);
  return parsed === null ? '—' : Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(parsed);
}

function percent(value: string | number | null | undefined): string {
  const parsed = numberValue(value);
  return parsed === null ? '—' : `${parsed.toFixed(parsed >= 10 ? 1 : 2)}%`;
}

/** Which editor a configuration opens in; the SHADOW overnight group is read-only (no draft). */
function editorDrafts(config: TradingStrategyConfig | null): {
  draft: GapPullbackTradingStrategyConfig | null;
  stochDraft: StochRsi5mTradingStrategyConfig | null;
} {
  if (config?.strategy_kind === 'stoch_rsi_5m_v1') return { draft: null, stochDraft: structuredClone(config) };
  if (config?.strategy_kind === 'overnight_shadow_v1') return { draft: null, stochDraft: null };
  return { draft: config ? structuredClone(config) : null, stochDraft: null };
}

function latestByEventType(events: StrategyEvent[], eventType: string): Map<string, StrategyEvent> {
  const output = new Map<string, StrategyEvent>();
  for (const event of events) {
    if (event.event_type !== eventType || output.has(event.instrument_id)) continue;
    output.set(event.instrument_id, event);
  }
  return output;
}

function latestByEventTypeAndPolicy(events: StrategyEvent[], eventType: string, policy: string): Map<string, StrategyEvent> {
  const output = new Map<string, StrategyEvent>();
  for (const event of events) {
    if (event.event_type !== eventType || event.payload?.policy !== policy || output.has(event.instrument_id)) continue;
    output.set(event.instrument_id, event);
  }
  return output;
}

function aiShadowDecision(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.decision;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function aiShadowTrade(event: StrategyEvent | undefined): Record<string, unknown> | null {
  return event?.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : null;
}

function recordObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function eventLearning(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.learning;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function learningScore(learning: Record<string, unknown> | null, key: string): number | null {
  if (!learning) return null;
  const value = Number(learning[key]);
  return Number.isFinite(value) ? value : null;
}

function eventStochTrend(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.policy;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function eventStochEntryAllowed(event: StrategyEvent | undefined): boolean | null {
  const raw = event?.payload?.risk_decision;
  if (!raw || typeof raw !== 'object') return null;
  const allowed = (raw as Record<string, unknown>).allowed;
  return typeof allowed === 'boolean' ? allowed : null;
}

function eventStochExecutionSummary(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.policy;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function eventIntradayLlm(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.assessment;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function intradayLlmProbability(assessment: Record<string, unknown> | null, key: string): number | null {
  if (!assessment) return null;
  const value = Number(assessment[key]);
  return Number.isFinite(value) ? value : null;
}

function eventQuality(event: StrategyEvent | undefined): number | null {
  const rawFeatures = event?.payload?.features;
  if (!rawFeatures || typeof rawFeatures !== 'object') return null;
  const score = Number((rawFeatures as Record<string, unknown>).quality_score);
  return Number.isFinite(score) ? score : null;
}

function eventClassification(event: StrategyEvent | undefined): Record<string, unknown> | null {
  const raw = event?.payload?.classification;
  return raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
}

function ConfigNumber({
  label,
  value,
  suffix,
  step = 'any',
  onChange,
}: {
  label: string;
  value: string | number;
  suffix?: string;
  step?: string;
  onChange: (next: string) => void;
}) {
  return (
    <label>
      <span>{label}{suffix ? <small>{suffix}</small> : null}</span>
      <input type="number" step={step} value={String(value)} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

const defaultStochRsi5mConfig = (): StochRsi5mConfig => ({
  strategy_id: 'stoch_rsi_5m_v1',
  strategy_version: '1.0.0',
  universe_scan_time_et: '09:20:00',
  universe_discovery_source: 'yahoo',
  auto_archive_daily_universe: true,
  universe_archive_grace_minutes: 10,
  universe_discovery_count: 50,
  minimum_gap_pct: '0',
  minimum_price: '0.50',
  maximum_price: '20',
  minimum_premarket_dollar_volume: '0',
  minimum_tod_rvol: '0',
  allow_missing_tod_rvol: true,
  maximum_spread_bps: '150',
  preferred_float_min_shares: '1',
  preferred_float_max_shares: '1000000000',
  float_preference_mode: 'ignore',
  require_catalyst_evidence: false,
  reject_dilution_flags: [],
  trade_selection: 'sequential',
  oversold_threshold: '18',
  recovery_threshold: '20',
  overbought_threshold: '95',
  rsi_period: 14,
  stochastic_period: 14,
  k_smoothing_period: 3,
  d_smoothing_period: 3,
  entry_start_et: '09:35:00',
  last_entry_et: '15:50:00',
  force_flat_et: '15:55:00',
});

function StochRsi5mEditor({
  draft,
  events,
  notice,
  status,
  onChange,
  onSave,
  onRefresh,
  onArchive,
}: {
  draft: StochRsi5mTradingStrategyConfig;
  events: StrategyEvent[];
  notice: string | null;
  status: 'loading' | 'ready' | 'saving' | 'error';
  onChange: (next: StochRsi5mTradingStrategyConfig) => void;
  onSave: () => void;
  onRefresh: () => void;
  onArchive: () => void;
}) {
  const setConfig = <K extends keyof StochRsi5mConfig>(key: K, value: StochRsi5mConfig[K]) => {
    onChange({ ...draft, config: { ...draft.config, [key]: value } });
  };
  const setRisk = (key: keyof StochRsi5mTradingStrategyConfig['risk'], value: string | number | boolean) => {
    onChange({ ...draft, risk: { ...draft.risk, [key]: value } });
  };
  const stochEvents = events.filter((event) => event.event_type === 'stoch_rsi_5m');

  return (
    <>
      <header className="trading-strategy-editor-header">
        <div><strong>{draft.strategy_id}</strong><small>5m Stoch RSI Cross · config v{draft.config.strategy_version} · finalized 5m bars · shadow only</small></div>
        <div className="trading-strategy-header-actions">
          <button type="button" onClick={onRefresh}>Refresh</button>
          {draft.archived_at ? null : <button type="button" className="danger" onClick={onArchive}>Archive</button>}
          <button type="button" className="primary" onClick={onSave} disabled={status === 'saving' || Boolean(draft.archived_at)}>{status === 'saving' ? 'Saving…' : draft.archived_at ? 'Archived' : 'Save strategy'}</button>
        </div>
      </header>
      {draft.archived_at ? <div className="trading-strategy-notice" role="status">Archived {new Date(draft.archived_at).toLocaleString()}. This strategy is read-only.</div> : null}
      {notice ? <div className="trading-strategy-notice" role="status">{notice}</div> : null}
      <section className="trading-strategy-overview">
        <div><strong>Arm below 18, confirm %K above %D, then buy after rising through 20; sell on a %K/%D cross down below 80 or above 95.</strong><small>Non-bullish confirmation candles require a later close above their high; bullish confirmation candles use the next five-minute bar open. The actual entry open must be strictly above the 5-period EMA calculated from finalized five-minute closes. Open positions exit after a completed five-minute close below that EMA or a bearish Stoch RSI cross. After an exit, a fresh setup may produce another sequential trade. Signals use completed candles and research fills use the next five-minute bar open. There is no AUTO PAPER or live broker path.</small></div>
        <div className="trading-mode-switch" role="group" aria-label="Strategy mode">
          {(['off', 'shadow'] as StrategyMode[]).map((mode) => <button type="button" key={mode} className={draft.mode === mode ? 'active' : undefined} aria-pressed={draft.mode === mode} onClick={() => onChange({ ...draft, mode })}>{mode[0].toUpperCase() + mode.slice(1)}</button>)}
        </div>
      </section>
      <div className="trading-strategy-config">
        <div className="trading-config-block">
          <header><strong>Strategy rule</strong><small>Deterministic Stoch RSI %K/%D crossing thresholds</small></header>
          <div className="trading-strategy-grid">
            <label><span>Strategy type</span><input value="stoch-rsi-5min" readOnly /></label>
            <label className="toggle-field"><span>Enabled</span><input type="checkbox" checked={draft.enabled} onChange={(event) => onChange({ ...draft, enabled: event.target.checked })} /></label>
            <label><span>Trades per symbol</span><select value={draft.config.trade_selection ?? 'sequential'} onChange={(event) => setConfig('trade_selection', event.target.value as NonNullable<StochRsi5mConfig['trade_selection']>)}><option value="sequential">Sequential</option><option value="early_single">First per session (early-single)</option></select></label>
            <ConfigNumber label="Oversold threshold" suffix="%K &lt;" step="0.1" value={draft.config.oversold_threshold} onChange={(value) => setConfig('oversold_threshold', value)} />
            <ConfigNumber label="Recovery confirmation" suffix="%K ≥" step="0.1" value={draft.config.recovery_threshold ?? '20'} onChange={(value) => setConfig('recovery_threshold', value)} />
            <ConfigNumber label="Overbought threshold" suffix="%K &gt;" step="0.1" value={draft.config.overbought_threshold} onChange={(value) => setConfig('overbought_threshold', value)} />
            <label><span>RSI period</span><input type="number" min="2" max="100" value={draft.config.rsi_period} onChange={(event) => setConfig('rsi_period', Number(event.target.value))} /></label>
            <label><span>Stochastic period</span><input type="number" min="2" max="100" value={draft.config.stochastic_period} onChange={(event) => setConfig('stochastic_period', Number(event.target.value))} /></label>
            <label><span>%K smoothing</span><input type="number" min="1" max="20" value={draft.config.k_smoothing_period} onChange={(event) => setConfig('k_smoothing_period', Number(event.target.value))} /></label>
            <label><span>%D smoothing</span><input type="number" min="1" max="20" value={draft.config.d_smoothing_period} onChange={(event) => setConfig('d_smoothing_period', Number(event.target.value))} /></label>
            <label><span>Entry starts ET</span><input type="time" step="1" value={draft.config.entry_start_et} onChange={(event) => setConfig('entry_start_et', event.target.value)} /></label>
            <label><span>Last entry ET</span><input type="time" step="1" value={draft.config.last_entry_et} onChange={(event) => setConfig('last_entry_et', event.target.value)} /></label>
            <label><span>Force flat ET</span><input type="time" step="1" value={draft.config.force_flat_et ?? '15:55:00'} onChange={(event) => setConfig('force_flat_et', event.target.value)} /></label>
          </div>
        </div>
        <div className="trading-config-block">
          <header><strong>Universe archive</strong><small>Evidence cohort only; Alpaca is not used for discovery or execution</small></header>
          <div className="trading-strategy-grid">
            <label><span>Scan time ET</span><input type="time" step="60" value={draft.config.universe_scan_time_et ?? '09:20:00'} onChange={(event) => setConfig('universe_scan_time_et', event.target.value)} /></label>
            <label><span>Discovery source</span><select value={draft.config.universe_discovery_source ?? 'yahoo'} onChange={(event) => setConfig('universe_discovery_source', event.target.value as StochRsi5mConfig['universe_discovery_source'])}><option value="yahoo">Yahoo</option><option value="finviz">Finviz</option></select></label>
            <label><span>Candidate count</span><input type="number" min="1" max="100" value={draft.config.universe_discovery_count ?? 50} onChange={(event) => setConfig('universe_discovery_count', Number(event.target.value))} /></label>
            <ConfigNumber label="Minimum price" suffix="$" step="0.01" value={draft.config.minimum_price} onChange={(value) => setConfig('minimum_price', value)} />
            <ConfigNumber label="Maximum price" suffix="$" step="0.01" value={draft.config.maximum_price} onChange={(value) => setConfig('maximum_price', value)} />
            <label className="toggle-field"><span>Auto-archive daily universe</span><input type="checkbox" checked={draft.config.auto_archive_daily_universe ?? true} onChange={(event) => setConfig('auto_archive_daily_universe', event.target.checked)} /></label>
          </div>
        </div>
        <div className="trading-config-block">
          <header><strong>Research risk profile</strong><small>Displayed for consistent account controls; this strategy cannot place orders</small></header>
          <div className="trading-strategy-grid">
            <ConfigNumber label="Maximum positions" value={draft.risk.max_positions} onChange={(value) => setRisk('max_positions', Number(value))} />
            <ConfigNumber label="Maximum trade value" suffix="$" step="1000" value={draft.risk.max_trade_value} onChange={(value) => setRisk('max_trade_value', value)} />
            <label className="toggle-field danger"><span>Kill switch</span><input type="checkbox" checked={draft.risk.kill_switch} onChange={(event) => setRisk('kill_switch', event.target.checked)} /></label>
          </div>
        </div>
        <section className="trading-config-block">
          <header><strong>Recent 5m Stoch RSI evidence</strong><small>{stochEvents.length} recorded event{stochEvents.length === 1 ? '' : 's'}</small></header>
          {stochEvents.length ? <div className="trading-events-table"><table><thead><tr><th>Symbol</th><th>State</th><th>Reason</th><th>Observed</th></tr></thead><tbody>{stochEvents.map((event) => <tr key={event.event_id}><td>{event.instrument_id.split(':').at(-1) ?? event.instrument_id}</td><td>{event.state.replaceAll('_', ' ')}</td><td>{event.reason_code ?? '—'}</td><td>{new Date(event.observed_at).toLocaleString()}</td></tr>)}</tbody></table></div> : <p>No Stoch RSI evidence has been recorded yet. Save the strategy, then let the shadow monitor evaluate the next completed bars.</p>}
        </section>
      </div>
    </>
  );
}

export function TradingStrategiesPanel() {
  const [strategies, setStrategies] = useState<TradingStrategyConfig[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [draft, setDraft] = useState<GapPullbackTradingStrategyConfig | null>(null);
  const [stochDraft, setStochDraft] = useState<StochRsi5mTradingStrategyConfig | null>(null);
  const [accounts, setAccounts] = useState<Array<{ account_id: string; name: string }>>([]);
  const [events, setEvents] = useState<StrategyEvent[]>([]);
  const [protections, setProtections] = useState<StrategyProtection[]>([]);
  const [universe, setUniverse] = useState<GapperUniverse | null>(null);
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(new Set());
  const [researchReviews, setResearchReviews] = useState<StrategyResearchReview[]>([]);
  const [universeJson, setUniverseJson] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [discovering, setDiscovering] = useState(false);
  const [capturingEvidence, setCapturingEvidence] = useState(false);
  const [freezing, setFreezing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [htrPromotionAllowed, setHtrPromotionAllowed] = useState(false);
  const [v2Qualification, setV2Qualification] = useState<V2ProspectiveQualification | null>(null);
  const [v2ReviewNote, setV2ReviewNote] = useState('');
  const [v2Reviewing, setV2Reviewing] = useState(false);

  const selected = useMemo(
    () => strategies.find((item) => item.strategy_id === selectedId) ?? null,
    [selectedId, strategies],
  );
  const selectedGap = selected?.strategy_kind === 'gap_pullback_v1' ? selected : null;
  const latestDeterministic = useMemo(() => latestByEventType(events, 'state'), [events]);
  const latestResearch = useMemo(() => latestByEventType(events, 'research_llm'), [events]);
  const latestLearning = useMemo(() => latestByEventType(events, 'intraday_learning'), [events]);
  const latestStochTrend = useMemo(() => latestByEventType(events, 'stoch_trend_capture'), [events]);
  const latestStochTrendEntry = useMemo(() => latestByEventType(events, 'stoch_trend_capture_entry'), [events]);
  const latestStochExecutionSummary = useMemo(() => latestByEventType(events, 'stoch_trend_execution_summary'), [events]);
  const latestIntradayLlm = useMemo(() => latestByEventType(events, 'intraday_llm'), [events]);
  const latestAiMinuteDecision = useMemo(() => latestByEventTypeAndPolicy(events, 'ai_shadow_decision', 'minute'), [events]);
  const latestAiEventDecision = useMemo(() => latestByEventTypeAndPolicy(events, 'ai_shadow_decision', 'event'), [events]);
  const latestAiMinuteTrade = useMemo(() => latestByEventTypeAndPolicy(events, 'ai_shadow_trade', 'minute'), [events]);
  const latestAiEventTrade = useMemo(() => latestByEventTypeAndPolicy(events, 'ai_shadow_trade', 'event'), [events]);
  const latestFourArmComparison = useMemo(() => {
    const event = events.find((item) => item.event_type === 'shadow_strategy_comparison');
    const payload = recordObject(event?.payload);
    if (!payload) return null;
    return {
      observedAt: event?.observed_at ?? null,
      payload,
      dataQuality: recordObject(payload.shared_data_quality),
      armA: recordObject(payload.arm_a_deterministic_v2),
      armB: recordObject(payload.arm_b_stoch_trend),
      armC: recordObject(payload.arm_c_ai_every_minute),
      armD: recordObject(payload.arm_d_ai_event_driven),
    };
  }, [events]);

  const loadUniverse = async (universeId: string | null) => {
    if (!universeId) {
      setUniverse(null);
      setUniverseJson('');
      setSelectedCandidates(new Set());
      return;
    }
    const frozen = await tradingStrategyApi.universe(universeId);
    setUniverse(frozen);
    setUniverseJson(JSON.stringify(frozen, null, 2));
    setSelectedCandidates(new Set(frozen.candidates.map((candidate) => candidate.instrument_id)));
  };

  const refreshDetail = async (strategyId: string, universeId?: string | null) => {
    const [nextEvents, nextProtections] = await Promise.all([
      tradingStrategyApi.events(strategyId),
      tradingStrategyApi.protections(strategyId),
    ]);
    setEvents(nextEvents);
    setProtections(nextProtections);
    if (universeId !== undefined) await loadUniverse(universeId);
  };

  const refresh = async () => {
    setStatus('loading');
    try {
      const [nextStrategies, nextAccounts] = await Promise.all([
        tradingStrategyApi.list(),
        tradingPaperApi.accounts(),
      ]);
      setStrategies(nextStrategies);
      setAccounts(nextAccounts.map((account) => ({ account_id: account.account_id, name: account.name })));
      const nextId = nextStrategies.some((item) => item.strategy_id === selectedId)
        ? selectedId
        : nextStrategies[0]?.strategy_id ?? '';
      setSelectedId(nextId);
      const current = nextStrategies.find((item) => item.strategy_id === nextId) ?? null;
      const drafts = editorDrafts(current);
      setDraft(drafts.draft);
      setStochDraft(drafts.stochDraft);
      if (current) await refreshDetail(current.strategy_id, current.active_universe_id);
      else {
        setEvents([]);
        setProtections([]);
        setUniverse(null);
        setStochDraft(null);
      }
      setStatus('ready');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      setStatus('error');
    }
  };

  useEffect(() => { void refresh(); }, []);

  useEffect(() => {
    let alive = true;
    void tradingHermesResearchApi.validation().then((report) => {
      if (alive) setHtrPromotionAllowed(Boolean(report?.promotion_allowed));
    }).catch(() => { if (alive) setHtrPromotionAllowed(false); });
    return () => { alive = false; };
  }, [selected?.strategy_id, selected?.revision]);

  useEffect(() => {
    let alive = true;
    if (!selected || selected.config.strategy_version !== '2.0.0') {
      setV2Qualification(null);
      return () => { alive = false; };
    }
    void tradingStrategyApi.v2Qualification(selected.strategy_id).then((qualification) => {
      if (alive) setV2Qualification(qualification);
    }).catch((error) => {
      if (alive) {
        setV2Qualification(null);
        setNotice(error instanceof Error ? error.message : String(error));
      }
    });
    return () => { alive = false; };
  }, [selected?.strategy_id, selected?.revision, selected?.config.strategy_version]);

  useEffect(() => {
    if (!selected) return;
    const drafts = editorDrafts(selected);
    setDraft(drafts.draft);
    setStochDraft(drafts.stochDraft);
    setResearchReviews([]);
    void refreshDetail(selected.strategy_id, selected.active_universe_id).catch((error) => {
      setNotice(error instanceof Error ? error.message : String(error));
    });
  }, [selected?.strategy_id, selected?.revision]);

  const startNew = () => {
    if (!accounts.length) {
      setNotice('Create a paper account before configuring an automated strategy.');
      return;
    }
    setSelectedId('');
    setEvents([]);
    setProtections([]);
    setUniverse(null);
    setUniverseJson('');
    setResearchReviews([]);
    setV2Qualification(null);
    setV2ReviewNote('');
    setSelectedCandidates(new Set());
    setStochDraft(null);
    setDraft(defaultStrategy(accounts[0].account_id));
    setNotice(null);
  };

  const startNewStochRsi5m = () => {
    if (!accounts.length) {
      setNotice('Create a paper account before configuring a strategy.');
      return;
    }
    setSelectedId('');
    setEvents([]);
    setProtections([]);
    setUniverse(null);
    setUniverseJson('');
    setResearchReviews([]);
    setV2Qualification(null);
    setV2ReviewNote('');
    setSelectedCandidates(new Set());
    setDraft(null);
    setStochDraft(defaultStochRsi5mStrategy(accounts[0].account_id));
    setNotice(null);
  };

  const setConfig = <K extends keyof GapPullbackConfig>(key: K, value: GapPullbackConfig[K]) => {
    setDraft((current) => current ? { ...current, config: { ...current.config, [key]: value } } : current);
  };
  const setConfigNumber = (key: keyof GapPullbackConfig, value: string) => {
    setDraft((current) => current ? { ...current, config: { ...current.config, [key]: value } } : current);
  };

  const upgradeToStrictV11 = () => {
    if (!draft) return;
    setDraft({ ...draft, strategy_version: '1.1.0', config: keepingOwner(strictV11Config(), draft.config) });
    setNotice('Loaded the strict v1.1 failed-selloff baseline with 5-minute structure and 1-minute execution. Review every value, then save to persist it.');
  };

  const loadReviewedV12 = () => {
    if (!draft || !htrPromotionAllowed) return;
    const config = keepingOwner({ ...strictV11Config(), strategy_version: '1.2.0' as const }, draft.config);
    setDraft({ ...draft, strategy_version: '1.2.0', config });
    setNotice('Loaded gap_pullback_v1 1.2.0. Market-structure defaults remain the strict v1.1 baseline; only the reviewed trading-research-1 policy becomes authoritative. Review and save explicitly.');
  };

  const loadFrozenV2 = () => {
    if (!draft) return;
    setDraft({
      ...draft,
      strategy_version: '2.0.0',
      mode: 'shadow',
      active_universe_id: null,
      config: keepingOwner(frozenV2Config(), draft.config),
      risk: { ...draft.risk, entry_start_et: '09:35:00', last_entry_et: '11:30:00' },
    });
    setNotice('Loaded the frozen V11 / strategy 2.0 profile in SHADOW mode and cleared any selected universe so qualification uses the strategy-owned raw morning archive. Structure: 1m L1→B1→higher-L2, base ≥4m, L2 resolution ≤8m, 1.5R target, +0.75R→+0.25R causal protection, 60m max hold. Evidence is mixed: the 58-session revealed sample was positive, the April/May frozen block produced only two positive trades, and the older March/April stress block produced 5 trades at -0.546R expectancy. Keep 2.0 in prospective SHADOW until captured live evidence is reviewed; do not promote from historical reconstruction alone.');
  };

  const loadFinvizLearningV2 = () => {
    if (!draft) return;
    const config = keepingOwner(finvizLearningV2Config(), draft.config);
    setDraft({
      ...draft,
      strategy_version: '2.0.0',
      mode: 'shadow',
      active_universe_id: null,
      config,
      risk: { ...draft.risk, entry_start_et: '09:35:00', last_entry_et: '11:30:00' },
    });
    setNotice('Loaded the interday six-substrategy SHADOW group: four embedded Finviz arms plus the linked 5m Stoch RSI and Gap Pullback V2 child strategies. AI actions use normalized research units only; deterministic execution eligibility, spread/halt vetoes and force-flat remain outside model authority. Nothing in these overlays can authorize an order or inherit Yahoo V2 AUTO PAPER qualification.');
  };

  const reviewV2Qualification = async () => {
    if (!selected || selected.config.strategy_version !== '2.0.0') return;
    const note = v2ReviewNote.trim();
    if (note.length < 10) {
      setNotice('V2 prospective promotion review requires an audit note of at least 10 characters.');
      return;
    }
    setV2Reviewing(true);
    try {
      const qualification = await tradingStrategyApi.reviewV2Qualification(selected.strategy_id, note);
      setV2Qualification(qualification);
      setV2ReviewNote('');
      setNotice(qualification.auto_paper_authorized
        ? 'V2 prospective evidence review recorded. AUTO PAPER is now authorized for this exact evidence/profile snapshot; future evidence changes require a new review.'
        : 'V2 review recorded, but AUTO PAPER remains blocked by the server qualification policy.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setV2Reviewing(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    if (draft.archived_at) {
      setNotice('Archived strategies are read-only. Create a new strategy to resume execution.');
      return;
    }
    if (draft.config.strategy_version === '1.2.0' && !htrPromotionAllowed) {
      setNotice('Strategy 1.2 requires an active reviewed HTR-15 validation artifact. Run HTR-14 validation and explicit promotion review first.');
      return;
    }
    if (draft.config.strategy_version === '2.0.0' && draft.mode === 'auto_paper' && !v2Qualification?.auto_paper_authorized) {
      setNotice('Strategy 2.0 AUTO PAPER is fail-closed until the frozen prospective qualification floors pass and the exact evidence snapshot receives an explicit review.');
      return;
    }
    if (!draft.account_id) {
      setNotice('A paper account is required.');
      return;
    }
    if (draft.config.structure_interval === '1m' && draft.config.execution_interval === '5m') {
      setNotice('Execution resolution cannot be coarser than the structure timeframe.');
      return;
    }
    if (draft.mode === 'auto_paper' && !draft.active_universe_id) {
      setNotice('AUTO PAPER requires a frozen point-in-time universe.');
      return;
    }
    setStatus('saving');
    try {
      const exists = strategies.some((item) => item.strategy_id === draft.strategy_id);
      const saved = exists ? await tradingStrategyApi.update(draft) : await tradingStrategyApi.create(draft);
      if (saved.strategy_kind !== 'gap_pullback_v1') throw new Error('Unexpected strategy kind returned for gap-pullback save.');
      setStrategies((current) => [saved, ...current.filter((item) => item.strategy_id !== saved.strategy_id)]);
      setSelectedId(saved.strategy_id);
      setDraft(structuredClone(saved));
      setNotice(saved.mode === 'auto_paper'
        ? `AUTO PAPER saved. ${saved.config.structure_interval} structure + ${saved.config.execution_interval} execution, deterministic strategy, server risk and eligible execution data are required before any paper order.`
        : saved.mode === 'shadow'
          ? 'Shadow mode saved. Signals and research are visible, but no strategy order is submitted.'
          : 'Strategy is off.');
      await refreshDetail(saved.strategy_id, saved.active_universe_id);
      setStatus('ready');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      setStatus('error');
    }
  };

  const saveStochRsi5m = async () => {
    if (!stochDraft) return;
    if (stochDraft.archived_at) {
      setNotice('Archived strategies are read-only. Create a new strategy to resume research.');
      return;
    }
    if (stochDraft.mode === 'auto_paper') {
      setNotice('The 5m Stoch RSI strategy is shadow-only and cannot be saved as AUTO PAPER.');
      return;
    }
    setStatus('saving');
    try {
      const exists = strategies.some((item) => item.strategy_id === stochDraft.strategy_id);
      const saved = exists ? await tradingStrategyApi.update(stochDraft) : await tradingStrategyApi.create(stochDraft);
      setStrategies((current) => [saved, ...current.filter((item) => item.strategy_id !== saved.strategy_id)]);
      setSelectedId(saved.strategy_id);
      setStochDraft(structuredClone(saved as StochRsi5mTradingStrategyConfig));
      setNotice(saved.mode === 'shadow'
        ? '5m Stoch RSI shadow strategy saved. Signals are recorded, but no strategy order is submitted.'
        : 'Strategy is off.');
      await refreshDetail(saved.strategy_id, saved.active_universe_id);
      setStatus('ready');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      setStatus('error');
    }
  };

  const archiveStrategy = async () => {
    if (!draft || !strategies.some((item) => item.strategy_id === draft.strategy_id)) {
      setNotice('Save the strategy before archiving it.');
      return;
    }
    if (draft.archived_at) {
      setNotice('This strategy is already archived and read-only.');
      return;
    }
    if (!window.confirm(`Archive strategy ${draft.strategy_id}? Execution is disabled, while configs, events, runs, protections and analytics remain reviewable.`)) return;
    setStatus('saving');
    try {
      await tradingStrategyApi.delete(draft);
      await refresh();
      setNotice(`Archived ${draft.strategy_id}. Historical evidence remains available in the strategy dashboard.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      setStatus('error');
    }
  };

  const archiveStochRsi5m = async () => {
    if (!stochDraft || !strategies.some((item) => item.strategy_id === stochDraft.strategy_id)) {
      setNotice('Save the strategy before archiving it.');
      return;
    }
    if (stochDraft.archived_at) {
      setNotice('This strategy is already archived and read-only.');
      return;
    }
    if (!window.confirm(`Archive strategy ${stochDraft.strategy_id}? Its shadow evidence remains reviewable.`)) return;
    setStatus('saving');
    try {
      await tradingStrategyApi.delete(stochDraft);
      setStochDraft(null);
      await refresh();
      setNotice(`Archived ${stochDraft.strategy_id}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      setStatus('error');
    }
  };

  const discoverFinviz = async () => {
    if (!draft) return;
    setDiscovering(true);
    try {
      const now = new Date();
      const timestamp = now.toISOString();
      const frozen = await tradingStrategyApi.discoverFinvizUniverse({
        universe_id: `finviz-gappers-${timestamp.slice(0, 10)}-${timestamp.slice(11, 16).replace(':', '')}`,
        evaluation_time: timestamp,
        count: draft.config.universe_discovery_count ?? 50,
        minimum_gap_pct: draft.config.minimum_gap_pct,
        minimum_price: draft.config.minimum_price,
        maximum_price: draft.config.maximum_price,
      });
      setUniverse(frozen);
      setUniverseJson(JSON.stringify(frozen, null, 2));
      setSelectedCandidates(new Set(frozen.candidates.map((candidate) => candidate.instrument_id)));
      setDraft((current) => current ? {
        ...current,
        active_universe_id: frozen.universe_id,
        config: { ...current.config, universe_discovery_source: 'finviz' },
      } : current);
      setNotice(`Scan complete: ${frozen.candidates.length} candidates were enriched from the atomic Finviz first-page cohort for ${frozen.session_date}. Manual scans still need the catalyst-capture step; scheduled Finviz auto-archives capture Yahoo headline evidence automatically.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDiscovering(false);
    }
  };

  const discoverYahoo = async () => {
    if (!draft) return;
    setDiscovering(true);
    try {
      const now = new Date();
      const timestamp = now.toISOString();
      const frozen = await tradingStrategyApi.discoverYahooUniverse({
        universe_id: `yahoo-gappers-${timestamp.slice(0, 10)}-${timestamp.slice(11, 16).replace(':', '')}`,
        evaluation_time: timestamp,
        count: draft.config.universe_discovery_count ?? 50,
        minimum_gap_pct: draft.config.minimum_gap_pct,
        minimum_price: draft.config.minimum_price,
        maximum_price: draft.config.maximum_price,
      });
      setUniverse(frozen);
      setUniverseJson(JSON.stringify(frozen, null, 2));
      setSelectedCandidates(new Set(frozen.candidates.map((candidate) => candidate.instrument_id)));
      setDraft((current) => current ? { ...current, active_universe_id: frozen.universe_id, config: { ...current.config, universe_discovery_source: 'yahoo' } } : current);
      setNotice(`Scan complete: ${frozen.candidates.length} current Yahoo gapper candidates were frozen for ${frozen.session_date}. Save the strategy, then collect catalyst evidence.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDiscovering(false);
    }
  };

  const captureYahooEvidence = async () => {
    if (!draft || !strategies.some((item) => item.strategy_id === draft.strategy_id)) {
      setNotice('Save the strategy and scanned universe before collecting catalyst evidence.');
      return;
    }
    if (!draft.active_universe_id) {
      setNotice('Scan or attach a frozen universe before collecting catalyst evidence.');
      return;
    }
    if (draft.mode === 'auto_paper') {
      setNotice('Pause AUTO PAPER before changing the daily research universe.');
      return;
    }
    setCapturingEvidence(true);
    try {
      const response = await tradingStrategyApi.captureYahooResearch(draft.strategy_id);
      if (response.strategy.strategy_kind !== 'gap_pullback_v1') throw new Error('Unexpected strategy kind returned for catalyst capture.');
      // The contract types kind and config separately; the check above pairs them.
      const strategy = response.strategy as GapPullbackTradingStrategyConfig;
      setStrategies((current) => [strategy, ...current.filter((item) => item.strategy_id !== strategy.strategy_id)]);
      setDraft(structuredClone(strategy));
      setUniverse(response.universe);
      setUniverseJson(JSON.stringify(response.universe, null, 2));
      setSelectedCandidates(new Set(response.universe.candidates.map((candidate) => candidate.instrument_id)));
      setResearchReviews([]);
      const failed = Object.keys(response.errors).length;
      setNotice(`Research capture complete: ${response.evidence_count} timestamped Yahoo headlines across ${response.candidates_with_evidence}/${response.universe.candidates.length} candidates${failed ? ` · ${failed} provider errors` : ''}. Review evidence/supply flags, then run optional LLM research.`);
      await refreshDetail(response.strategy.strategy_id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setCapturingEvidence(false);
    }
  };

  const freezeEdited = async () => {
    if (!draft || !universeJson.trim()) return;
    setFreezing(true);
    try {
      const request = universeImport(universeJson, draft.active_universe_id);
      const frozen = await tradingStrategyApi.freezeUniverse({
        ...request,
        universe_id: `${request.universe_id}-research-${Date.now()}`,
        evaluation_time: new Date().toISOString(),
        discovery_source: 'import',
      });
      setUniverse(frozen);
      setUniverseJson(JSON.stringify(frozen, null, 2));
      setSelectedCandidates(new Set(frozen.candidates.map((candidate) => candidate.instrument_id)));
      setDraft((current) => current ? { ...current, active_universe_id: frozen.universe_id } : current);
      setNotice(`Research evidence snapshot frozen: ${frozen.universe_id}. Save the strategy to persist this attachment.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setFreezing(false);
    }
  };

  const freezeSelected = async () => {
    if (!draft || !universe) return;
    const candidates = universe.candidates.filter((candidate) => selectedCandidates.has(candidate.instrument_id));
    if (!candidates.length) {
      setNotice('Select at least one candidate to freeze the narrowed daily universe.');
      return;
    }
    setFreezing(true);
    try {
      const stamp = new Date();
      const frozen = await tradingStrategyApi.freezeUniverse({
        universe_id: `${universe.universe_id}-selected-${stamp.toISOString().slice(11, 16).replace(':', '')}`,
        session_date: universe.session_date,
        evaluation_time: stamp.toISOString(),
        discovery_source: 'import',
        candidates,
      });
      setUniverse(frozen);
      setUniverseJson(JSON.stringify(frozen, null, 2));
      setSelectedCandidates(new Set(frozen.candidates.map((candidate) => candidate.instrument_id)));
      setDraft((current) => current ? { ...current, active_universe_id: frozen.universe_id } : current);
      setNotice(`Daily selection frozen: ${frozen.candidates.length} candidates remain. Save the strategy to attach this final daily universe.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setFreezing(false);
    }
  };

  const runLlmReview = async () => {
    if (!draft || !strategies.some((item) => item.strategy_id === draft.strategy_id)) {
      setNotice('Save this strategy before running the LLM research phase.');
      return;
    }
    if (!draft.active_universe_id) {
      setNotice('Attach a frozen universe before running the LLM research phase.');
      return;
    }
    setReviewing(true);
    try {
      const response = await tradingStrategyApi.runLlmResearch(draft.strategy_id, llmModel);
      setResearchReviews(response.reviews);
      await refreshDetail(draft.strategy_id);
      const reviewed = response.reviews.filter((item) => item.status === 'reviewed').length;
      const missing = response.reviews.filter((item) => item.status === 'missing_evidence').length;
      setNotice(`LLM research finished: ${reviewed} reviewed, ${missing} missing timestamped catalyst evidence. LLM output remains shadow-only.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setReviewing(false);
    }
  };

  const applyLlmExclusions = () => {
    setSelectedCandidates((current) => {
      const next = new Set(current);
      for (const review of researchReviews) {
        const classification = review.classification;
        if (!classification) continue;
        if (classification.directional_bias === 'negative' || classification.dilution_risk === 'explicit') next.delete(review.instrument_id);
      }
      return next;
    });
    setNotice('LLM suggestions changed the UI selection only. Freeze selected candidates to make the human narrowing decision explicit and immutable.');
  };

  const candidateRows = useMemo(() => {
    if (!universe) return [];
    return universe.candidates.map((candidate) => {
      const deterministic = latestDeterministic.get(candidate.instrument_id);
      const research = latestResearch.get(candidate.instrument_id);
      const learningEvent = latestLearning.get(candidate.instrument_id);
      const learning = eventLearning(learningEvent);
      const learningRank = Number(learningEvent?.payload?.rank);
      const stochTrendEvent = latestStochTrend.get(candidate.instrument_id);
      const stochTrend = eventStochTrend(stochTrendEvent);
      const stochTrendEntry = latestStochTrendEntry.get(candidate.instrument_id);
      const stochExecutionEvent = latestStochExecutionSummary.get(candidate.instrument_id);
      const stochExecution = eventStochExecutionSummary(stochExecutionEvent);
      const intradayLlmEvent = latestIntradayLlm.get(candidate.instrument_id);
      const intradayLlm = eventIntradayLlm(intradayLlmEvent);
      const aiMinuteEvent = latestAiMinuteDecision.get(candidate.instrument_id);
      const aiEventEvent = latestAiEventDecision.get(candidate.instrument_id);
      const aiMinuteDecision = aiShadowDecision(aiMinuteEvent);
      const aiEventDecision = aiShadowDecision(aiEventEvent);
      const aiMinuteTrade = aiShadowTrade(latestAiMinuteTrade.get(candidate.instrument_id));
      const aiEventTrade = aiShadowTrade(latestAiEventTrade.get(candidate.instrument_id));
      const localReview = researchReviews.find((item) => item.instrument_id === candidate.instrument_id);
      const classification = localReview?.classification
        ? localReview.classification as unknown as Record<string, unknown>
        : eventClassification(research);
      return {
        candidate,
        deterministic,
        research,
        classification,
        score: eventQuality(deterministic),
        learning,
        learningRank: Number.isFinite(learningRank) ? learningRank : null,
        stochTrend,
        stochTrendEvent,
        stochTrendEntry,
        stochExecution,
        stochExecutionEvent,
        intradayLlm,
        intradayLlmEvent,
        aiMinuteDecision,
        aiMinuteEvent,
        aiMinuteTrade,
        aiEventDecision,
        aiEventEvent,
        aiEventTrade,
      };
    });
  }, [universe, latestDeterministic, latestResearch, latestLearning, latestStochTrend, latestStochTrendEntry, latestStochExecutionSummary, latestIntradayLlm, latestAiMinuteDecision, latestAiEventDecision, latestAiMinuteTrade, latestAiEventTrade, researchReviews]);

  const evidenceReady = universe?.candidates.filter((candidate) => (candidate.catalyst_evidence_ids?.length ?? 0) > 0).length ?? 0;
  const llmReviewed = candidateRows.filter((row) => row.classification !== null).length;
  const evaluated = candidateRows.filter((row) => row.deterministic !== undefined).length;
  const entryReady = candidateRows.filter((row) => row.deterministic?.state === 'entry_ready').length;
  const submitted = events.filter((event) => event.event_type === 'entry_order_submitted').length;

  const phaseState = (phaseId: string): { tone: string; text: string } => {
    if (phaseId === 'discover') return universe ? { tone: 'complete', text: `${universe.candidates.length} frozen` } : { tone: 'pending', text: 'Not started' };
    if (phaseId === 'research') return !universe
      ? { tone: 'pending', text: 'Waiting for scan' }
      : evidenceReady === universe.candidates.length
        ? { tone: 'complete', text: `${evidenceReady}/${universe.candidates.length} evidence-ready` }
        : { tone: 'attention', text: `${evidenceReady}/${universe.candidates.length} have catalyst evidence` };
    if (phaseId === 'llm') return llmReviewed ? { tone: 'complete', text: `${llmReviewed} reviewed` } : { tone: 'pending', text: 'Optional review' };
    if (phaseId === 'deterministic') return evaluated ? { tone: 'active', text: `${evaluated} evaluated · ${draft?.config.structure_interval ?? '—'}` } : { tone: 'pending', text: `Waiting for ${draft?.config.structure_interval ?? 'structure'} bars` };
    if (phaseId === 'selection') return entryReady ? { tone: 'complete', text: `${entryReady} entry-ready` } : { tone: 'pending', text: `Score ≥ ${draft?.config.minimum_quality_score ?? 7}` };
    if (draft?.mode === 'auto_paper') return submitted ? { tone: 'complete', text: `${submitted} submitted` } : { tone: 'active', text: `AUTO PAPER · ${draft.config.execution_interval}` };
    return draft?.mode === 'shadow' ? { tone: 'attention', text: 'Shadow only' } : { tone: 'pending', text: 'Off' };
  };

  return (
    <div className="trading-strategies-panel">
      <aside>
        <div className="trading-strategies-heading">
          <div><strong>Strategies</strong><small>Reusable automated strategy catalog</small></div>
          <button type="button" onClick={startNew}>New</button>
          <button type="button" onClick={startNewStochRsi5m}>New 5m Stoch RSI</button>
        </div>
        {strategies.length ? strategies.map((item) => (
          <button key={item.strategy_id} type="button" className={item.strategy_id === selectedId ? 'active' : undefined} onClick={() => setSelectedId(item.strategy_id)}>
            <strong>{item.parent_strategy_id ? `↳ ${item.strategy_id}` : item.strategy_id}</strong>
            <span>{item.strategy_kind} · {item.mode.replace('_', ' ')}{item.parent_strategy_id ? ' · interday child' : ''}</span>
          </button>
        )) : <p>No strategies configured.</p>}
        <div className="trading-strategy-safety">
          <strong>Paper execution boundary</strong>
          <small>AI and model scores are shadow-only. No live broker route. AUTO PAPER requires deterministic gates, server risk approval, and eligible execution evidence.</small>
        </div>
      </aside>

      <section>
        {selected?.strategy_kind === 'overnight_shadow_v1' ? (
          <OvernightShadowSummary strategy={selected} events={events} />
        ) : stochDraft ? (
          <StochRsi5mEditor
            draft={stochDraft}
            events={events}
            notice={notice}
            status={status}
            onChange={setStochDraft}
            onSave={() => void saveStochRsi5m()}
            onRefresh={() => void refresh()}
            onArchive={() => void archiveStochRsi5m()}
          />
        ) : !draft ? (
          <div className="trading-strategies-empty"><strong>{definition.label}</strong><p>{definition.thesis}</p><button type="button" onClick={startNew}>Create strategy</button><button type="button" onClick={startNewStochRsi5m}>Create 5m Stoch RSI strategy</button></div>
        ) : (
          <>
            <header className="trading-strategy-editor-header">
              <div><strong>{draft.strategy_id}</strong><small>{definition.label} · config v{draft.config.strategy_version} · {draft.config.structure_interval} structure / {draft.config.execution_interval} execution · revision {draft.revision}</small></div>
              <div className="trading-strategy-header-actions">
                {draft.config.strategy_version === '1.0.0' ? <button type="button" onClick={upgradeToStrictV11}>Load v1.1 baseline</button> : null}
                {draft.config.strategy_version === '1.1.0' && htrPromotionAllowed ? <button type="button" onClick={loadReviewedV12}>Load reviewed HTR v1.2</button> : null}
                <button type="button" onClick={loadFrozenV2}>{draft.config.strategy_version === '2.0.0' ? 'Reload frozen V11 v2' : 'Load frozen V11 v2 shadow'}</button>
                <button type="button" onClick={loadFinvizLearningV2}>Load Finviz learning V2</button>
                <button type="button" onClick={() => void refresh()}>Refresh</button>
                {strategies.some((item) => item.strategy_id === draft.strategy_id) && !draft.archived_at ? <button type="button" className="danger" onClick={() => void archiveStrategy()} disabled={status === 'saving'}>Archive</button> : null}
                <button type="button" className="primary" onClick={() => void save()} disabled={status === 'saving' || Boolean(draft.archived_at)}>{status === 'saving' ? 'Saving…' : draft.archived_at ? 'Archived' : 'Save strategy'}</button>
              </div>
            </header>

            {draft.archived_at ? <div className="trading-strategy-notice" role="status">Archived {new Date(draft.archived_at).toLocaleString()}. This strategy is read-only; its historical evidence remains selectable in the Paper Strategy Dashboard.</div> : null}
            {notice ? <div className="trading-strategy-notice" role="status">{notice}</div> : null}

            <TradingStrategyExecutionCredentials />

            <section className="trading-strategy-overview">
              <div><strong>{draft.config.strategy_version === '2.0.0' ? 'Frozen V11 gap-as-impulse / failed-selloff profile' : definition.thesis}</strong><small>{draft.config.strategy_version === '2.0.0' ? 'Prospective profile: confirmed L1 → B1 → higher L2, base ≥4 minutes, L2→breakout ≤8 minutes, direct B1/VWAP break, fill-anchored 1.5R target and causal +0.75R→+0.25R protection. Reconstructed history is not a profitability guarantee.' : 'Higher low + VWAP reclaim + lower-high break remain mandatory. Structure and execution timeframes are separate, and simultaneous entry-ready names use quality score before scan rank.'}</small></div>
              <div className="trading-mode-switch" role="group" aria-label="Strategy mode">
                {(['off', 'shadow', 'auto_paper'] as StrategyMode[]).map((mode) => {
                  const v2AutoBlocked = mode === 'auto_paper' && draft.config.strategy_version === '2.0.0' && !v2Qualification?.auto_paper_authorized;
                  return <button type="button" key={mode} className={draft.mode === mode ? 'active' : undefined} aria-pressed={draft.mode === mode} disabled={v2AutoBlocked} title={v2AutoBlocked ? 'Requires reviewed prospective V2 qualification' : undefined} onClick={() => setDraft({ ...draft, mode })}>{mode === 'auto_paper' ? 'Auto paper' : mode[0].toUpperCase() + mode.slice(1)}</button>;
                })}
              </div>
            </section>

            {draft.config.strategy_version === '2.0.0' ? (
              <section className="trading-config-block" aria-label="V2 prospective qualification">
                <header>
                  <strong>Prospective AUTO PAPER qualification</strong>
                  <small>Frozen Aug 24+ policy · raw morning archive + live eligible SHADOW signal + post-session Alpaca IEX replay</small>
                </header>
                {v2Qualification ? (
                  <>
                    <div className="trading-strategy-grid">
                      <div><strong>Profile</strong><small>{v2Qualification.profile_match ? 'Exact frozen V2 profile' : 'Mismatch — reload frozen V11 v2'}</small></div>
                      <div><strong>Matched trades</strong><small>{v2Qualification.matched_eligible_trade_count} / {v2Qualification.thresholds.minimum_matched_trades}</small></div>
                      <div><strong>Distinct sessions</strong><small>{v2Qualification.distinct_sessions} / {v2Qualification.thresholds.minimum_distinct_sessions}</small></div>
                      <div><strong>Distinct symbols</strong><small>{v2Qualification.distinct_symbols} / {v2Qualification.thresholds.minimum_distinct_symbols}</small></div>
                      <div><strong>Execution match</strong><small>{v2Qualification.execution_match_rate == null ? 'N/A' : `${(Number(v2Qualification.execution_match_rate) * 100).toFixed(1)}%`} · min {(Number(v2Qualification.thresholds.minimum_execution_match_rate) * 100).toFixed(0)}%</small></div>
                      <div><strong>Expectancy</strong><small>{v2Qualification.expectancy_r == null ? 'N/A' : `${Number(v2Qualification.expectancy_r).toFixed(3)}R`} · min +{Number(v2Qualification.thresholds.minimum_expectancy_r).toFixed(2)}R</small></div>
                      <div><strong>90% lower bound</strong><small>{v2Qualification.one_sided_90_lcb_r == null ? 'N/A' : `${Number(v2Qualification.one_sided_90_lcb_r).toFixed(3)}R`} · must be &gt; 0R</small></div>
                      <div><strong>Max drawdown</strong><small>{v2Qualification.max_drawdown_r == null ? 'N/A' : `${Number(v2Qualification.max_drawdown_r).toFixed(3)}R`} · max {Number(v2Qualification.thresholds.maximum_drawdown_r).toFixed(1)}R</small></div>
                    </div>
                    <p><small>Profile <code>{v2Qualification.current_profile_fingerprint.slice(0, 12)}</code> · evidence <code>{v2Qualification.evidence_fingerprint.slice(0, 12)}</code> · replay trades {v2Qualification.replay_trade_count}. {v2Qualification.reason_codes.length ? `Blocking: ${v2Qualification.reason_codes.join(', ')}` : 'All quantitative floors pass.'}</small></p>
                    <p><strong>{v2Qualification.auto_paper_authorized ? 'AUTO PAPER authorized' : v2Qualification.qualified ? 'Quantitatively qualified — explicit review required' : 'Prospective qualification in progress'}</strong></p>
                    {v2Qualification.qualified && !v2Qualification.reviewed ? (
                      <div className="trading-strategy-grid">
                        <label className="wide-field"><span>Promotion review note<small>binds approval to this exact evidence fingerprint</small></span><textarea value={v2ReviewNote} onChange={(event) => setV2ReviewNote(event.target.value)} placeholder="Review the prospective sample, execution coverage, drawdown and edge before approving AUTO PAPER." /></label>
                        <button type="button" onClick={() => void reviewV2Qualification()} disabled={v2Reviewing || v2ReviewNote.trim().length < 10}>{v2Reviewing ? 'Recording review…' : 'Approve exact V2 evidence snapshot'}</button>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p><small>Save the frozen V2 profile in SHADOW mode first. Qualification begins with the prospective epoch on 2026-08-24; historical reconstruction cannot unlock AUTO PAPER.</small></p>
                )}
              </section>
            ) : null}

            <section className="trading-strategy-pipeline">
              <header><div><strong>Daily strategy phases</strong><small>Scan → research → LLM review → deterministic setup → select → paper trade.</small></div></header>
              <div className="trading-strategy-phase-grid">
                {definition.phases.map((phase) => { const state = phaseState(phase.id); return <article key={phase.id} data-tone={state.tone}><header><strong>{phase.label}</strong><span>{state.text}</span></header><p>{phase.description}</p>{phase.safety ? <small>{phase.safety}</small> : null}</article>; })}
              </div>
            </section>

            <details className="trading-strategy-config-section" open>
              <summary><strong>Strategy configuration</strong><span>Every execution-authorizing detail is configurable and reviewable.</span></summary>
              <div className="trading-strategy-config-body">
                <div className="trading-config-block">
                  <header><strong>Identity & paper account</strong><small>Instance settings</small></header>
                  <div className="trading-strategy-grid">
                    <label><span>Strategy type</span><select value={draft.strategy_kind} disabled><option value="gap_pullback_v1">{definition.label}</option></select></label>
                    <label><span>Paper account</span><select value={draft.account_id} onChange={(event) => setDraft({ ...draft, account_id: event.target.value })}>{accounts.map((account) => <option key={account.account_id} value={account.account_id}>{account.name}</option>)}</select></label>
                    <label><span>Frozen universe ID</span><input value={draft.active_universe_id ?? ''} onChange={(event) => setDraft({ ...draft, active_universe_id: event.target.value || null })} placeholder="finviz-gappers-..." /></label>
                    <label className="toggle-field"><span>Enabled</span><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /></label>
                  </div>
                </div>

                <div className="trading-config-block">
                  <header><strong>1. Scanner & liquidity</strong><small>Initial candidate gates and point-in-time archive</small></header>
                  <div className="trading-strategy-grid">
                    <label><span>Morning scan time ET<small>research/archive checkpoint</small></span><input type="time" step="60" value={draft.config.universe_scan_time_et ?? '09:20:00'} onChange={(event) => setConfig('universe_scan_time_et', event.target.value)} /></label>
                    <label><span>Discovery source<small>morning cohort only</small></span><select value={draft.config.universe_discovery_source ?? 'yahoo'} onChange={(event) => {
                      const source = event.target.value as 'yahoo' | 'finviz';
                      setDraft((current) => current ? {
                        ...current,
                        config: {
                          ...current.config,
                          universe_discovery_source: source,
                          universe_discovery_count: source === 'finviz'
                            ? Math.min(current.config.universe_discovery_count ?? 20, 20)
                            : current.config.universe_discovery_count,
                        },
                      } : current);
                    }}><option value="finviz">Finviz Top Gainers</option><option value="yahoo">Yahoo Day Gainers</option></select></label>
                    <label><span>Execution owner<small>which task runs it; the order gateway and its checks are the same</small></span><select value={draft.config.execution_owner ?? 'monitor'} onChange={(event) => setConfig('execution_owner', event.target.value as GapPullbackConfig['execution_owner'])}><option value="monitor">Strategy monitor</option><option value="runner_shadow">Monitor, runner shadows (parity)</option><option value="runner">Strategy runner</option></select></label>
                    <label className="toggle-field"><span>Intraday learning<small>research-only dynamic ranking; never order authority</small></span><input type="checkbox" checked={draft.config.intraday_learning_enabled ?? true} onChange={(event) => setDraft({ ...draft, config: { ...draft.config, intraday_learning_enabled: event.target.checked, stoch_trend_capture_enabled: event.target.checked ? draft.config.stoch_trend_capture_enabled : false, intraday_llm_enabled: event.target.checked ? draft.config.intraday_llm_enabled : false } })} /></label>
                    <label className="toggle-field"><span>3m Stoch trend capture<small>first oversold entry; range exit or 25% + trend runner; SHADOW only</small></span><input type="checkbox" checked={draft.config.stoch_trend_capture_enabled ?? false} disabled={!draft.config.intraday_learning_enabled} onChange={(event) => setConfig('stoch_trend_capture_enabled', event.target.checked)} /></label>
                    <label className="toggle-field"><span>Intraday LLM analyst<small>default LLM; interpretation only, never order authority</small></span><input type="checkbox" checked={draft.config.intraday_llm_enabled ?? false} onChange={(event) => setConfig('intraday_llm_enabled', event.target.checked)} /></label>
                    <label><span>LLM active candidates<small>material events first; heartbeat uses top-ranked names</small></span><input type="number" min="1" max="20" value={draft.config.intraday_llm_top_n ?? 5} onChange={(event) => setConfig('intraday_llm_top_n', Number(event.target.value))} /></label>
                    <label><span>LLM heartbeat<small>minutes for quiet top names; material events run sooner</small></span><input type="number" min="5" max="60" value={draft.config.intraday_llm_interval_minutes ?? 10} onChange={(event) => setConfig('intraday_llm_interval_minutes', Number(event.target.value))} /></label>
                    <label className="toggle-field"><span>Auto-archive morning universe<small>evidence only; never authorizes orders</small></span><input type="checkbox" checked={draft.config.auto_archive_daily_universe ?? true} onChange={(event) => setConfig('auto_archive_daily_universe', event.target.checked)} /></label>
                    <label><span>Archive grace<small>minutes after scan time</small></span><input type="number" min="1" max="60" value={draft.config.universe_archive_grace_minutes ?? 10} onChange={(event) => setConfig('universe_archive_grace_minutes', Number(event.target.value))} /></label>
                    <label><span>Discovery candidates<small>{draft.config.universe_discovery_source === 'finviz' ? 'atomic first page; max 20' : 'raw top-gainer count'}</small></span><input type="number" min="1" max={draft.config.universe_discovery_source === 'finviz' ? 20 : 100} value={Math.min(draft.config.universe_discovery_count ?? 50, draft.config.universe_discovery_source === 'finviz' ? 20 : 100)} onChange={(event) => setConfig('universe_discovery_count', Number(event.target.value))} /></label>
                    <ConfigNumber label="Minimum gap" suffix="%" step="0.5" value={draft.config.minimum_gap_pct} onChange={(value) => setConfigNumber('minimum_gap_pct', value)} />
                    <ConfigNumber label="Minimum price" suffix="$" step="0.01" value={draft.config.minimum_price} onChange={(value) => setConfigNumber('minimum_price', value)} />
                    <ConfigNumber label="Maximum price" suffix="$" step="0.01" value={draft.config.maximum_price} onChange={(value) => setConfigNumber('maximum_price', value)} />
                    <ConfigNumber label="Premarket dollar volume" suffix="$" step="1000000" value={draft.config.minimum_premarket_dollar_volume} onChange={(value) => setConfigNumber('minimum_premarket_dollar_volume', value)} />
                    <ConfigNumber label="Minimum TOD RVOL" suffix="×" step="0.5" value={draft.config.minimum_tod_rvol} onChange={(value) => setConfigNumber('minimum_tod_rvol', value)} />
                    <label className="toggle-field"><span>Allow missing TOD RVOL<small>reconstructed history only</small></span><input type="checkbox" checked={draft.config.allow_missing_tod_rvol} onChange={(event) => setConfig('allow_missing_tod_rvol', event.target.checked)} /></label>
                    <ConfigNumber label="Maximum spread" suffix="bps" step="10" value={draft.config.maximum_spread_bps} onChange={(value) => setConfigNumber('maximum_spread_bps', value)} />
                    <ConfigNumber label="Preferred float minimum" suffix="shares" step="100000" value={draft.config.preferred_float_min_shares} onChange={(value) => setConfigNumber('preferred_float_min_shares', value)} />
                    <ConfigNumber label="Preferred float maximum" suffix="shares" step="100000" value={draft.config.preferred_float_max_shares} onChange={(value) => setConfigNumber('preferred_float_max_shares', value)} />
                    <label><span>Float handling</span><select value={draft.config.float_preference_mode} onChange={(event) => setConfig('float_preference_mode', event.target.value as GapPullbackConfig['float_preference_mode'])}><option value="ignore">Ignore</option><option value="score">Score preference</option><option value="require">Hard require</option></select></label>
                  </div>
                </div>

                <div className="trading-config-block">
                  <header><strong>2. Catalyst & supply research</strong><small>Deterministic evidence gates</small></header>
                  <div className="trading-strategy-grid">
                    <label className="toggle-field"><span>Require timestamped catalyst evidence</span><input type="checkbox" checked={draft.config.require_catalyst_evidence} onChange={(event) => setConfig('require_catalyst_evidence', event.target.checked)} /></label>
                    <label className="wide-field"><span>Reject supply / dilution flags<small>comma separated</small></span><input value={draft.config.reject_dilution_flags.join(', ')} onChange={(event) => setConfig('reject_dilution_flags', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} /></label>
                  </div>
                </div>

                <div className="trading-config-block">
                  <header><strong>3. Failed sell-off structure</strong><small>Causal price/volume requirements</small></header>
                  <div className="trading-strategy-grid">
                    <label><span>Structure timeframe<small>L1 / B1 / L2, VWAP, breakout</small></span><select value={draft.config.structure_interval} onChange={(event) => { const structure_interval = event.target.value as GapPullbackConfig['structure_interval']; setDraft({ ...draft, config: { ...draft.config, structure_interval, execution_interval: structure_interval === '1m' ? '1m' : draft.config.execution_interval } }); }}><option value="1m">1 minute</option><option value="5m">5 minutes</option></select></label>
                    <label><span>Execution resolution<small>backtest entry/stop/target bars</small></span><select value={draft.config.execution_interval} onChange={(event) => setConfig('execution_interval', event.target.value as GapPullbackConfig['execution_interval'])}><option value="1m">1 minute</option><option value="5m" disabled={draft.config.structure_interval === '1m'}>5 minutes</option></select></label>
                    <ConfigNumber label="Opening impulse minimum" suffix="%" step="0.5" value={draft.config.opening_impulse_min_pct} onChange={(value) => setConfigNumber('opening_impulse_min_pct', value)} />
                    <ConfigNumber label="Pullback depth minimum" suffix="%" step="1" value={draft.config.pullback_min_pct} onChange={(value) => setConfigNumber('pullback_min_pct', value)} />
                    <ConfigNumber label="Pullback depth maximum" suffix="%" step="1" value={draft.config.pullback_max_pct} onChange={(value) => setConfigNumber('pullback_max_pct', value)} />
                    <ConfigNumber label="Selling / impulse volume max" suffix="ratio" step="0.05" value={draft.config.pullback_volume_max_ratio} onChange={(value) => setConfigNumber('pullback_volume_max_ratio', value)} />
                    <ConfigNumber label="Higher-low buffer" suffix="bps" step="5" value={draft.config.higher_low_buffer_bps} onChange={(value) => setConfigNumber('higher_low_buffer_bps', value)} />
                    <ConfigNumber label="Breakout volume minimum" suffix="× recent" step="0.05" value={draft.config.breakout_volume_ratio} onChange={(value) => setConfigNumber('breakout_volume_ratio', value)} />
                    <label><span>Pivot left bars</span><input type="number" min="1" max="10" value={draft.config.pivot_left_bars} onChange={(event) => setConfig('pivot_left_bars', Number(event.target.value))} /></label>
                    <label><span>Pivot right bars</span><input type="number" min="1" max="10" value={draft.config.pivot_right_bars} onChange={(event) => setConfig('pivot_right_bars', Number(event.target.value))} /></label>
                    <label><span>Volume lookback bars</span><input type="number" min="2" max="100" value={draft.config.volume_lookback_bars} onChange={(event) => setConfig('volume_lookback_bars', Number(event.target.value))} /></label>
                  </div>
                </div>

                {draft.config.strategy_version === '2.0.0' ? (
                  <div className="trading-config-block">
                    <header><strong>V2. Frozen timing & management</strong><small>V11 causal profile; edit only when deliberately creating a new strategy version/experiment</small></header>
                    <div className="trading-strategy-grid">
                      <ConfigNumber label="L1→B1 recovery minimum" suffix="%" step="0.5" value={draft.config.v2_recovery_min_pct ?? '5'} onChange={(value) => setConfigNumber('v2_recovery_min_pct', value)} />
                      <ConfigNumber label="Second pullback minimum" suffix="%" step="0.5" value={draft.config.v2_second_pullback_min_pct ?? '2'} onChange={(value) => setConfigNumber('v2_second_pullback_min_pct', value)} />
                      <label><span>L1→B1 minimum<small>finalized 1m bars</small></span><input type="number" min="0" max="120" value={draft.config.v2_minimum_l1_to_b1_minutes ?? 4} onChange={(event) => setConfig('v2_minimum_l1_to_b1_minutes', Number(event.target.value))} /></label>
                      <label><span>L2→signal maximum<small>finalized 1m bars</small></span><input type="number" min="1" max="390" value={draft.config.v2_maximum_l2_to_signal_minutes ?? 8} onChange={(event) => setConfig('v2_maximum_l2_to_signal_minutes', Number(event.target.value))} /></label>
                      <ConfigNumber label="V2 breakout volume minimum" suffix="× prior 5" step="0.1" value={draft.config.v2_minimum_breakout_volume_ratio ?? '0'} onChange={(value) => setConfigNumber('v2_minimum_breakout_volume_ratio', value)} />
                      <ConfigNumber label="Profit-protection trigger" suffix="R" step="0.05" value={draft.config.v2_profit_protection_trigger_r ?? '0.75'} onChange={(value) => setConfigNumber('v2_profit_protection_trigger_r', value)} />
                      <ConfigNumber label="Protected stop" suffix="R" step="0.05" value={draft.config.v2_protected_stop_r ?? '0.25'} onChange={(value) => setConfigNumber('v2_protected_stop_r', value)} />
                      <label><span>Maximum hold<small>minutes after fill</small></span><input type="number" min="1" max="390" value={draft.config.v2_max_hold_minutes ?? 60} onChange={(event) => setConfig('v2_max_hold_minutes', Number(event.target.value))} /></label>
                    </div>
                    <small>Profit protection is armed only by a prior finalized execution bar; a bar cannot tighten its own stop.</small>
                  </div>
                ) : null}

                <div className="trading-config-block">
                  <header><strong>4. Breakout confirmation & quality</strong><small>0–10 ranking/gating model</small></header>
                  <div className="trading-strategy-grid">
                    <label className="toggle-field"><span>Require break-and-hold / retest</span><input type="checkbox" checked={draft.config.require_breakout_hold} onChange={(event) => setConfig('require_breakout_hold', event.target.checked)} /></label>
                    <label><span>Hold bars</span><input type="number" min="1" max="5" value={draft.config.breakout_hold_bars} onChange={(event) => setConfig('breakout_hold_bars', Number(event.target.value))} /></label>
                    <ConfigNumber label="Hold tolerance" suffix="bps below B1" step="5" value={draft.config.breakout_hold_tolerance_bps} onChange={(value) => setConfigNumber('breakout_hold_tolerance_bps', value)} />
                    <label><span>Minimum quality score<small>/ 10</small></span><input type="number" min="0" max="10" value={draft.config.minimum_quality_score} onChange={(event) => setConfig('minimum_quality_score', Number(event.target.value))} /></label>
                    <ConfigNumber label="Stop buffer" suffix="bps below L2" step="5" value={draft.config.stop_buffer_bps} onChange={(value) => setConfigNumber('stop_buffer_bps', value)} />
                    <ConfigNumber label="Reward multiple" suffix="R" step="0.25" value={draft.config.reward_multiple} onChange={(value) => setConfigNumber('reward_multiple', value)} />
                    <label><span>RSI exit period<small>execution bars</small></span><input type="number" min="2" max="100" value={draft.config.exit_rsi_period} onChange={(event) => setConfig('exit_rsi_period', Number(event.target.value))} /></label>
                    <ConfigNumber label="RSI exit threshold" suffix="RSI" step="1" value={draft.config.exit_rsi_threshold} onChange={(value) => setConfigNumber('exit_rsi_threshold', value)} />
                    <label><span>Entry starts ET</span><input type="time" step="1" value={draft.config.entry_start_et} onChange={(event) => setConfig('entry_start_et', event.target.value)} /></label>
                    <label><span>Last entry ET</span><input type="time" step="1" value={draft.config.last_entry_et} onChange={(event) => setConfig('last_entry_et', event.target.value)} /></label>
                  </div>
                  <div className="quality-score-explainer"><span><b>0–2</b> fresh catalyst</span><span><b>0–2</b> supply / float</span><span><b>0–2</b> opening structure</span><span><b>0–2</b> controlled pullback</span><span><b>0–2</b> reclaim + break + hold</span></div>
                  <div className="quality-score-explainer"><span><b>Portfolio priority</b> earlier signal time → higher quality score → lower scan rank → symbol</span></div>
                </div>

                <div className="trading-config-block">
                  <header><strong>5. Server risk & protection</strong><small>Paper-account authority</small></header>
                  <div className="trading-strategy-grid">
                    <ConfigNumber label="Risk per trade" suffix="%" step="0.05" value={draft.risk.risk_per_trade_pct} onChange={(value) => setDraft({ ...draft, risk: { ...draft.risk, risk_per_trade_pct: value } })} />
                    <ConfigNumber label="Maximum daily loss" suffix="%" step="0.1" value={draft.risk.max_daily_loss_pct} onChange={(value) => setDraft({ ...draft, risk: { ...draft.risk, max_daily_loss_pct: value } })} />
                    <ConfigNumber label="Maximum open risk" suffix="%" step="0.1" value={draft.risk.max_open_risk_pct} onChange={(value) => setDraft({ ...draft, risk: { ...draft.risk, max_open_risk_pct: value } })} />
                    <label><span>Maximum positions</span><input type="number" min="1" max="50" value={draft.risk.max_positions} onChange={(event) => setDraft({ ...draft, risk: { ...draft.risk, max_positions: Number(event.target.value) } })} /></label>
                    <label><span>Maximum trades / day</span><input type="number" min="1" max="100" value={draft.risk.max_trades_per_day} onChange={(event) => setDraft({ ...draft, risk: { ...draft.risk, max_trades_per_day: Number(event.target.value) } })} /></label>
                    <ConfigNumber label="Maximum trade value" suffix="$" step="1000" value={draft.risk.max_trade_value} onChange={(value) => setDraft({ ...draft, risk: { ...draft.risk, max_trade_value: value } })} />
                    <ConfigNumber label="Risk spread ceiling" suffix="bps" step="10" value={draft.risk.max_spread_bps} onChange={(value) => setDraft({ ...draft, risk: { ...draft.risk, max_spread_bps: value } })} />
                    <label className="toggle-field"><span>One trade / symbol / day</span><input type="checkbox" checked={draft.risk.one_trade_per_symbol_per_day} onChange={(event) => setDraft({ ...draft, risk: { ...draft.risk, one_trade_per_symbol_per_day: event.target.checked } })} /></label>
                    <label><span>Force flat ET</span><input type="time" step="1" value={draft.risk.force_flat_et} onChange={(event) => setDraft({ ...draft, risk: { ...draft.risk, force_flat_et: event.target.value } })} /></label>
                    <label className="toggle-field danger"><span>Kill switch</span><input type="checkbox" checked={draft.risk.kill_switch} onChange={(event) => setDraft({ ...draft, risk: { ...draft.risk, kill_switch: event.target.checked } })} /></label>
                  </div>
                </div>
              </div>
            </details>

            {selected ? (
              selectedGap ? <TradingStrategyBacktest strategy={selectedGap} /> : <section className="strategy-range-backtest"><header><div><strong>Backtest this strategy</strong><small>5m Stoch RSI uses the replay endpoint and shadow evidence; the gap-pullback backtest does not apply.</small></div></header></section>
            ) : (
              <section className="strategy-range-backtest"><header><div><strong>Backtest this strategy</strong><small>Save the strategy first so the backtest is pinned to a persisted configuration revision.</small></div></header></section>
            )}

            <section className="trading-research-workbench">
              <header>
                <div><strong>Freeze point-in-time gapper universe</strong><small>Research workbench: scan, collect timestamped evidence, narrow, run optional LLM review, then attach the selected daily universe.</small></div>
                <div className="workbench-actions">
                  <button type="button" className="primary" onClick={() => void discoverFinviz()} disabled={discovering || freezing || capturingEvidence}>{discovering ? 'Scanning…' : 'Scan Finviz Top Gainers & freeze'}</button>
                  <button type="button" onClick={() => void discoverYahoo()} disabled={discovering || freezing || capturingEvidence}>Yahoo fallback</button>
                  <button type="button" onClick={() => void captureYahooEvidence()} disabled={capturingEvidence || draft.mode === 'auto_paper'}>{capturingEvidence ? 'Collecting…' : 'Collect Yahoo catalyst evidence'}</button>
                  <button type="button" onClick={() => void freezeEdited()} disabled={freezing || discovering || capturingEvidence || !universeJson.trim()}>{freezing ? 'Freezing…' : 'Freeze edited evidence'}</button>
                </div>
              </header>

              <div className="llm-research-bar">
                <div><strong>LLM research phase</strong><small>Uses only attached timestamped catalyst evidence. AI and model scores are shadow-only.</small></div>
                <input aria-label="LLM model override" value={llmModel} onChange={(event) => setLlmModel(event.target.value)} placeholder="Optional model override" />
                <button type="button" onClick={() => void runLlmReview()} disabled={reviewing}>{reviewing ? 'Reviewing…' : 'Run LLM review'}</button>
                <button type="button" onClick={applyLlmExclusions} disabled={!researchReviews.length}>Apply suggested exclusions</button>
              </div>

              {universe ? (
                <>
                  <div className="candidate-toolbar">
                    <div><strong>{universe.universe_id}</strong><small>{universe.session_date} · {universe.discovery_source} · {universe.candidates.length} captured · {selectedCandidates.size} selected · simultaneous entries: score first, then scan rank</small></div>
                    <div><button type="button" onClick={() => setSelectedCandidates(new Set(universe.candidates.map((candidate) => candidate.instrument_id)))}>Select all</button><button type="button" onClick={() => setSelectedCandidates(new Set())}>Select none</button><button type="button" className="primary" onClick={() => void freezeSelected()} disabled={freezing || !selectedCandidates.size}>Freeze selected & attach</button></div>
                  </div>
                  <div className="candidate-table-wrap">
                    <table className="strategy-candidate-table">
                      <thead><tr><th>Use</th><th>Candidate</th><th>Intraday learning</th><th>Stoch trend</th><th>AI every 1m</th><th>AI event</th><th>Intraday LLM</th><th>Gap</th><th>Price</th><th>RVOL</th><th>$ volume</th><th>Float</th><th>Spread</th><th>Catalyst</th><th>Supply</th><th>Premkt LLM</th><th>Deterministic state</th><th>Score</th></tr></thead>
                      <tbody>{candidateRows.map(({ candidate, deterministic, research, classification, score, learning, learningRank, stochTrend, stochTrendEvent, stochTrendEntry, stochExecution, stochExecutionEvent, aiMinuteDecision, aiMinuteEvent, aiMinuteTrade, aiEventDecision, aiEventEvent, aiEventTrade, intradayLlm, intradayLlmEvent }) => <tr key={candidate.instrument_id} data-tone={deterministic ? eventTone(deterministic) : 'working'}><td><input type="checkbox" aria-label={`Use ${candidate.instrument_id}`} checked={selectedCandidates.has(candidate.instrument_id)} onChange={(event) => setSelectedCandidates((current) => { const next = new Set(current); if (event.target.checked) next.add(candidate.instrument_id); else next.delete(candidate.instrument_id); return next; })} /></td><td><strong>{candidate.instrument_id.split(':').at(-1) ?? candidate.instrument_id}</strong><small>morning rank {candidate.discovery_rank ?? '—'}</small></td><td>{learning ? <><strong>#{learningRank ?? '—'} · {String(learning.pattern ?? 'unresolved').replaceAll('_', ' ')}</strong><small>sqz {learningScore(learning, 'squeeze_probability_score') ?? '—'} · fail {learningScore(learning, 'failed_selloff_probability_score') ?? '—'} · trend {learningScore(learning, 'trend_continuation_score') ?? '—'} · hold {learningScore(learning, 'gap_retention_score') ?? '—'}</small></> : <span className="warn">waiting for finalized bars</span>}</td><td>{stochTrend ? <><strong>{String(stochTrend.state ?? stochTrendEvent?.state ?? 'waiting').replaceAll('_', ' ')}</strong><small>{stochTrend.state === 'data_gap' ? 'replay invalid — internal 3m data gap' : stochTrendEntry ? (eventStochEntryAllowed(stochTrendEntry) === true ? 'entry eligible' : eventStochEntryAllowed(stochTrendEntry) === false ? 'entry vetoed' : 'entry evidence captured') : draft.config.stoch_trend_capture_enabled ? 'awaiting first 3m oversold' : 'disabled'}</small>{stochExecution?.complete === true && stochExecution.net_execution_return_pct != null ? <small>net {Number(stochExecution.net_execution_return_pct).toFixed(2)}% · drag {Number(stochExecution.execution_drag_pct ?? 0).toFixed(2)}%</small> : stochTrend.return_pct != null ? <small>ref {Number(stochTrend.return_pct).toFixed(2)}% · execution {stochExecutionEvent?.state ?? 'pending'}</small> : null}</> : draft.config.stoch_trend_capture_enabled ? <span className="warn">awaiting 3m signal</span> : '—'}</td><td>{aiMinuteDecision ? <><strong title={String(aiMinuteDecision.reason ?? '')}>{String(aiMinuteEvent?.payload?.effective_action ?? aiMinuteDecision.action ?? 'skip').toUpperCase()} · {Number(aiMinuteDecision.confidence ?? 0)}%</strong><small>{String(aiMinuteDecision.market_regime ?? 'unresolved').replaceAll('_', ' ')}</small>{aiMinuteTrade?.net_execution_return_pct != null ? <small>last net {Number(aiMinuteTrade.net_execution_return_pct).toFixed(2)}% · stability {Number(aiMinuteTrade.decision_stability ?? 0).toFixed(2)}</small> : null}</> : <span className="warn">awaiting 1m AI</span>}</td><td>{aiEventDecision ? <><strong title={String(aiEventDecision.reason ?? '')}>{String(aiEventEvent?.payload?.effective_action ?? aiEventDecision.action ?? 'skip').toUpperCase()} · {Number(aiEventDecision.confidence ?? 0)}%</strong><small>{String(aiEventDecision.market_regime ?? 'unresolved').replaceAll('_', ' ')}</small>{aiEventTrade?.net_execution_return_pct != null ? <small>last net {Number(aiEventTrade.net_execution_return_pct).toFixed(2)}% · stability {Number(aiEventTrade.decision_stability ?? 0).toFixed(2)}</small> : <small>{Array.isArray(aiEventEvent?.payload?.trigger_reasons) ? (aiEventEvent?.payload?.trigger_reasons as unknown[]).join(', ') : 'waiting for next event'}</small>}</> : <span className="warn">awaiting event AI</span>}</td><td>{intradayLlm ? <><strong title={String(intradayLlm.summary ?? '')}>{String(intradayLlm.market_regime ?? 'unresolved').replaceAll('_', ' ')} · {intradayLlmProbability(intradayLlm, 'confidence') ?? '—'}%</strong><small>sqz {intradayLlmProbability(intradayLlm, 'squeeze_probability') ?? '—'} · fail {intradayLlmProbability(intradayLlm, 'failed_selloff_probability') ?? '—'} · trend {intradayLlmProbability(intradayLlm, 'trend_continuation_probability') ?? '—'} · dist {intradayLlmProbability(intradayLlm, 'distribution_probability') ?? '—'} · {String(intradayLlm.thesis_change ?? '—')}</small><small>{intradayLlmEvent?.payload?.model ? String(intradayLlmEvent.payload.model) : 'default LLM'}</small></> : draft.config.intraday_llm_enabled ? <span className="warn">awaiting LLM batch</span> : '—'}</td><td>{percent(candidate.gap_pct)}</td><td>${numberValue(candidate.premarket_price)?.toFixed(2) ?? '—'}</td><td>{numberValue(candidate.tod_rvol)?.toFixed(1) ?? '—'}×</td><td>${compact(candidate.premarket_dollar_volume)}</td><td>{compact(candidate.float_shares)}</td><td>{numberValue(candidate.spread_bps)?.toFixed(0) ?? '—'} bps</td><td>{(candidate.catalyst_evidence_ids?.length ?? 0) > 0 ? <span className="pass">{candidate.catalyst_evidence_ids?.length} evidence</span> : <span className="warn">missing</span>}</td><td>{candidate.dilution_flags?.length ? <span className="fail">{candidate.dilution_flags.join(', ')}</span> : <span className="pass">clean flags</span>}</td><td>{classification ? <span title={String(classification.rationale ?? '')}>{String(classification.directional_bias ?? classification.catalyst_class ?? 'reviewed')} · {Math.round(Number(classification.confidence ?? 0) * 100)}%</span> : research?.state === 'research_missing' ? <span className="warn">needs evidence</span> : '—'}</td><td><strong>{deterministic?.state ?? 'not evaluated'}</strong><small>{deterministic?.reason_code ?? ''}</small></td><td><span className={score !== null && score >= draft.config.minimum_quality_score ? 'score pass' : 'score'}>{score ?? '—'}/10</span></td></tr>)}</tbody>
                    </table>
                  </div>
                </>
              ) : <div className="workbench-empty">Run the Finviz Top Gainers scan (preferred), use Yahoo fallback, or attach an immutable universe to start today's research pipeline.</div>}

              <details className="universe-json-editor"><summary>Point-in-time evidence JSON</summary><p>Yahoo headline evidence is a starting point. Attach SEC/company evidence IDs and deterministic supply flags here when available, then freeze a new immutable research snapshot. Existing universe IDs are never mutated.</p><textarea aria-label="Gapper universe JSON" value={universeJson} onChange={(event) => setUniverseJson(event.target.value)} placeholder={'[{"instrument_id":"equity:NASDAQ:XYZ","gap_pct":"35","premarket_dollar_volume":"15000000","tod_rvol":"8","float_shares":"8000000","catalyst_evidence_ids":["ev-..."],"dilution_flags":[]}]'} /></details>
            </section>

            {draft.strategy_id === INTERDAY_TRADING_STRATEGY_ID ? (
              <section className="trading-config-block" aria-label="Interday SHADOW substrategy group">
                <header>
                  <strong>Interday SHADOW substrategy group</strong>
                  <small>Same frozen Finviz Top-5 cohort · four embedded arms plus three linked deterministic child strategies</small>
                </header>
                <div className="trading-strategy-grid">
                  {INTERDAY_SUBSTRATEGIES.map((substrategy) => {
                    const child = substrategy.strategyId
                      ? strategies.find((item) => item.strategy_id === substrategy.strategyId)
                      : null;
                    const embeddedEnabled = substrategy.key === 'deterministic-v2'
                      || (substrategy.key === 'stoch-trend-capture' && draft.config.stoch_trend_capture_enabled)
                      || (substrategy.key === 'ai-every-minute' && draft.config.intraday_llm_enabled)
                      || (substrategy.key === 'ai-event-driven' && draft.config.intraday_llm_enabled);
                    const enabled = child ? child.enabled && child.mode !== 'off' : embeddedEnabled;
                    return <div key={substrategy.key}>
                      <strong>{substrategy.label}</strong>
                      <small>{substrategy.description}</small>
                      <small>{child ? `${child.mode.replace('_', ' ')} · ${child.enabled ? 'enabled' : 'disabled'}${child.parent_strategy_id === draft.strategy_id ? ' · attached' : ' · not attached'}` : enabled ? 'enabled in parent' : 'disabled in parent'}</small>
                    </div>;
                  })}
                </div>
                {latestFourArmComparison ? (
                  <>
                    <div className="trading-strategy-grid">
                      <div>
                        <strong>A · Deterministic V2</strong>
                        <small>{Number(latestFourArmComparison.armA?.replay_trade_count ?? 0)} replay trades · mean {latestFourArmComparison.armA?.mean_r_result == null ? '—' : `${Number(latestFourArmComparison.armA.mean_r_result).toFixed(3)}R`}</small>
                        <small>live entry spread {latestFourArmComparison.armA?.mean_live_entry_spread_bps == null ? '—' : `${Number(latestFourArmComparison.armA.mean_live_entry_spread_bps).toFixed(0)} bps`} · eligible {Number(latestFourArmComparison.armA?.live_execution_eligible_signal_count ?? 0)}/{Number(latestFourArmComparison.armA?.live_shadow_execution_observation_count ?? 0)}</small>
                      </div>
                      <div>
                        <strong>B · Stoch trend</strong>
                        <small>{Number(latestFourArmComparison.armB?.completed_trade_count ?? 0)} trades · mean net {latestFourArmComparison.armB?.mean_net_execution_return_pct == null ? '—' : `${Number(latestFourArmComparison.armB.mean_net_execution_return_pct).toFixed(2)}%`}</small>
                        <small>mean execution drag {latestFourArmComparison.armB?.mean_execution_drag_pct == null ? '—' : `${Number(latestFourArmComparison.armB.mean_execution_drag_pct).toFixed(2)}%`}</small>
                      </div>
                      <div>
                        <strong>C · Pure AI every 1m</strong>
                        <small>{Number(latestFourArmComparison.armC?.trade_count ?? 0)} trades · mean net {latestFourArmComparison.armC?.mean_net_execution_return_pct == null ? '—' : `${Number(latestFourArmComparison.armC.mean_net_execution_return_pct).toFixed(2)}%`}</small>
                        <small>{Number(latestFourArmComparison.armC?.llm_call_count ?? 0)} calls · {compact(Number(latestFourArmComparison.armC?.llm_total_tokens ?? 0))} tokens · stability {latestFourArmComparison.armC?.overall_decision_stability == null ? '—' : Number(latestFourArmComparison.armC.overall_decision_stability).toFixed(2)} · errors {Number(latestFourArmComparison.armC?.llm_error_count ?? 0)}</small>
                      </div>
                      <div>
                        <strong>D · Event AI hybrid</strong>
                        <small>{Number(latestFourArmComparison.armD?.trade_count ?? 0)} trades · mean net {latestFourArmComparison.armD?.mean_net_execution_return_pct == null ? '—' : `${Number(latestFourArmComparison.armD.mean_net_execution_return_pct).toFixed(2)}%`}</small>
                        <small>{Number(latestFourArmComparison.armD?.llm_call_count ?? 0)} calls · {compact(Number(latestFourArmComparison.armD?.llm_total_tokens ?? 0))} tokens · stability {latestFourArmComparison.armD?.overall_decision_stability == null ? '—' : Number(latestFourArmComparison.armD.overall_decision_stability).toFixed(2)} · errors {Number(latestFourArmComparison.armD?.llm_error_count ?? 0)}</small>
                      </div>
                    </div>
                    <p>
                      <small>
                        Data gaps: {Number(latestFourArmComparison.dataQuality?.market_data_gap_event_count ?? 0)} · cohort {Number(latestFourArmComparison.dataQuality?.cohort_candidate_count ?? 0)} symbols
                        {latestFourArmComparison.observedAt ? ` · snapshot ${new Date(latestFourArmComparison.observedAt).toLocaleTimeString()}` : ''}
                        {' · '}Return units and execution models are intentionally not harmonized yet, so this panel does not rank A/B/C/D.
                      </small>
                    </p>
                  </>
                ) : (
                  <p><small>Prospective comparison is collecting. The first A/B/C/D snapshot is produced after the regular session closes; intraday AI actions remain visible in the candidate table above.</small></p>
                )}
              </section>
            ) : null}

            <section className="trading-strategy-monitoring">
              <section><header><div><strong>Strategy activity</strong><small>Research, deterministic states, rejections, selection and orders</small></div><span>{events.length}</span></header><div className="strategy-event-list">{events.slice(0, 50).map((event) => <details key={`${event.event_id}-${event.observed_at}`} className={eventTone(event)}><summary><strong>{event.instrument_id.split(':').at(-1) ?? event.instrument_id}</strong><span>{event.event_type === 'research_llm' ? 'Premkt LLM' : event.event_type === 'intraday_llm' ? 'Intraday LLM' : event.event_type === 'intraday_llm_batch' ? 'LLM batch' : event.event_type === 'stoch_trend_capture' ? 'Stoch trend' : event.event_type === 'stoch_trend_capture_entry' ? 'Stoch entry' : event.event_type === 'stoch_trend_execution' ? 'Stoch execution' : event.event_type === 'stoch_trend_execution_summary' ? 'Stoch net' : event.event_type === 'ai_shadow_decision' ? `AI ${String(event.payload?.policy ?? '')} decision` : event.event_type === 'ai_shadow_fill' ? `AI ${String(event.payload?.policy ?? '')} fill` : event.event_type === 'ai_shadow_trade' ? `AI ${String(event.payload?.policy ?? '')} trade` : event.event_type === 'ai_shadow_batch' ? `AI ${String(event.payload?.policy ?? '')} batch` : event.event_type === 'ai_shadow_session_summary' ? `AI ${String(event.payload?.policy ?? '')} summary` : event.event_type === 'ai_shadow_data_gap' ? 'AI data gap' : event.event_type === 'shadow_strategy_comparison' ? 'A/B/C/D comparison' : event.state}</span><small>{event.reason_code ?? '—'}</small><time>{new Date(event.observed_at).toLocaleTimeString()}</time></summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>)}{!events.length ? <p>No strategy events yet.</p> : null}</div></section>
              <section><header><div><strong>Active protections</strong><small>Persisted stop / target state</small></div><span>{protections.length}</span></header><div className="strategy-protection-list">{protections.map((protection) => <article key={protection.protection_id}><strong>{protection.instrument_id.split(':').at(-1) ?? protection.instrument_id}</strong><span>{protection.status}</span><small>qty {String(protection.quantity)} · stop {String(protection.stop_price)} · target {String(protection.target_price)}</small></article>)}{!protections.length ? <p>No active strategy protections.</p> : null}</div></section>
            </section>
          </>
        )}
      </section>
    </div>
  );
}
