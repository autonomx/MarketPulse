/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { tradingHermesResearchApi } from './tradingHermesResearchApi';
import type { HermesResearchAudit, HermesResearchValidation, ResearchRecommendation } from './tradingHermesResearchApi';
import { tradingStrategyApi } from './tradingStrategyApi';
import type { GapperUniverse, TradingStrategyConfig } from './tradingStrategyTypes';
import './TradingHermesResearchPanel.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

const RECOMMENDATION_LEVELS: ResearchRecommendation[] = ['observe_only', 'score_only', 'soft_gate', 'hard_gate'];

const COVERAGE_LABELS: Array<[keyof NonNullable<HermesResearchAudit['latest_report']>['coverage'], string]> = [
  ['sec', 'SEC checked'],
  ['company_ir', 'Company IR checked'],
  ['recent_news', 'Recent news checked'],
  ['prior_news_novelty', 'Prior-news novelty'],
  ['atm', 'ATM status'],
  ['warrants', 'Warrant status'],
  ['resale_registration', 'Resale registration'],
  ['convertibles', 'Convertible exposure'],
];

function stamp(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function statusMark(value: string): string {
  if (value === 'complete') return '✓';
  if (value === 'failed') return '✕';
  if (value === 'unresolved') return '?';
  return '·';
}

function statusTone(value: string): string {
  if (value === 'complete' || value === 'clear' || value === 'confirmed') return 'complete';
  if (value === 'failed' || value === 'risk_found') return 'risk';
  return 'unresolved';
}

function compactNumber(value: string | number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return `${Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }).format(parsed)}${suffix}`;
}

export function TradingHermesResearchPanel({ strategy }: { strategy: TradingStrategyConfig }) {
  const [universe, setUniverse] = useState<GapperUniverse | null>(null);
  const [instrumentId, setInstrumentId] = useState('');
  const [audit, setAudit] = useState<HermesResearchAudit | null>(null);
  const [validation, setValidation] = useState<HermesResearchValidation | null>(null);
  const [attribution, setAttribution] = useState<Record<string, unknown> | null>(null);
  const [reviewSelections, setReviewSelections] = useState<Record<string, ResearchRecommendation>>({});
  const [reviewNote, setReviewNote] = useState('');
  const [asOf, setAsOf] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'running' | 'error'>('idle');
  const [notice, setNotice] = useState(`Select a candidate to inspect exactly what ${PRODUCT_NAME} knew and when it knew it.`);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (!strategy.active_universe_id) {
        setUniverse(null);
        setInstrumentId('');
        setAudit(null);
        return;
      }
      try {
        const next = await tradingStrategyApi.universe(strategy.active_universe_id);
        if (!alive) return;
        setUniverse(next);
        setInstrumentId((current) => current && next.candidates.some((candidate) => candidate.instrument_id === current)
          ? current
          : next.candidates[0]?.instrument_id ?? '');
      } catch (error) {
        if (alive) setNotice(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => { alive = false; };
  }, [strategy.strategy_id, strategy.revision, strategy.active_universe_id]);

  const loadAudit = async (candidate = instrumentId) => {
    if (!candidate) return;
    setStatus('loading');
    try {
      const cutoff = asOf ? new Date(asOf).toISOString() : undefined;
      const next = await tradingHermesResearchApi.audit(candidate, cutoff);
      setAudit(next);
      setNotice(next.latest_report
        ? `Loaded report v${next.latest_report.report_version} as known by ${stamp(next.as_of)}.`
        : `No causally visible HTR report exists for this candidate as of ${stamp(next.as_of)}.`);
      setStatus('idle');
    } catch (error) {
      setStatus('error');
      setNotice(error instanceof Error ? error.message : String(error));
    }
  };

  useEffect(() => {
    if (instrumentId) void loadAudit(instrumentId);
    // Deliberately refresh only when the selected candidate changes. Operators can
    // change the as-of timestamp and explicitly press Refresh to avoid hidden time travel.
  }, [instrumentId]);

  const runResearch = async () => {
    if (!instrumentId) return;
    setStatus('running');
    setNotice('Running bounded primary-source harvest and iterative Hermes follow-ups…');
    try {
      const result = await tradingHermesResearchApi.start(instrumentId, strategy.strategy_id);
      setNotice(`Research report v${result.report.report_version} saved · ${result.report.research_status} · planner ${result.planner_backend}. New facts only affect future decisions.`);
      await loadAudit(instrumentId);
    } catch (error) {
      setStatus('error');
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setStatus((current) => current === 'error' ? current : 'idle');
    }
  };

  const runValidation = async () => {
    setStatus('loading');
    try {
      const [nextValidation, nextAttribution] = await Promise.all([
        tradingHermesResearchApi.validate(strategy.strategy_id),
        tradingHermesResearchApi.attribution(strategy.strategy_id),
      ]);
      setValidation(nextValidation);
      setReviewSelections(Object.fromEntries(nextValidation.feature_results.map((item) => [item.feature, item.recommendation])));
      setReviewNote('');
      setAttribution(nextAttribution);
      setNotice(nextValidation.promotion_allowed
        ? 'A reviewed validation artifact permits promotion for this policy.'
        : `Validation remains non-authoritative: ${nextValidation.sample_size} outcomes, ${nextValidation.exact_sample_size} exact.`);
      setStatus('idle');
    } catch (error) {
      setStatus('error');
      setNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const reviewValidation = async () => {
    if (!validation || validation.promotion_allowed) return;
    const approvedRecommendations = Object.fromEntries(
      validation.feature_results.map((item) => [item.feature, reviewSelections[item.feature] ?? 'observe_only']),
    ) as Record<string, ResearchRecommendation>;
    if (!Object.values(approvedRecommendations).some((value) => value !== 'observe_only')) {
      setNotice('Select at least one HTR-14 recommendation for score/gate authority. All other features may remain observe-only.');
      return;
    }
    const note = reviewNote.trim();
    if (note.length < 10) {
      setNotice('Add a review note of at least 10 characters explaining the promotion decision.');
      return;
    }
    const confirmed = window.confirm(
      'Create this reviewed HTR-15 execution-authority artifact?\n\n' +
      'This pins the selected recommendations to trading-research-1. It does not change gap_pullback_v1 1.0/1.1; only an explicitly configured 1.2 strategy can consume it.',
    );
    if (!confirmed) return;
    setStatus('loading');
    try {
      const reviewed = await tradingHermesResearchApi.reviewValidation(
        validation.validation_id,
        approvedRecommendations,
        note,
      );
      setValidation(reviewed);
      setNotice(`Reviewed HTR-15 policy saved. Promotion is enabled for policy ${reviewed.policy_version}, but strategy 1.0/1.1 remain research-non-authoritative; only 1.2 may consume it.`);
      setStatus('idle');
    } catch (error) {
      setStatus('error');
      setNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const candidates = universe?.candidates ?? [];
  const report = audit?.latest_report ?? null;
  const facts = audit?.fact_set ?? null;
  const coverage = report?.coverage ?? null;
  const visibleEvidence = audit?.evidence ?? [];
  const supply = facts?.supply ?? [];
  const reportTimeline = audit?.report_timeline ?? [];
  const promotableValidation = Boolean(validation && !validation.promotion_allowed && validation.feature_results.some((item) => item.recommendation !== 'observe_only'));

  const sourceSummary = useMemo(() => {
    const tiers = new Map<number, number>();
    for (const item of visibleEvidence) tiers.set(item.source_authority_tier, (tiers.get(item.source_authority_tier) ?? 0) + 1);
    return [...tiers.entries()].sort(([a], [b]) => a - b).map(([tier, count]) => `Tier ${tier}: ${count}`).join(' · ');
  }, [visibleEvidence]);

  return (
    <section className="trading-hermes-research" aria-label="Hermes trading research audit">
      <header>
        <div>
          <strong>Hermes causal research</strong>
          <small>Primary-source evidence → iterative follow-ups → immutable facts → shadow features. 1.0/1.1 never grant HTR order authority.</small>
        </div>
        <span>HTR · causal research</span>
      </header>

      <div className="htr-controls">
        <label>
          <span>Candidate</span>
          <select value={instrumentId} onChange={(event) => setInstrumentId(event.target.value)} disabled={!candidates.length}>
            {!candidates.length ? <option value="">No attached universe</option> : null}
            {candidates.map((candidate) => <option key={candidate.instrument_id} value={candidate.instrument_id}>{candidate.instrument_id.split(':').at(-1) ?? candidate.instrument_id}</option>)}
          </select>
        </label>
        <label>
          <span>Audit as-of<small>blank = now</small></span>
          <input type="datetime-local" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
        </label>
        <button type="button" onClick={() => void loadAudit()} disabled={!instrumentId || status === 'loading' || status === 'running'}>Refresh as-of</button>
        <button type="button" className="primary" onClick={() => void runResearch()} disabled={!instrumentId || status === 'running'}>{status === 'running' ? 'Researching…' : 'Run bounded research'}</button>
        <button type="button" onClick={() => void runValidation()} disabled={status === 'loading' || status === 'running'}>Validate outcome dataset</button>
      </div>

      <p className="htr-notice" role="status">{notice}</p>

      {!strategy.active_universe_id ? <div className="htr-empty">Attach/freeze a point-in-time universe first. Research never selects or mutates the active universe itself.</div> : null}

      {report ? (
        <>
          <div className="htr-status-grid">
            <article data-tone={statusTone(report.catalyst_status)}><small>Catalyst</small><strong>{report.catalyst_status}</strong><span>{facts?.catalyst.catalyst_type ?? 'unknown'} · {facts?.catalyst.same_day ? 'same day' : 'not confirmed same day'}</span></article>
            <article data-tone={statusTone(report.supply_status)}><small>Supply</small><strong>{report.supply_status.replaceAll('_', ' ')}</strong><span>{facts?.supply_metrics.supply_resolution_status ?? 'unresolved'}</span></article>
            <article data-tone={report.research_status === 'complete' ? 'complete' : 'unresolved'}><small>Research</small><strong>{report.research_status}</strong><span>report v{report.report_version} · {report.planner_backend}</span></article>
            <article><small>{PRODUCT_NAME} knew report at</small><strong>{stamp(report.omnix_known_at)}</strong><span>decision reads require known_at ≤ decision_at</span></article>
          </div>

          <div className="htr-columns">
            <section>
              <header><strong>Coverage</strong><small>Explicit checks, not a synthetic completion percentage</small></header>
              <div className="htr-coverage">
                {coverage ? COVERAGE_LABELS.map(([key, label]) => <div key={key} data-tone={statusTone(coverage[key])}><b>{statusMark(coverage[key])}</b><span>{label}</span><small>{coverage[key]}</small></div>) : null}
              </div>
              {report.unresolved_facts.length ? <div className="htr-unresolved"><strong>Unresolved</strong>{report.unresolved_facts.map((item) => <span key={item}>? {item.replaceAll('_', ' ')}</span>)}</div> : <div className="htr-unresolved clean"><strong>Unresolved</strong><span>None recorded in this report.</span></div>}
            </section>

            <section>
              <header><strong>Derived facts</strong><small>Strategy never parses report prose</small></header>
              <dl className="htr-facts">
                <div><dt>Primary catalyst</dt><dd>{facts?.catalyst.primary_confirmed ? 'confirmed' : 'not confirmed'}</dd></div>
                <div><dt>Primary / secondary sources</dt><dd>{facts?.catalyst.source_count_primary ?? 0} / {facts?.catalyst.source_count_secondary ?? 0}</dd></div>
                <div><dt>Immediate supply risk</dt><dd>{facts?.supply_metrics.immediate_supply_risk === null || facts?.supply_metrics.immediate_supply_risk === undefined ? 'unknown' : facts.supply_metrics.immediate_supply_risk ? 'yes' : 'no'}</dd></div>
                <div><dt>Potential dilution / float</dt><dd>{compactNumber(facts?.supply_metrics.potential_dilution_pct_float, '%')}</dd></div>
                <div><dt>ITM warrants / float</dt><dd>{compactNumber(facts?.supply_metrics.in_the_money_warrant_pct_float, '%')}</dd></div>
                <div><dt>ATM remaining / market cap</dt><dd>{compactNumber(facts?.supply_metrics.remaining_atm_pct_market_cap, '%')}</dd></div>
              </dl>
              {audit?.shadow ? <div className="htr-shadow"><strong>AI shadow</strong><span>{audit.shadow.novelty} · {audit.shadow.relevance} relevance · {audit.shadow.catalyst_class}</span><small>{audit.shadow.conflict_summary || audit.shadow.rationale}</small></div> : <div className="htr-shadow"><strong>AI shadow</strong><span>Unavailable / not run</span></div>}
            </section>
          </div>

          <details className="htr-detail" open>
            <summary>Evidence visible as of {stamp(audit?.as_of)}</summary>
            <div className="htr-table-wrap">
              <table><thead><tr><th>Source</th><th>Authority</th><th>Published / available</th><th>Captured</th><th>{PRODUCT_NAME} known</th><th>Extraction</th></tr></thead>
                <tbody>{visibleEvidence.map((item) => <tr key={item.evidence_id}><td><strong>{item.title ?? item.evidence_id}</strong><small>{item.source_type} · {item.source_locator}</small></td><td>Tier {item.source_authority_tier}</td><td>{stamp(item.source_published_at)}<small>{stamp(item.source_available_at)}</small></td><td>{stamp(item.captured_at)}</td><td><strong>{stamp(item.omnix_known_at)}</strong></td><td>{item.extraction_status}</td></tr>)}</tbody>
              </table>
              {!visibleEvidence.length ? <p>No causally visible evidence.</p> : <small>{sourceSummary}</small>}
            </div>
          </details>

          <details className="htr-detail">
            <summary>Typed supply facts ({supply.length})</summary>
            <div className="htr-table-wrap"><table><thead><tr><th>Type</th><th>Status</th><th>Shares</th><th>Capacity</th><th>Strike</th><th>Registration</th><th>Resolution</th></tr></thead><tbody>{supply.map((item) => <tr key={item.fact_id}><td>{item.supply_type}</td><td><strong>{item.status}</strong></td><td>{compactNumber(item.shares)}</td><td>{item.remaining_capacity_usd === null ? '—' : `$${compactNumber(item.remaining_capacity_usd)}`}</td><td>{item.strike_price === null ? '—' : `$${item.strike_price}`}</td><td>{item.registration_status ?? '—'}</td><td>{item.resolution_status}</td></tr>)}</tbody></table></div>
          </details>

          <details className="htr-detail">
            <summary>Hermes action trace ({audit?.hermes_actions.length ?? 0})</summary>
            <div className="htr-action-list">{audit?.hermes_actions.map((action) => <article key={action.action_id}><header><strong>{action.step + 1}. {action.operation}</strong><span>{action.status}</span><time>{stamp(action.omnix_known_at)}</time></header><p>{action.reason || 'No planner reason supplied.'}</p><small>{action.evidence_ids.length ? `Evidence: ${action.evidence_ids.join(', ')}` : JSON.stringify(action.result_summary)}</small></article>)}{!audit?.hermes_actions.length ? <p>No Hermes actions were required or Hermes was disabled; deterministic source harvest may still have produced the report.</p> : null}</div>
          </details>

          <details className="htr-detail">
            <summary>Immutable report timeline ({reportTimeline.length})</summary>
            <div className="htr-timeline">{reportTimeline.map((item) => <article key={item.report_id}><strong>v{item.report_version}</strong><span>{item.research_status} · catalyst {item.catalyst_status} · supply {item.supply_status}</span><time>known {stamp(item.omnix_known_at)}</time><small>{item.stop_reason ?? 'completed'}</small></article>)}</div>
          </details>
        </>
      ) : strategy.active_universe_id && instrumentId ? <div className="htr-empty">No HTR report is visible at this as-of timestamp. Run research or choose a later audit time.</div> : null}

      {validation ? <section className="htr-validation" data-promoted={validation.promotion_allowed ? 'true' : 'false'}>
        <header><strong>HTR-14 / HTR-15 validation</strong><span>{validation.sample_size} outcomes · {validation.exact_sample_size} exact</span></header>
        <p>{validation.promotion_allowed
          ? 'Reviewed promotion artifact is active. It can affect only explicitly configured strategy 1.2; 1.0/1.1 continue to ignore HTR authority.'
          : 'Automatic HTR-14 analysis is non-authoritative. A reviewer may preserve or reduce eligible recommendations, never strengthen them.'}</p>
        {validation.feature_results.map((item) => {
          const maximum = RECOMMENDATION_LEVELS.indexOf(item.recommendation);
          return <div key={item.feature}>
            <strong>{item.feature.replaceAll('_', ' ')}</strong>
            {validation.promotion_allowed ? <span>{item.recommendation}</span> : <select
              aria-label={`Reviewed authority for ${item.feature}`}
              value={reviewSelections[item.feature] ?? 'observe_only'}
              onChange={(event) => setReviewSelections((current) => ({ ...current, [item.feature]: event.target.value as ResearchRecommendation }))}
            >{RECOMMENDATION_LEVELS.slice(0, maximum + 1).map((value) => <option key={value} value={value}>{value}</option>)}</select>}
            <small>validator ≤ {item.recommendation} · in {String(item.in_sample_effect_r ?? 'N/A')}R · out {String(item.out_of_sample_effect_r ?? 'N/A')}R · 2R Δ {String(item.win_probability_delta ?? 'N/A')} · CI [{String(item.confidence_interval_low ?? 'N/A')}, {String(item.confidence_interval_high ?? 'N/A')}] · n={item.sample_size}</small>
          </div>;
        })}
        {promotableValidation ? <>
          <label className="htr-review-note"><span>Promotion review note</span><textarea value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="Explain why these validated features should become authoritative in strategy 1.2." /></label>
          <button type="button" className="primary" onClick={() => void reviewValidation()} disabled={status === 'loading' || status === 'running'}>Create reviewed HTR-15 policy</button>
        </> : null}
        {!validation.promotion_allowed && !promotableValidation ? <small>No feature currently meets HTR-14 promotion thresholds. Continue collecting causal outcomes.</small> : null}
        {attribution ? <details><summary>Attribution summary</summary><pre>{JSON.stringify(attribution, null, 2)}</pre></details> : null}
      </section> : null}
    </section>
  );
}
