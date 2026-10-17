import { useEffect, useMemo, useState } from 'react';
import { tradingStrategyApi } from './tradingStrategyApi';
import { tradingStrategyOperationsApi, type TradingOperationalHealth } from './tradingStrategyOperationsApi';
import type {
  GapperCandidate,
  GapperUniverse,
  StrategyEvent,
  StrategyProtection,
  TradingStrategyConfig,
} from './tradingStrategyTypes';
import './TradingSymbolIntelligence.css';

type SymbolIntelligenceSnapshot = {
  strategy: TradingStrategyConfig | null;
  universe: GapperUniverse | null;
  candidate: GapperCandidate | null;
  events: StrategyEvent[];
  protection: StrategyProtection | null;
  health: TradingOperationalHealth | null;
};

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function num(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
}

function pct(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}%` : '—';
}

function price(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—';
}

function shortId(value: unknown): string {
  const text = typeof value === 'string' ? value : '';
  if (!text) return '—';
  return text.length > 19 ? `${text.slice(0, 10)}…${text.slice(-6)}` : text;
}

function time(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function label(value: string | null | undefined): string {
  return value ? value.toLowerCase().replaceAll('_', ' ') : '—';
}

function symbol(instrumentId: string): string {
  return instrumentId.split(':').at(-1)?.replace('-', '/') ?? instrumentId;
}

function eventTime(event: StrategyEvent): number {
  const parsed = new Date(event.observed_at).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function findCandidate(universe: GapperUniverse, instrumentId: string): GapperCandidate | null {
  return universe.candidates.find((candidate) => candidate.instrument_id === instrumentId) ?? null;
}

function statusTone(state: string | null | undefined): 'healthy' | 'degraded' | 'blocked' | 'unknown' {
  if (state === 'healthy') return 'healthy';
  if (state === 'degraded') return 'degraded';
  if (state === 'blocked') return 'blocked';
  return 'unknown';
}

function Metric({ label: metricLabel, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="symbol-intel-metric">
      <small>{metricLabel}</small>
      <strong>{value}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

export function TradingSymbolIntelligence({
  instrumentId,
  bindingId,
  accountId,
}: {
  instrumentId: string;
  bindingId: string | null;
  accountId: string | null | undefined;
}) {
  const [snapshot, setSnapshot] = useState<SymbolIntelligenceSnapshot>({
    strategy: null,
    universe: null,
    candidate: null,
    events: [],
    protection: null,
    health: null,
  });
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId || !instrumentId) {
      setSnapshot({ strategy: null, universe: null, candidate: null, events: [], protection: null, health: null });
      setStatus('idle');
      setError(null);
      return;
    }

    let alive = true;
    const load = async () => {
      setStatus('loading');
      setError(null);
      try {
        const strategies = (await tradingStrategyApi.list())
          .filter((strategy) => strategy.account_id === accountId && !strategy.archived_at);
        const activeStrategies = strategies.filter((strategy) => strategy.active_universe_id);
        const contexts = await Promise.all(activeStrategies.map(async (strategy) => {
          try {
            const universe = await tradingStrategyApi.universe(strategy.active_universe_id!);
            return { strategy, universe, candidate: findCandidate(universe, instrumentId) };
          } catch {
            return { strategy, universe: null, candidate: null };
          }
        }));
        const matched = contexts.find((context) => context.candidate)
          ?? contexts.find((context) => context.strategy.enabled && context.strategy.mode !== 'off')
          ?? contexts[0]
          ?? null;
        const selectedStrategy = matched?.strategy
          ?? strategies.find((strategy) => strategy.enabled && strategy.mode !== 'off')
          ?? strategies[0]
          ?? null;
        const selectedUniverse = matched?.universe ?? null;
        const candidate = matched?.candidate ?? null;

        const [events, protections, health] = await Promise.all([
          selectedStrategy ? tradingStrategyApi.events(selectedStrategy.strategy_id, 500) : Promise.resolve([]),
          selectedStrategy ? tradingStrategyApi.protections(selectedStrategy.strategy_id) : Promise.resolve([]),
          tradingStrategyOperationsApi.health(accountId, { instrumentId, bindingId }),
        ]);
        if (!alive) return;
        const symbolEvents = events
          .filter((event) => event.instrument_id === instrumentId)
          .sort((left, right) => eventTime(right) - eventTime(left));
        const protection = protections.find((item) => item.instrument_id === instrumentId) ?? null;
        setSnapshot({
          strategy: selectedStrategy,
          universe: selectedUniverse,
          candidate,
          events: symbolEvents,
          protection,
          health,
        });
        setStatus('ready');
      } catch (reason) {
        if (!alive) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setStatus('error');
      }
    };
    void load();
    return () => { alive = false; };
  }, [accountId, bindingId, instrumentId]);

  const latestEvent = snapshot.events[0] ?? null;
  const eventPayload = asRecord(latestEvent?.payload);
  const features = asRecord(eventPayload.features);
  const execution = snapshot.health?.execution;
  const protection = snapshot.protection;
  const candidate = snapshot.candidate;
  const strategy = snapshot.strategy;
  const correlation = useMemo(() => ({
    sessionId: eventPayload.session_id,
    setupId: eventPayload.setup_id,
    intentId: eventPayload.trade_intent_id,
    riskDecisionId: eventPayload.risk_decision_id,
    strategyRevision: eventPayload.strategy_revision,
  }), [eventPayload]);
  const catalystIds = candidate?.catalyst_evidence_ids ?? [];
  const dilutionFlags = candidate?.dilution_flags ?? [];
  const executionReasons = execution?.reason_codes ?? [];
  const recentEvents = snapshot.events.slice(0, 8);

  if (!accountId) {
    return (
      <section className="trading-symbol-intelligence empty" aria-label="Symbol Intelligence">
        <strong>Symbol Intelligence</strong>
        <span>Select a paper account in the Trade tab to correlate strategy, risk and execution evidence for {symbol(instrumentId)}.</span>
      </section>
    );
  }

  return (
    <section className="trading-symbol-intelligence" aria-label="Symbol Intelligence" data-status={status}>
      <header className="symbol-intel-header">
        <div>
          <strong>{symbol(instrumentId)} · Symbol Intelligence</strong>
          <small>Frozen discovery evidence, deterministic state, protection and execution eligibility</small>
        </div>
        <span className={`symbol-intel-health tone-${statusTone(execution?.state)}`}>
          {status === 'loading' ? 'REFRESHING' : execution?.state?.toUpperCase() ?? 'UNKNOWN'}
        </span>
      </header>

      {error ? <div className="symbol-intel-error" role="alert">Symbol Intelligence unavailable: {error}</div> : null}

      <div className="symbol-intel-section">
        <header><strong>Strategy state</strong><span>{strategy ? `${strategy.mode.toUpperCase()} · v${strategy.strategy_version}` : 'No strategy'}</span></header>
        <div className="symbol-intel-grid compact">
          <Metric label="Current state" value={label(latestEvent?.state)} detail={latestEvent ? time(latestEvent.observed_at) : 'No symbol events'} />
          <Metric label="Reason" value={label(latestEvent?.reason_code)} detail={latestEvent?.event_type ? label(latestEvent.event_type) : '—'} />
          <Metric label="Quality" value={num(features.quality_score, 0)} detail={`Spread ${features.spread_bps == null ? '—' : `${num(features.spread_bps)} bps`}`} />
          <Metric label="Strategy revision" value={String(correlation.strategyRevision ?? strategy?.revision ?? '—')} detail={strategy?.risk.kill_switch ? 'Kill switch ON' : 'Kill switch off'} />
        </div>
      </div>

      <div className="symbol-intel-section">
        <header><strong>Frozen discovery</strong><span>{snapshot.universe ? snapshot.universe.session_date : 'No matched universe'}</span></header>
        <div className="symbol-intel-grid">
          <Metric label="Gap" value={pct(candidate?.gap_pct)} detail={`Rank ${candidate?.discovery_rank ?? '—'}`} />
          <Metric label="Premarket price" value={price(candidate?.premarket_price)} detail={`Previous ${price(candidate?.previous_close)}`} />
          <Metric label="TOD RVOL" value={num(candidate?.tod_rvol)} detail={`Premarket $vol ${num(candidate?.premarket_dollar_volume, 0)}`} />
          <Metric
            label="Data integrity"
            value={candidate ? (candidate.market_data_complete === false ? 'Incomplete' : 'Complete') : '—'}
            detail={candidate?.data_quality_flags?.length ? candidate.data_quality_flags.map(label).join(' · ') : `Premarket bars ${candidate?.premarket_bar_count ?? '—'}`}
          />
          <Metric label="Float" value={num(candidate?.float_shares, 0)} detail={`Observed ${time(candidate?.observed_at ?? null)}`} />
        </div>
      </div>

      <div className="symbol-intel-section">
        <header><strong>Failed-selloff structure</strong><span>Deterministic event features</span></header>
        <div className="symbol-intel-grid">
          <Metric label="L1" value={price(features.l1)} detail={`Impulse ${pct(features.opening_impulse_pct)}`} />
          <Metric label="B1" value={price(features.b1)} detail={`Recovery ${pct(features.v2_recovery_pct ?? features.recovery_pct)}`} />
          <Metric label="L2" value={price(features.l2)} detail={`2nd pullback ${pct(features.second_pullback_depth_pct)}`} />
          <Metric label="VWAP" value={price(features.session_vwap)} detail={`Breakout vol ${num(features.breakout_volume_ratio)}`} />
        </div>
      </div>

      <div className="symbol-intel-section">
        <header><strong>Execution &amp; protection</strong><span>Server-authoritative paper evidence</span></header>
        <div className="symbol-intel-grid">
          <Metric label="Eligibility" value={execution?.execution_eligible ? 'Eligible' : 'Blocked'} detail={execution?.provider ?? 'Provider —'} />
          <Metric label="Spread" value={execution?.spread_bps == null ? '—' : `${num(execution.spread_bps)} bps`} detail={`${execution?.freshness_mode ?? 'freshness —'} · ${execution?.session ?? 'session —'}`} />
          <Metric label="Observation age" value={execution?.observation_age_ms == null ? '—' : `${num(execution.observation_age_ms, 0)} ms`} detail={execution?.source_time ? time(execution.source_time) : 'No source time'} />
          <Metric label="Protection" value={protection?.status ? label(protection.status) : 'None'} detail={protection ? `Stop ${price(protection.stop_price)} · target ${price(protection.target_price)}` : 'No active protection'} />
        </div>
        {executionReasons.length ? <div className="symbol-intel-reasons">{executionReasons.map((reason) => <span key={reason}>{label(reason)}</span>)}</div> : null}
      </div>

      <div className="symbol-intel-section">
        <header><strong>Catalyst &amp; supply</strong><span>Frozen evidence only</span></header>
        <div className="symbol-intel-evidence">
          <div><small>Catalyst evidence</small><strong>{catalystIds.length}</strong><span>{catalystIds.length ? catalystIds.map(shortId).join(' · ') : 'No catalyst evidence IDs on frozen candidate'}</span></div>
          <div><small>Dilution flags</small><strong>{dilutionFlags.length}</strong><span>{dilutionFlags.length ? dilutionFlags.map(label).join(' · ') : 'No active dilution flags on frozen candidate'}</span></div>
        </div>
      </div>

      <div className="symbol-intel-section">
        <header><strong>Lifecycle correlation</strong><span>trade-lifecycle-v1</span></header>
        <div className="symbol-intel-identities">
          <div><small>Session</small><code title={String(correlation.sessionId ?? '')}>{shortId(correlation.sessionId)}</code></div>
          <div><small>Setup</small><code title={String(correlation.setupId ?? '')}>{shortId(correlation.setupId)}</code></div>
          <div><small>Intent</small><code title={String(correlation.intentId ?? '')}>{shortId(correlation.intentId)}</code></div>
          <div><small>Risk decision</small><code title={String(correlation.riskDecisionId ?? '')}>{shortId(correlation.riskDecisionId)}</code></div>
        </div>
      </div>

      <div className="symbol-intel-section timeline">
        <header><strong>Recent deterministic events</strong><span>{snapshot.events.length} symbol events loaded</span></header>
        {recentEvents.length ? (
          <div className="symbol-intel-timeline">
            {recentEvents.map((event) => (
              <div key={event.event_id}>
                <time>{time(event.observed_at)}</time>
                <span><strong>{label(event.event_type)}</strong><small>{label(event.state)}{event.reason_code ? ` · ${label(event.reason_code)}` : ''}</small></span>
              </div>
            ))}
          </div>
        ) : <div className="symbol-intel-empty-row">No deterministic strategy events for this symbol yet.</div>}
      </div>

      <footer>
        Read-only intelligence. This panel cannot authorize or place orders; live broker and AI execution remain disabled.
      </footer>
    </section>
  );
}
