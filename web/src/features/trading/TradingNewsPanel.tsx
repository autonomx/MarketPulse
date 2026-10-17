import { useEffect, useRef, useState } from 'react';
import { tradingHermesResearchApi } from './tradingHermesResearchApi';
import type { HermesMarketBrief, HermesResearchAudit, HermesResearchEvidence } from './tradingHermesResearchApi';
import './TradingNews.css';

type NewsResearchStatus = 'idle' | 'running' | 'ready' | 'empty' | 'error';

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Unknown time';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function isHttpSource(locator: string): boolean {
  return /^https?:\/\//i.test(locator);
}

function BriefItems({
  title,
  items,
  evidenceById,
}: {
  title: string;
  items: HermesMarketBrief['key_points'];
  evidenceById: Map<string, HermesResearchEvidence>;
}) {
  if (!items.length) return null;
  return (
    <section className="trading-news-brief-items" aria-label={title}>
      <strong>{title}</strong>
      <ul>
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>
            <span>{item.text}</span>
            {item.source_evidence_ids.length ? (
              <span className="trading-news-brief-citations">
                {item.source_evidence_ids.map((evidenceId) => {
                  const evidence = evidenceById.get(evidenceId);
                  return evidence && isHttpSource(evidence.source_locator) ? (
                    <a key={evidenceId} href={evidence.source_locator} target="_blank" rel="noreferrer" title={evidence.title || evidence.source_locator}>
                      Source
                    </a>
                  ) : <small key={evidenceId}>Source</small>;
                })}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TradingNewsPanel({ instrumentId }: { instrumentId: string }) {
  const [status, setStatus] = useState<NewsResearchStatus>('idle');
  const [audit, setAudit] = useState<HermesResearchAudit | null>(null);
  const [brief, setBrief] = useState<HermesMarketBrief | null>(null);
  const [briefWarning, setBriefWarning] = useState<string | null>(null);
  const [notice, setNotice] = useState('Research runs only when you request it.');
  const requestGenerationRef = useRef(0);
  const activeInstrumentRef = useRef(instrumentId);

  useEffect(() => {
    activeInstrumentRef.current = instrumentId;
    requestGenerationRef.current += 1;
    setStatus('idle');
    setAudit(null);
    setBrief(null);
    setBriefWarning(null);
    setNotice('Research runs only when you request it.');
  }, [instrumentId]);

  const runResearch = async () => {
    if (!instrumentId || status === 'running') return;
    const requestedInstrumentId = instrumentId;
    const requestGeneration = ++requestGenerationRef.current;
    setStatus('running');
    setAudit(null);
    setBrief(null);
    setBriefWarning(null);
    setNotice('Running Hermes research for this symbol...');
    try {
      const result = await tradingHermesResearchApi.start(requestedInstrumentId);
      if (
        requestGeneration !== requestGenerationRef.current
        || activeInstrumentRef.current !== requestedInstrumentId
      ) return;
      const nextAudit = await tradingHermesResearchApi.audit(requestedInstrumentId);
      if (
        requestGeneration !== requestGenerationRef.current
        || activeInstrumentRef.current !== requestedInstrumentId
      ) return;
      const currentEvidenceIds = new Set(result.report.source_evidence_ids);
      const currentEvidence = nextAudit.evidence.filter((item) => currentEvidenceIds.has(item.evidence_id));
      setAudit({
        ...nextAudit,
        latest_report: result.report,
        evidence: currentEvidence,
      });
      setBrief(result.brief ?? null);
      setBriefWarning(result.brief_warning ?? null);
      const sourceCount = currentEvidence.length;
      setStatus(sourceCount ? 'ready' : 'empty');
      setNotice(sourceCount
        ? `Research complete. ${sourceCount} sources used for this run. Planner: ${result.planner_backend}.`
        : `Research finished, but no usable sources were returned. Planner: ${result.planner_backend}.`);
    } catch (error) {
      setStatus('error');
      setNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const report = audit?.latest_report ?? null;
  const preferredEvidence = (audit?.evidence ?? []).filter(
    (item) => item.source_type === 'web' || item.source_type === 'company_ir',
  );
  const evidence = (preferredEvidence.length ? preferredEvidence : audit?.evidence ?? []).slice(0, 10);
  const evidenceById = new Map((audit?.evidence ?? []).map((item) => [item.evidence_id, item]));
  const statusLabel = status === 'running'
    ? 'Researching'
    : status === 'error'
      ? 'Error'
      : status === 'empty'
        ? 'No sources'
        : status === 'ready'
          ? 'Ready'
          : 'On demand';

  return (
    <section className="trading-news-panel" aria-label="Trading news">
      <header>
        <div>
          <strong>Market news</strong>
          <small>On-demand Hermes research</small>
        </div>
        <span className="trading-news-status" data-status={status}>{statusLabel}</span>
      </header>

      <div className="trading-news-empty trading-news-hermes-intro">
        <span aria-hidden="true">H</span>
        <div>
          <strong>Research this symbol with Hermes.</strong>
          <p>Hermes checks current web results, company releases, and SEC evidence, then records the sources it used.</p>
        </div>
      </div>

      <button
        type="button"
        className="trading-news-research-button"
        onClick={() => void runResearch()}
        disabled={!instrumentId || status === 'running'}
      >
        {status === 'running' ? 'Researching...' : 'Research with Hermes'}
      </button>

      <p className="trading-news-notice" role="status">{notice}</p>

      {report ? (
        <>
          <div className="trading-news-research-summary">
            <div><small>Research</small><strong>{report.research_status}</strong></div>
            <div><small>Catalyst</small><strong>{report.catalyst_status}</strong></div>
            <div><small>Sources</small><strong>{audit?.evidence.length ?? 0}</strong></div>
          </div>

          {brief ? (
            <section className="trading-news-ai-brief" aria-label="AI market brief">
              <header>
                <div>
                  <small>AI market brief</small>
                  <strong>{brief.headline}</strong>
                </div>
                <span>{brief.confidence} confidence</span>
              </header>
              <p>{brief.summary}</p>
              <BriefItems title="Key points" items={brief.key_points} evidenceById={evidenceById} />
              <BriefItems title="Risks" items={brief.risks} evidenceById={evidenceById} />
              <BriefItems title="Watch" items={brief.watch_items} evidenceById={evidenceById} />
              <small className="trading-news-brief-meta">
                Generated with your configured AI · {formatDate(brief.generated_at)}
              </small>
            </section>
          ) : briefWarning ? <p className="trading-news-brief-warning">{briefWarning}</p> : null}

          <section className="trading-news-evidence" aria-label="Hermes research sources">
            <header>
              <strong>Sources used</strong>
              <small>As of {formatDate(audit?.as_of)}</small>
            </header>
            {evidence.length ? evidence.map((item) => (
              <article key={item.evidence_id}>
                {isHttpSource(item.source_locator) ? (
                  <a href={item.source_locator} target="_blank" rel="noreferrer">
                    {item.title || item.source_locator}
                  </a>
                ) : <strong>{item.title || item.source_locator}</strong>}
                <small>{item.source_type} / {formatDate(item.source_published_at || item.captured_at)}</small>
              </article>
            )) : <p>No source evidence was returned.</p>}
          </section>
        </>
      ) : null}

      <small className="trading-news-disclaimer">
        Research is read-only. It does not place orders or automatically run when this tab opens.
      </small>
    </section>
  );
}
