import { useEffect, useState } from 'react';
import { tradingStrategyApi, type StrategyRuntimeMonitorStatus, type TradingStrategyOperationsStatus } from './tradingStrategyApi';
import type { StrategyEvent } from './tradingStrategyTypes';
import './TradingStrategyIndicatorEvidence.css';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';

export type IndicatorSnapshotEvidence = {
  close: string | null;
  ema9: string | null;
  ema20: string | null;
  macd: string | null;
  macdSignal: string | null;
  macdHistogram: string | null;
  stochasticRsiK: string | null;
  stochasticRsiD: string | null;
};

export type ProspectiveFeatureEvidence = {
  fingerprint: string | null;
  allCoreAvailable: boolean;
  premarketAvailable: boolean;
  premarketRangePct: string | null;
  premarketCloseVsHighPct: string | null;
  premarketLast30mReturnPct: string | null;
  researchAvailable: boolean;
  catalystType: string | null;
  primaryCatalystConfirmed: boolean | null;
  supplyResolution: string | null;
  immediateSupplyRisk: boolean | null;
  haltHistoryComplete: boolean;
  haltEventCount: number;
  haltedAtDecision: boolean | null;
};

export type ProspectiveIndicatorEvidence = {
  eventId: string;
  instrumentId: string;
  observedAt: string;
  cutoff: string | null;
  source: string | null;
  partialMarket: boolean;
  barCount: number;
  fullWarmup: boolean;
  confirmed: boolean | null;
  reasonCodes: string[];
  error: string | null;
  oneMinute: IndicatorSnapshotEvidence;
  fiveMinute: IndicatorSnapshotEvidence;
  prospective: ProspectiveFeatureEvidence;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function numberValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function snapshot(value: unknown): IndicatorSnapshotEvidence {
  const item = record(value);
  return {
    close: text(item?.close),
    ema9: text(item?.ema9),
    ema20: text(item?.ema20),
    macd: text(item?.macd),
    macdSignal: text(item?.macd_signal),
    macdHistogram: text(item?.macd_histogram),
    stochasticRsiK: text(item?.stochastic_rsi_k),
    stochasticRsiD: text(item?.stochastic_rsi_d),
  };
}

function prospectiveFeatures(execution: Record<string, unknown>): ProspectiveFeatureEvidence {
  const features = record(execution.prospective_signal_features);
  const completeness = record(features?.completeness);
  const premarket = record(features?.premarket);
  const research = record(features?.research);
  const catalyst = record(research?.catalyst);
  const supply = record(research?.supply);
  const halt = record(features?.halt_history);
  return {
    fingerprint: text(features?.immutable_fingerprint),
    allCoreAvailable: booleanValue(completeness?.all_core_available) === true,
    premarketAvailable: booleanValue(completeness?.premarket_available) === true,
    premarketRangePct: text(premarket?.range_pct),
    premarketCloseVsHighPct: text(premarket?.close_vs_high_pct),
    premarketLast30mReturnPct: text(premarket?.last_30m_return_pct),
    researchAvailable: booleanValue(completeness?.research_available) === true,
    catalystType: text(catalyst?.catalyst_type),
    primaryCatalystConfirmed: booleanValue(catalyst?.primary_confirmed),
    supplyResolution: text(supply?.resolution_status),
    immediateSupplyRisk: booleanValue(supply?.immediate_supply_risk),
    haltHistoryComplete: booleanValue(completeness?.halt_history_complete) === true,
    haltEventCount: numberValue(halt?.halt_event_count),
    haltedAtDecision: booleanValue(halt?.halted_at_decision),
  };
}

export function collectProspectiveIndicatorEvidence(
  events: StrategyEvent[],
  limit = 12,
): ProspectiveIndicatorEvidence[] {
  const output: ProspectiveIndicatorEvidence[] = [];
  for (const event of events) {
    if (event.event_type !== 'shadow_execution') continue;
    const execution = record(event.payload.execution);
    if (!execution || !('indicator_entry_confirmed' in execution)) continue;
    const context = record(execution.indicator_context);
    const reasons = Array.isArray(execution.indicator_entry_reason_codes)
      ? execution.indicator_entry_reason_codes.filter((value): value is string => typeof value === 'string')
      : [];
    output.push({
      eventId: event.event_id,
      instrumentId: event.instrument_id,
      observedAt: event.observed_at,
      cutoff: text(execution.indicator_context_cutoff),
      source: text(execution.indicator_context_source),
      partialMarket: booleanValue(execution.indicator_context_partial_market) === true,
      barCount: numberValue(execution.indicator_context_bar_count),
      fullWarmup: booleanValue(execution.indicator_context_full_warmup) === true,
      confirmed: booleanValue(execution.indicator_entry_confirmed),
      reasonCodes: reasons,
      error: text(execution.indicator_context_error),
      oneMinute: snapshot(context?.one_minute),
      fiveMinute: snapshot(context?.five_minute),
      prospective: prospectiveFeatures(execution),
    });
    if (output.length >= Math.max(0, limit)) break;
  }
  return output;
}

function compactDecimal(value: string | null): string {
  if (value === null) return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  const magnitude = Math.abs(parsed);
  if (magnitude >= 100) return parsed.toFixed(2);
  if (magnitude >= 1) return parsed.toFixed(3);
  return parsed.toFixed(4);
}

function percent(value: string | null): string {
  return value === null ? '—' : `${compactDecimal(value)}%`;
}

function statusLabel(item: ProspectiveIndicatorEvidence): string {
  if (item.confirmed === true) return 'CONFIRMED';
  if (item.confirmed === false) return 'VETOED';
  return 'UNAVAILABLE';
}

function statusTone(item: ProspectiveIndicatorEvidence): string {
  if (item.confirmed === true) return 'confirmed';
  if (item.confirmed === false) return 'vetoed';
  return 'unavailable';
}

function runtimeTone(status: StrategyRuntimeMonitorStatus): string {
  if (!status.configured_enabled) return 'disabled';
  if (!status.registered || !status.running) return 'stopped';
  return 'running';
}

function runtimeLabel(status: StrategyRuntimeMonitorStatus): string {
  if (!status.configured_enabled) return 'DISABLED';
  if (!status.registered) return 'NOT REGISTERED';
  return status.running ? 'RUNNING' : 'STOPPED';
}

function counterSummary(status: StrategyRuntimeMonitorStatus): string {
  const entries = Object.entries(status.counters);
  if (!entries.length) return 'no counters';
  return entries.map(([key, value]) => `${key.replaceAll('_', ' ')} ${value}`).join(' · ');
}

function RuntimeMonitor({
  label,
  purpose,
  status,
}: {
  label: string;
  purpose: string;
  status: StrategyRuntimeMonitorStatus;
}) {
  return (
    <article className="indicator-runtime-monitor" data-status={runtimeTone(status)}>
      <header><strong>{label}</strong><span>{runtimeLabel(status)}</span></header>
      <small>{purpose}</small>
      <div>
        <span>{status.interval_seconds == null ? 'interval —' : `every ${status.interval_seconds}s`}</span>
        <span>{status.last_run_at ? `last run ${new Date(status.last_run_at).toLocaleTimeString()}` : 'no completed run yet'}</span>
      </div>
      <small>{counterSummary(status)}</small>
      {status.last_error ? <p title={status.last_error}>Last reported error: {status.last_error}</p> : null}
    </article>
  );
}

function IndicatorValues({ label, value }: { label: string; value: IndicatorSnapshotEvidence }) {
  return (
    <div className="indicator-evidence-timeframe">
      <strong>{label}</strong>
      <span>Close <b>{compactDecimal(value.close)}</b></span>
      <span>EMA9 <b>{compactDecimal(value.ema9)}</b></span>
      <span>EMA20 <b>{compactDecimal(value.ema20)}</b></span>
      <span>MACD <b>{compactDecimal(value.macd)}</b></span>
      <span>Signal <b>{compactDecimal(value.macdSignal)}</b></span>
      <span>Hist <b>{compactDecimal(value.macdHistogram)}</b></span>
      <span>Stoch K <b>{compactDecimal(value.stochasticRsiK)}</b></span>
      <span>Stoch D <b>{compactDecimal(value.stochasticRsiD)}</b></span>
    </div>
  );
}

function ProspectiveFeatures({ value }: { value: ProspectiveFeatureEvidence }) {
  const catalyst = value.catalystType ?? (value.researchAvailable ? 'unknown catalyst' : 'research unavailable');
  const supply = value.supplyResolution ?? (value.researchAvailable ? 'supply unresolved' : 'research unavailable');
  const halt = value.haltHistoryComplete
    ? `${value.haltEventCount} halt event${value.haltEventCount === 1 ? '' : 's'}`
    : 'halt history incomplete';
  return (
    <div role="group" className="indicator-evidence-meta" aria-label="Prospective win-rate feature coverage">
      <span className={value.allCoreAvailable ? 'pass' : 'warn'}>{value.allCoreAvailable ? 'All core features captured' : 'Partial prospective features'}</span>
      <span>{value.premarketAvailable ? `PM range ${percent(value.premarketRangePct)} · vs high ${percent(value.premarketCloseVsHighPct)}` : 'PM structure unavailable'}</span>
      <span>{value.premarketAvailable ? `PM last 30m ${percent(value.premarketLast30mReturnPct)}` : 'PM trend unavailable'}</span>
      <span>{catalyst}{value.primaryCatalystConfirmed === true ? ' · primary confirmed' : ''}</span>
      <span>{supply}{value.immediateSupplyRisk === true ? ' · immediate risk' : ''}</span>
      <span>{halt}{value.haltedAtDecision === true ? ' · HALTED' : ''}</span>
    </div>
  );
}

export function TradingStrategyIndicatorEvidence({
  events,
  visible,
  loadError = null,
}: {
  events: StrategyEvent[];
  visible: boolean;
  loadError?: string | null;
}) {
  const [operations, setOperations] = useState<TradingStrategyOperationsStatus | null>(null);
  const [operationsError, setOperationsError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) {
      setOperations(null);
      setOperationsError(null);
      return;
    }
    let alive = true;
    const refreshOperations = async () => {
      try {
        const next = await tradingStrategyApi.operationsStatus();
        if (!alive) return;
        setOperations(next);
        setOperationsError(null);
      } catch (error) {
        if (!alive) return;
        setOperationsError(error instanceof Error ? error.message : String(error));
      }
    };
    void refreshOperations();
    const stopPolling = startPolling(refreshOperations, POLL_INTERVALS_MS.strategyOperations);
    return () => {
      alive = false;
      stopPolling();
    };
  }, [visible]);

  if (!visible) return null;
  const evidence = collectProspectiveIndicatorEvidence(events);
  return (
    <section className="trading-indicator-evidence" aria-label="Prospective indicator entry evidence">
      <header>
        <div>
          <strong>Prospective indicator & win-rate evidence</strong>
          <small>Research only · PM/catalyst/supply/halt/MTF capture · no AUTO PAPER authority · Alpaca IEX partial-market evidence</small>
        </div>
        <span>{evidence.length} signals</span>
      </header>
      {operations ? (
        <div role="group" className="indicator-runtime-grid" aria-label="Prospective capture runtime">
          <RuntimeMonitor label="Morning archive" purpose="09:20 ET immutable raw universe capture" status={operations.universe_archive_monitor} />
          <RuntimeMonitor label="SHADOW evaluator" purpose="Deterministic structural/live execution observation" status={operations.strategy_monitor} />
          <RuntimeMonitor label="Post-session replay" purpose="Canonical V2 replay and qualification evidence" status={operations.v2_qualification_monitor} />
        </div>
      ) : operationsError ? (
        <p className="indicator-evidence-error">Could not load prospective runtime status: {operationsError}</p>
      ) : (
        <p className="indicator-runtime-loading">Checking prospective capture monitors…</p>
      )}
      {evidence.length ? (
        <div className="indicator-evidence-list">
          {evidence.map((item) => (
            <article key={item.eventId} data-status={statusTone(item)}>
              <header>
                <div>
                  <strong>{item.instrumentId.split(':').at(-1) ?? item.instrumentId}</strong>
                  <small>{new Date(item.observedAt).toLocaleTimeString()} · cutoff {item.cutoff ? new Date(item.cutoff).toLocaleTimeString() : '—'}</small>
                </div>
                <span className="indicator-evidence-status">{statusLabel(item)}</span>
              </header>
              <div className="indicator-evidence-meta">
                <span className={item.fullWarmup ? 'pass' : 'warn'}>{item.fullWarmup ? 'Full 1m/5m warm-up' : 'Partial warm-up'}</span>
                <span>{item.barCount} finalized 1m bars</span>
                <span>{item.source ?? 'unknown source'}{item.partialMarket ? ' · partial market' : ''}</span>
                {item.prospective.fingerprint ? <span title={item.prospective.fingerprint}>feature fp {item.prospective.fingerprint.slice(0, 10)}…</span> : null}
              </div>
              <ProspectiveFeatures value={item.prospective} />
              {item.error ? <p className="indicator-evidence-error">{item.error}</p> : null}
              {!item.error && item.reasonCodes.length ? (
                <p className="indicator-evidence-reasons">Veto: {item.reasonCodes.join(' · ')}</p>
              ) : null}
              <div className="indicator-evidence-timeframes">
                <IndicatorValues label="1 minute" value={item.oneMinute} />
                <IndicatorValues label="5 minute" value={item.fiveMinute} />
              </div>
            </article>
          ))}
        </div>
      ) : loadError ? (
        <p className="indicator-evidence-error">Could not load persisted SHADOW indicator evidence: {loadError}</p>
      ) : (
        <p className="indicator-evidence-empty">Waiting for prospective Aug 24+ V2 SHADOW structural signals. No indicator or win-rate feature result is inferred before a signal exists.</p>
      )}
    </section>
  );
}
