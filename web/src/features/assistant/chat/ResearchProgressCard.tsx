import { useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { JobRecord } from '../../../api/client';
import type { components } from '../api/generated';
import {
  arrayRecords,
  asRecord,
  awaitingResearchPlanApproval,
  cancelResearchJob,
  completedStageCount,
  dismissResearchJob,
  hasSavedResearchPlan,
  humanizeCode,
  isActiveResearchJob,
  isStalledResearchJob,
  normalizedPageLimit,
  numberValue,
  researchJobOutput,
  researchMaxPages,
  researchPlanSteps,
  researchPlanTitle,
  researchProgressPercent,
  researchStageAnnouncement,
  researchStageLabel,
  startResearchJob,
  stringList,
  stringValue,
  updateResearchPlanPages,
  useResearchProgress,
  visibleResearchJobId,
  type JobOutput,
} from './researchProgressController';

type ChatMessage = components['schemas']['ChatMessage'];

const CANCEL_REASON = 'Canceled by the user from the research progress panel.';

/** Deep research progress below the transcript, for the session's newest research job. */
export function ResearchProgressCard() {
  const state = useResearchProgress();
  const jobId = visibleResearchJobId(state);
  if (!jobId) return null;
  if (state.error && !state.job) {
    return <section className="assistant-research-progress" data-omnix-research-progress="true"><p role="status">{state.error}</p></section>;
  }
  if (!state.job || state.job.id !== jobId) {
    return <section className="assistant-research-progress" data-omnix-research-progress="true"><p role="status">Restoring research progress…</p></section>;
  }
  return <ResearchJobPanel job={state.job} />;
}

/** The panel for one research job: plan review, plan progress, a stalled worker, or job status. */
export function ResearchJobPanel({ job }: { job: JobRecord }) {
  if (awaitingResearchPlanApproval(job)) return <ResearchPlanReview key={job.id} job={job} />;
  if (isActiveResearchJob(job) && hasSavedResearchPlan(job)) return <ResearchPlanProgress job={job} />;
  if (isStalledResearchJob(job)) return <StalledResearchJob job={job} />;
  return <ResearchJobStatus job={job} />;
}

function CancelButton({ job, reason = CANCEL_REASON }: { job: JobRecord; reason?: string }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const requested = String(job.status) === 'cancel_requested';
  return (
    <>
      <button
        type="button"
        data-omnix-research-cancel="true"
        disabled={requested || busy}
        onClick={() => {
          setBusy(true);
          setFailed(false);
          cancelResearchJob(job, reason).catch(() => setFailed(true)).finally(() => setBusy(false));
        }}
      >
        {requested ? 'Cancellation requested' : busy ? 'Canceling…' : 'Cancel research'}
      </button>
      {failed ? <span role="alert">Cancellation failed.</span> : null}
    </>
  );
}

function ProgressTrack({ job }: { job: JobRecord }) {
  return <div className="assistant-research-progress-track" aria-hidden="true"><span style={{ width: `${researchProgressPercent(job)}%` } as CSSProperties} /></div>;
}

function ResearchJobStatus({ job }: { job: JobRecord }) {
  const active = isActiveResearchJob(job);
  const details = researchJobOutput(job);
  return (
    <section className={`assistant-research-progress status-${String(job.status)}`} data-omnix-research-progress="true">
      <header>
        <div><p className="eyebrow">Deep research</p><h3>{researchStageLabel(job)}</h3></div>
        <div className="assistant-research-header-actions">
          <span className="assistant-research-status">{humanizeCode(String(job.status))}</span>
          {active ? null : <button type="button" className="assistant-research-close" data-omnix-research-close="true" aria-label="Close research progress" onClick={() => dismissResearchJob(job.id)}>&times;</button>}
        </div>
      </header>
      <ProgressTrack job={job} />
      <p className="assistant-research-announcement" aria-live="polite" aria-atomic="true">{researchStageAnnouncement(job)}</p>
      <div className="assistant-research-progress-actions">
        {active ? <CancelButton job={job} /> : null}
        <small>{completedStageCount(job)} of {job.stages?.length ?? 0} stages complete</small>
      </div>
      {details ? <ResearchJobDetails details={details} /> : null}
    </section>
  );
}

function ResearchPlanProgress({ job }: { job: JobRecord }) {
  const plan = researchPlanSteps(job);
  const completedCount = Math.min(plan.length, completedStageCount(job));
  return (
    <section className={`assistant-research-progress status-${String(job.status)} status-plan-progress`} data-omnix-research-progress="true">
      <header>
        <div><p className="eyebrow">Deep research</p><h3>{researchPlanTitle(job)}</h3></div>
        <span className="assistant-research-status">{humanizeCode(String(job.status))}</span>
      </header>
      <ProgressTrack job={job} />
      <p className="assistant-research-plan-intro" role="status">{researchStageAnnouncement(job)} The completed outline areas are marked below.</p>
      <ol className="assistant-research-plan-list" aria-label="Research outline progress">
        {plan.map((step, index) => (
          <li key={step} className={index < completedCount ? 'is-completed' : ''}><i aria-hidden="true">{index < completedCount ? '✓' : ''}</i><span>{step}</span></li>
        ))}
      </ol>
      <div className="assistant-research-plan-controls">
        <label>Max pages to search<input type="number" min={1} max={100} value={researchMaxPages(job)} readOnly aria-label="Research maximum pages" /></label>
        <small>A hard cap on unique pages searched for this run.</small>
      </div>
      <div className="assistant-research-progress-actions assistant-research-plan-actions">
        <small>{completedCount} of {plan.length} outline areas complete</small>
        <CancelButton job={job} />
      </div>
    </section>
  );
}

function StalledResearchJob({ job }: { job: JobRecord }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="assistant-research-progress status-stalled" data-omnix-research-progress="true">
      <header>
        <div><p className="eyebrow">Deep research</p><h3>Research needs restarting</h3></div>
        <span className="assistant-research-status">Paused</span>
      </header>
      <p className="assistant-research-announcement" role="status">The research worker stopped before it began. Restarting preserves this plan and its {researchMaxPages(job)}-page limit.</p>
      <div className="assistant-research-progress-actions assistant-research-plan-actions">
        <small>No pages have been searched yet.</small>
        <button
          type="button"
          className="assistant-research-start"
          data-omnix-research-restart="true"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            startResearchJob(job).catch(() => setError('The research worker could not be restarted.')).finally(() => setBusy(false));
          }}
        >
          {busy ? 'Restarting…' : 'Restart research'}
        </button>
        {error ? <span data-omnix-research-plan-error="true" role="alert">{error}</span> : null}
      </div>
    </section>
  );
}

function ResearchPlanReview({ job }: { job: JobRecord }) {
  const maxPages = researchMaxPages(job);
  const [pages, setPages] = useState(String(maxPages));
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState<'save' | 'start' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pageInputRef = useRef<HTMLInputElement | null>(null);
  const plannerBackend = stringValue(asRecord(job.input_payload).planner_backend);
  const outlineStatus = ['provider', 'hermes'].includes(plannerBackend) ? 'AI outline ready' : 'Outline ready';

  async function persistPages(): Promise<JobRecord> {
    const selected = normalizedPageLimit(pages);
    if (selected === null) throw new Error('Choose a page limit from 1 to 100.');
    return updateResearchPlanPages(job, selected);
  }

  function run(kind: 'save' | 'start' | 'cancel', task: () => Promise<unknown>, fallback: string): void {
    setAction(kind);
    setError(null);
    task()
      .then(() => {
        if (kind === 'save') setEditing(false);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : fallback))
      .finally(() => setAction(null));
  }

  function toggleEditing(): void {
    if (!editing) {
      setEditing(true);
      queueMicrotask(() => {
        pageInputRef.current?.focus();
        pageInputRef.current?.select();
      });
      return;
    }
    run('save', persistPages, 'The research plan could not be updated.');
  }

  const busy = action !== null;
  return (
    <section className="assistant-research-progress status-plan-review" data-omnix-research-progress="true">
      <header>
        <div><p className="eyebrow">Deep research</p><h3>{researchPlanTitle(job)}</h3></div>
        <span className="assistant-research-status">{outlineStatus}</span>
      </header>
      <p className="assistant-research-plan-intro" role="status">Here’s the AI-generated research outline I’ll follow before writing the answer. No web pages have been searched yet.</p>
      <ol className="assistant-research-plan-list" aria-label="Research outline">
        {researchPlanSteps(job).map((step) => <li key={step}><i aria-hidden="true" /><span>{step}</span></li>)}
      </ol>
      <div className="assistant-research-plan-controls">
        <label>
          Max pages to search
          <input
            ref={pageInputRef}
            type="number"
            min={1}
            max={100}
            step={1}
            value={pages}
            readOnly={!editing}
            aria-label="Research plan maximum pages"
            aria-describedby="omnix-research-plan-page-help"
            onChange={(event) => setPages(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && editing) toggleEditing();
            }}
          />
        </label>
        <small id="omnix-research-plan-page-help">A hard cap on unique pages searched for this run.</small>
      </div>
      <div className="assistant-research-progress-actions assistant-research-plan-actions">
        <button type="button" data-omnix-research-plan-update="true" disabled={busy} onClick={toggleEditing}>
          {action === 'save' ? 'Saving…' : editing ? 'Save' : 'Edit'}
        </button>
        <span>
          <button type="button" data-omnix-research-plan-cancel="true" disabled={busy} onClick={() => run('cancel', () => cancelResearchJob(job, 'Canceled before deep research started.'), 'The research plan could not be canceled.')}>
            {action === 'cancel' ? 'Canceling…' : 'Cancel'}
          </button>
          <button type="button" className="assistant-research-start" data-omnix-research-plan-start="true" disabled={busy} onClick={() => run('start', async () => startResearchJob(await persistPages()), 'The research plan could not be started.')}>
            {action === 'start' ? 'Starting…' : 'Start research'}
          </button>
        </span>
        {error ? <span data-omnix-research-plan-error="true" role="alert">{error}</span> : null}
      </div>
    </section>
  );
}

function DetailRow({ term, children }: { term: string; children: ReactNode }) {
  return <div><dt>{term}</dt><dd>{children}</dd></div>;
}

function ResearchJobDetails({ details }: { details: JobOutput }) {
  const searchHits = details.searchDiagnostics.reduce((total, diagnostic) => total + (diagnostic.results ?? 0), 0);
  const extractionFailures = details.searchDiagnostics.reduce((total, diagnostic) => total + (diagnostic.extractionFailures ?? 0), 0);
  return (
    <details className="assistant-research-job-details">
      <summary>Research details</summary>
      <dl>
        {details.researchStatus ? <DetailRow term="Result">{humanizeCode(details.researchStatus)}</DetailRow> : null}
        {details.researchProvider ? <DetailRow term="Search provider">{humanizeCode(details.researchProvider)}</DetailRow> : null}
        {details.plannerBackend ? <DetailRow term="Planner">{details.plannerBackend}</DetailRow> : null}
        {details.synthesisBackend ? <DetailRow term="Synthesis">{details.synthesisBackend}</DetailRow> : null}
        {details.logicalQueries !== undefined ? <DetailRow term="Queries">{details.logicalQueries}</DetailRow> : null}
        <DetailRow term="Search hits">{searchHits}</DetailRow>
        {details.extractedPages !== undefined ? <DetailRow term="Pages reviewed">{details.extractedPages}{details.sources.length ? ` of ${details.sources.length}` : ''}</DetailRow> : null}
        {extractionFailures ? <DetailRow term="Extraction failures">{extractionFailures}</DetailRow> : null}
        {details.stopReason ? <DetailRow term="Stop reason">{humanizeCode(details.stopReason)}</DetailRow> : null}
      </dl>
      {details.sources.length ? (
        <section><h4>Sources</h4><ol className="assistant-research-source-list">
          {details.sources.map((source) => (
            <li key={source.id}>
              <span>{source.citation ? `[${source.citation}] ` : ''}{source.title}</span>
              {source.url ? <a href={source.url} target="_blank" rel="noreferrer">Open source</a> : null}
              {source.extractionStatus ? <small>{humanizeCode(source.extractionStatus)}</small> : null}
            </li>
          ))}
        </ol></section>
      ) : null}
      {details.searchDiagnostics.length ? (
        <section><h4>Search diagnostics</h4><ol>
          {details.searchDiagnostics.map((diagnostic, index) => (
            <li key={`${diagnostic.query ?? 'query'}-${index}`}>
              <span>{diagnostic.query || 'Search query'}</span>
              <small>{[diagnostic.provider, diagnostic.status, diagnostic.results !== undefined ? `${diagnostic.results} results` : '', diagnostic.coverage, diagnostic.error].filter(Boolean).join(' · ')}</small>
            </li>
          ))}
        </ol></section>
      ) : null}
      {details.conflicts.length ? <section><h4>Unresolved conflicts</h4><ul>{details.conflicts.map((conflict) => <li key={conflict.id}>{conflict.summary}</li>)}</ul></section> : null}
      {details.warnings.length ? <section><h4>Warnings</h4><ul>{details.warnings.map((warning) => <li key={warning}>{humanizeCode(warning)}</li>)}</ul></section> : null}
    </details>
  );
}

/** Search details under a quick-search or deep-research answer. */
export function ResearchMessageDetails({ message }: { message: ChatMessage }) {
  const metadata = asRecord(message.metadata);
  const mode = stringValue(metadata.research_mode);
  if (mode !== 'quick' && mode !== 'deep') return null;
  const status = stringValue(metadata.research_status) || 'completed';
  const validation = asRecord(metadata.citation_validation ?? metadata.synthesis_validation);
  const diagnostics = arrayRecords(metadata.search_diagnostics);
  const searchHits = diagnostics.reduce((total, diagnostic) => total + (numberValue(diagnostic.results) ?? 0), 0);
  const extractionFailures = diagnostics.reduce((total, diagnostic) => total + (numberValue(diagnostic.extraction_failures) ?? 0), 0);
  const budget = asRecord(metadata.research_budget);
  const pageLimit = numberValue(budget.max_sources) ?? numberValue(metadata.max_sources);
  const provider = stringValue(metadata.research_provider) || stringValue(metadata.web_search_provider);
  const providerChain = stringList(metadata.research_provider_chain);
  const stopReason = stringValue(metadata.research_stop_reason);
  const sourceCount = numberValue(metadata.web_search_source_count) ?? searchHits;
  const logicalQueries = numberValue(metadata.logical_queries);
  const extractedPages = numberValue(metadata.extracted_pages);
  const conflictCount = numberValue(metadata.conflict_count);
  const searches = diagnostics.map((diagnostic) => stringValue(diagnostic.query)).filter(Boolean);
  const warnings = stringList(metadata.research_warnings);
  return (
    <details data-omnix-research-message-details="true" className="assistant-research-message-details">
      <summary>{mode === 'quick' ? 'Quick search details' : 'Research details'} · {status}</summary>
      <dl>
        <DetailRow term="Mode">{mode === 'quick' ? 'Quick search' : 'Deep research'}</DetailRow>
        {provider ? <DetailRow term="Search provider">{humanizeCode(provider)}</DetailRow> : null}
        {providerChain.length ? <DetailRow term="Provider route">{providerChain.map(humanizeCode).join(' -> ')}</DetailRow> : null}
        {stringValue(metadata.source_manifest_id) ? <DetailRow term="Sources">Manifest saved</DetailRow> : null}
        {stringValue(metadata.planner_backend) ? <DetailRow term="Planner">{stringValue(metadata.planner_backend)}</DetailRow> : null}
        {stringValue(metadata.synthesis_backend) ? <DetailRow term="Synthesis">{stringValue(metadata.synthesis_backend)}</DetailRow> : null}
        {logicalQueries !== null ? <DetailRow term="Queries">{logicalQueries}</DetailRow> : null}
        <DetailRow term="Search hits">{sourceCount}</DetailRow>
        {extractedPages !== null ? <DetailRow term="Pages reviewed">{extractedPages}{pageLimit !== null ? ` of ${pageLimit}` : ''}</DetailRow> : null}
        {extractionFailures ? <DetailRow term="Extraction failures">{extractionFailures}</DetailRow> : null}
        {conflictCount !== null ? <DetailRow term="Conflicts">{conflictCount}</DetailRow> : null}
        {typeof validation.valid === 'boolean' ? <DetailRow term="Citations">{validation.valid ? 'Validated' : 'Validation warning'}</DetailRow> : null}
        {stopReason ? <DetailRow term="Stop reason">{humanizeCode(stopReason)}</DetailRow> : null}
        {searches.length ? <DetailRow term="Searches performed">{searches.join(' | ')}</DetailRow> : null}
      </dl>
      {warnings.length ? <ul>{warnings.map((warning) => <li key={warning}>{humanizeCode(warning)}</li>)}</ul> : null}
    </details>
  );
}
