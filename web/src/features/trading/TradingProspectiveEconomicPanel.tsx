/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { tradingStrategyApi, type TradingStrategyOperationsStatus } from './tradingStrategyApi';
import type {
  ProspectiveEconomicHoldoutReviewInput,
  ProspectiveEconomicStatus,
  StrategyEvent,
  TradingStrategyConfig,
} from './tradingStrategyTypes';
import './TradingProspectiveEconomicPanel.css';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';

function numeric(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function pct(value: string | number | null | undefined): string {
  const parsed = numeric(value);
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`;
}

function r(value: string | number | null | undefined): string {
  const parsed = numeric(value);
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}${parsed.toFixed(3)}R`;
}

function progress(value: number, required: number): number {
  if (required <= 0) return 0;
  return Math.max(0, Math.min(100, value / required * 100));
}

function statusTone(done: boolean, failed = false): 'done' | 'failed' | 'pending' {
  if (failed) return 'failed';
  return done ? 'done' : 'pending';
}

function symbol(instrumentId: string): string {
  return instrumentId.split(':').at(-1) ?? instrumentId;
}

function shortTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="prospective-economic-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

function ProgressMetric({
  label,
  value,
  required,
}: {
  label: string;
  value: number;
  required: number;
}) {
  return (
    <div className="prospective-economic-progress">
      <div><span>{label}</span><strong>{value} / {required}</strong></div>
      <progress max={100} value={progress(value, required)} />
    </div>
  );
}

function Step({
  index,
  title,
  subtitle,
  tone,
  children,
}: {
  index: number;
  title: string;
  subtitle: string;
  tone: 'done' | 'failed' | 'pending';
  children?: ReactNode;
}) {
  return (
    <section className={`prospective-economic-step ${tone}`}>
      <header>
        <span className="prospective-economic-step-number">{index}</span>
        <div><strong>{title}</strong><small>{subtitle}</small></div>
        <span className="prospective-economic-step-state">{tone === 'done' ? 'PASS' : tone === 'failed' ? 'STOP' : 'WAIT'}</span>
      </header>
      {children ? <div className="prospective-economic-step-body">{children}</div> : null}
    </section>
  );
}

const emptyHoldout = (): ProspectiveEconomicHoldoutReviewInput => ({
  trade_count: 0,
  win_rate: '',
  expectancy_r: '',
  one_sided_90_lcb_r: null,
  max_drawdown_r: '',
  artifact_ref: '',
  review_note: '',
});

export function TradingProspectiveEconomicPanel() {
  const [strategies, setStrategies] = useState<TradingStrategyConfig[]>([]);
  const [strategyId, setStrategyId] = useState('');
  const [status, setStatus] = useState<ProspectiveEconomicStatus | null>(null);
  const [events, setEvents] = useState<StrategyEvent[]>([]);
  const [operations, setOperations] = useState<TradingStrategyOperationsStatus | null>(null);
  const [evaluationNote, setEvaluationNote] = useState('');
  const [holdout, setHoldout] = useState<ProspectiveEconomicHoldoutReviewInput>(emptyHoldout);
  const [autoPaperNote, setAutoPaperNote] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const v2Strategies = useMemo(
    () => strategies.filter((strategy) => strategy.config.strategy_version === '2.0.0' && !strategy.archived_at),
    [strategies],
  );

  const refreshStatus = async (nextId = strategyId) => {
    if (!nextId) {
      setStatus(null);
      setEvents([]);
      return;
    }
    const [nextStatus, nextEvents, nextOperations] = await Promise.all([
      tradingStrategyApi.prospectiveEconomic(nextId),
      tradingStrategyApi.prospectiveEconomicEvents(nextId, 500),
      tradingStrategyApi.operationsStatus(),
    ]);
    setStatus(nextStatus);
    setEvents(nextEvents);
    setOperations(nextOperations);
  };

  useEffect(() => {
    let alive = true;
    void Promise.all([tradingStrategyApi.list(), tradingStrategyApi.operationsStatus()])
      .then(([rows, nextOperations]) => {
        if (!alive) return;
        const eligible = rows.filter((strategy) => strategy.config.strategy_version === '2.0.0' && !strategy.archived_at);
        setStrategies(rows);
        setOperations(nextOperations);
        const nextId = eligible[0]?.strategy_id ?? '';
        setStrategyId(nextId);
        if (nextId) return refreshStatus(nextId);
        return undefined;
      })
      .catch((error) => { if (alive) setNotice(error instanceof Error ? error.message : String(error)); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!strategyId) return;
    return startPolling(() => refreshStatus(strategyId), POLL_INTERVALS_MS.strategyOperations);
  }, [strategyId]);

  const runAction = async (action: () => Promise<ProspectiveEconomicStatus>) => {
    if (!strategyId) return;
    setBusy(true);
    setNotice(null);
    try {
      const next = await action();
      setStatus(next);
      setEvents(await tradingStrategyApi.prospectiveEconomicEvents(strategyId, 500));
      setOperations(await tradingStrategyApi.operationsStatus());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const selectStrategy = (nextId: string) => {
    setStrategyId(nextId);
    setStatus(null);
    setEvents([]);
    setEvaluationNote('');
    setHoldout(emptyHoldout());
    setAutoPaperNote('');
    void refreshStatus(nextId).catch((error) => setNotice(error instanceof Error ? error.message : String(error)));
  };

  const monitor = operations?.prospective_economic_monitor;
  const metrics = status?.metrics;
  const thresholds = status?.thresholds;
  const evaluationFailed = Boolean(status?.evaluation_recorded && !status.evaluation_passed);
  const holdoutFailed = Boolean(status?.holdout_reviewed && !['ROBUST', 'GOLD'].includes(status.holdout_verdict));
  const candidateTransitions = events.filter((event) => event.event_type === 'prospective_economic_candidate').length;
  const recent = events.slice(0, 18);

  return (
    <div className="prospective-economic-panel">
      <header className="prospective-economic-hero">
        <div>
          <strong>Prospective economic evidence</strong>
          <small>Frozen Aug 24, 2026 · +1R before −1R within 60 full 1m bars · SHADOW only</small>
        </div>
        <span className={monitor?.running ? 'monitor-live' : 'monitor-off'}>{monitor?.running ? 'RECORDER LIVE' : 'RECORDER OFF'}</span>
      </header>

      <label className="prospective-economic-strategy-select">
        <span>V2 strategy</span>
        <select value={strategyId} onChange={(event) => selectStrategy(event.target.value)}>
          {v2Strategies.length === 0 ? <option value="">No active V2 strategies</option> : null}
          {v2Strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.strategy_id}</option>)}
        </select>
      </label>

      {notice ? <div className="prospective-economic-notice">{notice}</div> : null}
      {!status || !metrics || !thresholds ? (
        <p className="prospective-economic-empty">{strategyId ? 'Loading frozen evidence state…' : 'Create or select a V2 SHADOW strategy to begin prospective collection.'}</p>
      ) : (
        <>
          <div className="prospective-economic-summary">
            <Metric label="Economic win" value={pct(metrics.win_rate)} hint={`min ${pct(thresholds.minimum_win_rate)}`} />
            <Metric label="Expectancy" value={r(metrics.expectancy_r)} hint={`min ${r(thresholds.minimum_expectancy_r)}`} />
            <Metric label="90% LCB" value={r(metrics.one_sided_90_lcb_r)} hint="must be > 0R" />
            <Metric label="Drawdown" value={r(metrics.max_drawdown_r)} hint={`max ${r(thresholds.maximum_drawdown_r)}`} />
          </div>

          <Step
            index={1}
            title="Collect prospective economic signals"
            subtitle="All causal candidate states retained diagnostically; only frozen signals/outcomes score promotion"
            tone={status.sample_ready ? 'done' : 'pending'}
          >
            <ProgressMetric label="Matched outcomes" value={metrics.matched_outcome_count} required={thresholds.minimum_matched_outcomes} />
            <ProgressMetric label="Distinct sessions" value={metrics.distinct_sessions} required={thresholds.minimum_distinct_sessions} />
            <ProgressMetric label="Distinct symbols" value={metrics.distinct_symbols} required={thresholds.minimum_distinct_symbols} />
            <Metric label="Evidence completion" value={pct(metrics.execution_match_rate)} hint={`min ${pct(thresholds.minimum_execution_match_rate)}`} />
            <p><small>Candidate transitions {candidateTransitions} loaded · signals {metrics.signal_count} · executable {metrics.matched_signal_count} · completed outcomes {metrics.matched_outcome_count}. Coverage is completed outcomes / all frozen signals, so ineligible, incomplete and pending outcomes stay in the denominator. Candidate diagnostics never enter promotion scoring.</small></p>
          </Step>

          <Step
            index={2}
            title="Evaluate exactly once"
            subtitle="The first eligible evaluation freezes PASS or FAIL for this profile"
            tone={status.evaluation_recorded ? statusTone(status.evaluation_passed, evaluationFailed) : 'pending'}
          >
            <p><strong>{status.evaluation_recorded ? (status.evaluation_passed ? 'Frozen PASS' : 'Frozen FAIL — profile retired') : status.sample_ready ? (status.quantitative_pass ? 'Sample ready and quantitative floors currently pass' : 'Sample ready; current metrics fail one or more floors') : 'Collection floor not reached'}</strong></p>
            {!status.evaluation_recorded && status.sample_ready ? (
              <div className="prospective-economic-action">
                <textarea value={evaluationNote} onChange={(event) => setEvaluationNote(event.target.value)} placeholder="Record why this exact prospective sample is being opened for its one-shot evaluation." />
                <button type="button" disabled={busy || evaluationNote.trim().length < 10} onClick={() => void runAction(() => tradingStrategyApi.evaluateProspectiveEconomic(strategyId, evaluationNote))}>Freeze one-shot evaluation</button>
              </div>
            ) : null}
          </Step>

          <Step
            index={3}
            title="Open sealed historical holdout"
            subtitle={`${thresholds.holdout_start} → ${thresholds.holdout_end}; inaccessible until the one-shot prospective gate passes`}
            tone={status.holdout_reviewed ? statusTone(['ROBUST', 'GOLD'].includes(status.holdout_verdict), holdoutFailed) : 'pending'}
          >
            {!status.sealed_holdout_unlocked ? <p><strong>SEALED</strong> · no historical result should be inspected or entered for this profile.</p> : null}
            {status.sealed_holdout_unlocked && !status.holdout_reviewed ? (
              <div className="prospective-economic-form-grid">
                <label><span>Trades</span><input type="number" min="0" value={holdout.trade_count} onChange={(event) => setHoldout({ ...holdout, trade_count: Number(event.target.value) })} /></label>
                <label><span>Win rate (0–1)</span><input type="number" min="0" max="1" step="0.01" value={String(holdout.win_rate)} onChange={(event) => setHoldout({ ...holdout, win_rate: event.target.value })} /></label>
                <label><span>Expectancy R</span><input type="number" step="0.01" value={String(holdout.expectancy_r)} onChange={(event) => setHoldout({ ...holdout, expectancy_r: event.target.value })} /></label>
                <label><span>90% LCB R</span><input type="number" step="0.01" value={holdout.one_sided_90_lcb_r == null ? '' : String(holdout.one_sided_90_lcb_r)} onChange={(event) => setHoldout({ ...holdout, one_sided_90_lcb_r: event.target.value || null })} /></label>
                <label><span>Max DD R</span><input type="number" min="0" step="0.01" value={String(holdout.max_drawdown_r)} onChange={(event) => setHoldout({ ...holdout, max_drawdown_r: event.target.value })} /></label>
                <label className="wide"><span>Artifact/run reference</span><input value={holdout.artifact_ref} onChange={(event) => setHoldout({ ...holdout, artifact_ref: event.target.value })} placeholder="GitHub run + artifact/digest" /></label>
                <label className="wide"><span>Review note</span><textarea value={holdout.review_note} onChange={(event) => setHoldout({ ...holdout, review_note: event.target.value })} placeholder="Confirm the sealed date block was opened only after the prospective PASS and no parameters were changed." /></label>
                <button className="wide" type="button" disabled={busy || holdout.artifact_ref.trim().length < 8 || holdout.review_note.trim().length < 10 || holdout.win_rate === '' || holdout.expectancy_r === '' || holdout.max_drawdown_r === ''} onClick={() => void runAction(() => tradingStrategyApi.reviewProspectiveEconomicHoldout(strategyId, holdout))}>Record sealed holdout verdict</button>
              </div>
            ) : null}
            {status.holdout_reviewed ? <p><strong>Holdout verdict: {status.holdout_verdict}</strong></p> : null}
          </Step>

          <Step
            index={4}
            title="Prospective SHADOW soak"
            subtitle="Fresh evidence after a successful sealed-holdout review; no order authority"
            tone={status.soak_passed ? 'done' : holdoutFailed ? 'failed' : 'pending'}
          >
            <ProgressMetric label="Post-holdout outcomes" value={status.soak_metrics.matched_outcome_count} required={thresholds.soak_minimum_matched_outcomes} />
            <ProgressMetric label="Post-holdout sessions" value={status.soak_metrics.distinct_sessions} required={thresholds.soak_minimum_distinct_sessions} />
            <ProgressMetric label="Post-holdout symbols" value={status.soak_metrics.distinct_symbols} required={thresholds.soak_minimum_distinct_symbols} />
            <div className="prospective-economic-inline-metrics">
              <Metric label="Coverage" value={pct(status.soak_metrics.execution_match_rate)} />
              <Metric label="Win" value={pct(status.soak_metrics.win_rate)} />
              <Metric label="Exp" value={r(status.soak_metrics.expectancy_r)} />
              <Metric label="DD" value={r(status.soak_metrics.max_drawdown_r)} />
            </div>
          </Step>

          <Step
            index={5}
            title="AUTO PAPER review"
            subtitle="Final human review binds the entire evidence chain; V2 qualification must still pass independently"
            tone={status.auto_paper_research_authorized ? 'done' : holdoutFailed || evaluationFailed ? 'failed' : 'pending'}
          >
            {status.auto_paper_reviewed ? (
              <p><strong>Economic research gate reviewed.</strong> AUTO PAPER still remains subject to the server-side V2 prospective qualification.</p>
            ) : status.soak_passed ? (
              <div className="prospective-economic-action">
                <textarea value={autoPaperNote} onChange={(event) => setAutoPaperNote(event.target.value)} placeholder="Review the prospective sample, sealed holdout and fresh SHADOW soak before binding the final research approval." />
                <button type="button" disabled={busy || autoPaperNote.trim().length < 10} onClick={() => void runAction(() => tradingStrategyApi.reviewProspectiveEconomicAutoPaper(strategyId, autoPaperNote))}>Approve exact evidence chain</button>
              </div>
            ) : <p>Final review remains locked until the prior stages pass.</p>}
          </Step>

          <section className="prospective-economic-fingerprints">
            <strong>Frozen audit identity</strong>
            <small>Profile <code>{status.profile_fingerprint}</code></small>
            <small>Collection evidence <code>{status.evidence_fingerprint}</code></small>
            <small>Pipeline <code>{status.pipeline_evidence_fingerprint}</code></small>
            {status.reason_codes.length ? <small>Blocking: {status.reason_codes.join(' · ')}</small> : <small>No economic-pipeline blockers.</small>}
          </section>

          <section className="prospective-economic-events">
            <header><strong>Recent prospective evidence</strong><small>{events.length} loaded</small></header>
            {recent.length === 0 ? <p>No prospective events yet.</p> : recent.map((event) => (
              <article key={event.event_id}>
                <div><strong>{symbol(event.instrument_id)}</strong><span>{event.event_type.replaceAll('_', ' ')}</span></div>
                <small>{shortTime(event.observed_at)} · {event.reason_code || event.state}</small>
              </article>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
