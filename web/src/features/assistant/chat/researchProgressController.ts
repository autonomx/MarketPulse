import { useSyncExternalStore } from 'react';
import {
  omnixApiClient,
  type ChatSession,
  type JobRecord,
  type SendChatMessageResponse,
} from '../../../api/client';
import type { components } from '../api/generated';
import { downloadBlob } from '../../../shared/download';
import { emitOmnixEvent } from '../../../events/bus';
import { POLL_INTERVALS_MS, startPolling } from '../../../shared/timers';
import { assistantApiClient } from '../api/assistantClient';

let researchProgressControllerInstalled = false;

const RESEARCH_JOB_TYPE = 'assistant.deep_research';
const ACTIVE_STATUSES = new Set(['queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested']);
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled', 'stale']);

type ChatMessage = components['schemas']['ChatMessage'];
let activeSession: ChatSession | null = null;
let activeJob: JobRecord | null = null;
let stopJobPolling: (() => void) | null = null;
let pollingJobId: string | null = null;
let currentSessionId: string | null = null;
let recoveringSessionId: string | null = null;
const dismissedJobIds = new Set<string>();

/** What the research progress card shows (WP-9.4: Chat renders it from this state). */
export type ResearchProgressState = {
  session: ChatSession | null;
  job: JobRecord | null;
  dismissedJobIds: ReadonlySet<string>;
  error: string | null;
};

let snapshot: ResearchProgressState = { session: null, job: null, dismissedJobIds: new Set(), error: null };
const listeners = new Set<() => void>();

function publish(error: string | null = null): void {
  snapshot = { session: activeSession, job: activeJob, dismissedJobIds: new Set(dismissedJobIds), error };
  listeners.forEach((listener) => listener());
}

export const researchProgressStore = {
  getState: (): ResearchProgressState => snapshot,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useResearchProgress(): ResearchProgressState {
  return useSyncExternalStore(researchProgressStore.subscribe, researchProgressStore.getState, researchProgressStore.getState);
}

/** The job the card is about, or null when nothing should show. */
export function visibleResearchJobId(state: ResearchProgressState): string | null {
  const jobId = preferredResearchJobId(state.session?.messages ?? [], state.job);
  if (!jobId) return null;
  if (state.dismissedJobIds.has(jobId) && (!state.job || !isActiveResearchJob(state.job))) return null;
  if (state.job?.id === jobId && String(state.job.status) === 'completed') return null;
  return jobId;
}

export function dismissResearchJob(jobId: string): void {
  dismissedJobIds.add(jobId);
  publish();
}

/** Cancels a research job (before it starts or while it runs). */
export async function cancelResearchJob(job: JobRecord, reason: string): Promise<void> {
  activeJob = await omnixApiClient.cancelJob(job.id, reason);
  if (!isActiveResearchJob(activeJob)) stopWatchingResearchJob();
  publish();
}

/** Saves a changed page limit on a plan awaiting approval. */
export async function updateResearchPlanPages(job: JobRecord, pages: number): Promise<JobRecord> {
  if (pages === researchMaxPages(job)) return job;
  activeJob = await assistantApiClient.updateDeepResearchPlan(job.id, { max_pages: pages });
  publish();
  return activeJob;
}

/** Starts an approved plan (or restarts a stalled one) and follows it. */
export async function startResearchJob(job: JobRecord): Promise<void> {
  activeJob = await assistantApiClient.startDeepResearchPlan(job.id);
  watchResearchJob(activeJob.id);
  publish();
}

export function installResearchProgressController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (researchProgressControllerInstalled) return () => undefined;
  researchProgressControllerInstalled = true;
  const handleUnload = () => stopWatchingResearchJob();
  window.addEventListener('beforeunload', handleUnload, { once: true });
  return () => {
    window.removeEventListener('beforeunload', handleUnload);
    stopWatchingResearchJob();
    activeSession = null;
    activeJob = null;
    currentSessionId = null;
    publish();
    researchProgressControllerInstalled = false;
  };
}

/** The chat workspace reports each session it loads, so research progress follows it. */
export function noteChatSession(session: ChatSession): ChatSession {
  if (researchProgressControllerInstalled) captureSession(session);
  return session;
}

/** The chat workspace reports each message it sends; a deep research job is followed until it ends. */
export function noteChatMessageSent(sessionId: string, result: SendChatMessageResponse): SendChatMessageResponse {
  if (!researchProgressControllerInstalled) return result;
  captureSession(result.session);
  if (result.job?.type === RESEARCH_JOB_TYPE) {
    activeJob = result.job;
    currentSessionId = sessionId;
    publish();
    watchResearchJob(result.job.id);
  }
  return result;
}

function captureSession(session: ChatSession): void {
  const sessionChanged = currentSessionId !== null && currentSessionId !== session.id;
  if (sessionChanged) {
    activeJob = null;
    stopWatchingResearchJob();
  }
  activeSession = session;
  currentSessionId = session.id;
  const jobId = preferredResearchJobId(session.messages ?? [], activeJob);
  if (!jobId) {
    if (!isActiveResearchJob(activeJob)) stopWatchingResearchJob();
    void recoverLatestResearchJob(session.id);
    publish();
    return;
  }
  if (!isActiveResearchJob(activeJob)) void recoverLatestResearchJob(session.id, jobId);
  if (activeJob?.id !== jobId || isActiveResearchJob(activeJob)) watchResearchJob(jobId);
  publish();
}

// Progress (pages read, sources) is written without job events, so it is polled.
function watchResearchJob(jobId: string): void {
  if (pollingJobId === jobId && stopJobPolling !== null) return;
  stopWatchingResearchJob();
  pollingJobId = jobId;
  void pollResearchJob(jobId);
  stopJobPolling = startPolling(() => pollResearchJob(jobId), POLL_INTERVALS_MS.researchProgress);
}

function stopWatchingResearchJob(): void {
  stopJobPolling?.();
  stopJobPolling = null;
  pollingJobId = null;
}

/** Download and expand buttons inside a rendered research report (Chat passes the click). */
export function handleResearchReportAction(event: Event): void {
  const target = event.target instanceof Element
    ? event.target.closest<HTMLButtonElement>('[data-omnix-research-report-download], [data-omnix-research-report-expand]')
    : null;
  const report = target?.closest<HTMLElement>('.assistant-research-report');
  if (!target || !report) return;

  if (target.hasAttribute('data-omnix-research-report-download')) {
    downloadResearchReport(report);
    return;
  }

  const expanded = report.classList.toggle('is-expanded');
  report.closest<HTMLElement>('.assistant-chat-message')?.classList.toggle('assistant-chat-message-report-expanded', expanded);
  target.setAttribute('aria-pressed', String(expanded));
  target.setAttribute('aria-label', expanded ? 'Collapse research report' : 'Expand research report');
  target.setAttribute('title', expanded ? 'Collapse report' : 'Expand report');
  target.textContent = expanded ? '×' : '⛶';
  if (expanded) report.scrollIntoView({ block: 'nearest' });
}

function downloadResearchReport(report: HTMLElement): void {
  const host = report.closest<HTMLElement>('[data-raw-content]');
  const content = host?.dataset.rawContent || report.querySelector<HTMLElement>('.assistant-research-report-body')?.innerText || '';
  downloadBlob(new Blob([content], { type: 'text/markdown;charset=utf-8' }), 'omnix-research-report.md');
}

async function pollResearchJob(jobId: string): Promise<void> {
  try {
    const job = await omnixApiClient.getJob(jobId);
    if (pollingJobId !== jobId) return;
    activeJob = job;
    publish();
    if (!TERMINAL_STATUSES.has(String(job.status))) return;
    if (currentSessionId && await recoverLatestResearchJob(currentSessionId, job.id)) return;
    stopWatchingResearchJob();
    if (currentSessionId) {
      activeSession = await omnixApiClient.getChatSession(currentSessionId);
      // Chat refetches its session and shows the research answer.
      dispatchRefreshSignal();
      publish();
    }
  } catch {
    publish('Research progress could not be refreshed.');
  }
}

async function recoverLatestResearchJob(sessionId: string, excludeJobId?: string): Promise<boolean> {
  if (recoveringSessionId === sessionId) return false;
  recoveringSessionId = sessionId;
  try {
    const response = await omnixApiClient.listJobs({ limit: 100, full: true });
    if (currentSessionId !== sessionId) return false;
    const recovered = latestActiveResearchJobForSession(response.jobs ?? [], sessionId, excludeJobId);
    if (!recovered) return false;
    if (activeJob && isActiveResearchJob(activeJob)) {
      const activeCreatedAt = Date.parse(String(activeJob.created_at ?? '')) || 0;
      const recoveredCreatedAt = Date.parse(String(recovered.created_at ?? '')) || 0;
      if (activeCreatedAt >= recoveredCreatedAt) return false;
    }
    activeJob = recovered;
    publish();
    watchResearchJob(recovered.id);
    return true;
  } catch {
    return false;
  } finally {
    if (recoveringSessionId === sessionId) recoveringSessionId = null;
  }
}

export function latestResearchJobId(messages: ChatMessage[]): string | null {
  for (const message of [...messages].reverse()) {
    const jobId = stringValue(asRecord(message.metadata).research_job_id);
    if (jobId) return jobId;
  }
  return null;
}

export function isActiveResearchJob(job: JobRecord | null | undefined): boolean {
  return Boolean(job?.type === RESEARCH_JOB_TYPE && ACTIVE_STATUSES.has(String(job.status)));
}

export function researchStageLabel(job: JobRecord): string {
  const stage = activeStage(job);
  if (stage?.label) return stage.label;
  if (String(job.status) === 'completed') return 'Research complete';
  if (String(job.status) === 'failed') return 'Research failed';
  return 'Research job';
}

export function researchStageAnnouncement(job: JobRecord): string {
  const status = String(job.status);
  if (status === 'completed') {
    const details = researchJobOutput(job);
    if (details?.researchStatus === 'partial' || details?.stopReason === 'no_reliable_sources') {
      return 'Research completed with limited evidence. Review the warnings and search diagnostics before relying on the answer.';
    }
    return 'Research complete. The answer and source details are available in the conversation.';
  }
  if (status === 'failed') {
    const error = stringValue(asRecord(job.error).message);
    return error ? `Research failed: ${error}` : 'Research failed. Review the recorded error before retrying.';
  }
  if (status === 'canceled') return 'Research canceled.';
  if (status === 'cancel_requested') return 'Cancellation requested. The current operation will stop at the next safe boundary.';
  const stage = activeStage(job);
  return stringValue(stage?.progress?.message) || stage?.label || 'Research is queued.';
}

export function researchProgressPercent(job: JobRecord): number {
  if (String(job.status) === 'completed') return 100;
  const total = Math.max(1, job.stages?.length ?? 1);
  return Math.max(0, Math.min(99, Math.round((completedStageCount(job) / total) * 100)));
}

function activeStage(job: JobRecord) {
  const stages = job.stages ?? [];
  return stages.find((stage) => ['running', 'leased', 'cancel_requested'].includes(String(stage.status)))
    ?? stages.find((stage) => !['completed', 'failed', 'canceled', 'stale'].includes(String(stage.status)))
    ?? stages.at(-1);
}

export function completedStageCount(job: JobRecord): number {
  return (job.stages ?? []).filter((stage) => String(stage.status) === 'completed').length;
}

export type JobOutput = {
  researchStatus?: string;
  researchProvider?: string;
  plannerBackend?: string;
  synthesisBackend?: string;
  stopReason?: string;
  logicalQueries?: number;
  extractedPages?: number;
  searchDiagnostics: Array<{ query?: string; provider?: string; status?: string; results?: number; coverage?: string; error?: string; extractionFailures?: number }>;
  sources: Array<{ id: string; title: string; url?: string; citation?: string; extractionStatus?: string }>;
  conflicts: Array<{ id: string; summary: string }>;
  warnings: string[];
};

export function awaitingResearchPlanApproval(job: JobRecord): boolean {
  return asRecord(job.input_payload).awaiting_plan_approval === true
    && String(job.status) === 'queued';
}

export function preferredResearchJobId(
  messages: ChatMessage[],
  currentJob: JobRecord | null,
): string | null {
  if (currentJob && isActiveResearchJob(currentJob)) return currentJob.id;
  return latestResearchJobId(messages) ?? currentJob?.id ?? null;
}

export function latestActiveResearchJobForSession(
  jobs: JobRecord[],
  sessionId: string,
  excludeJobId?: string,
): JobRecord | null {
  return jobs
    .filter((job) => job.id !== excludeJobId
      && job.type === RESEARCH_JOB_TYPE
      && isActiveResearchJob(job)
      && stringValue(asRecord(job.input_payload).session_id) === sessionId)
    .sort((left, right) => Date.parse(String(right.created_at)) - Date.parse(String(left.created_at)))[0] ?? null;
}

export function hasSavedResearchPlan(job: JobRecord): boolean {
  const plan = asRecord(asRecord(job.input_payload).research_plan);
  return Boolean(stringValue(plan.title) || stringList(plan.steps).length);
}

export function isStalledResearchJob(job: JobRecord): boolean {
  const stages = job.stages ?? [];
  return String(job.status) === 'running'
    && stages.length > 0
    && stages.every((stage) => String(stage.status) === 'queued');
}

export function researchMaxPages(job: JobRecord): number {
  const maxSources = numberValue(asRecord(job.input_payload).max_sources);
  return Math.max(1, Math.min(100, maxSources ?? 12));
}

export function researchPlanTitle(job: JobRecord): string {
  const input = asRecord(job.input_payload);
  const plan = asRecord(input.research_plan);
  const explicitTitle = stringValue(plan.title);
  if (explicitTitle) return explicitTitle;
  const subject = researchPlanSubject(stringValue(input.question) || stringValue(plan.objective));
  return subject ? `${subject} Deep Research` : 'Deep Research Plan';
}

export function researchPlanSteps(job: JobRecord): string[] {
  const plan = asRecord(asRecord(job.input_payload).research_plan);
  const generatedSteps = stringList(plan.steps).slice(0, 8);
  if (generatedSteps.length) return generatedSteps;
  const input = asRecord(job.input_payload);
  const subject = researchPlanSubject(stringValue(input.question) || stringValue(plan.objective));
  if (isMarketResearchQuestion(stringValue(input.question) || stringValue(plan.objective))) {
    const topic = subject || 'the company';
    return [
      `Collect recent news and filings on ${topic} from primary financial sources.`,
      `Gather market data and price history for ${topic} over the relevant time horizon.`,
      `Analyze fundamentals, earnings, guidance, and analyst revisions for ${topic}.`,
      `Assess market sentiment, valuation, and other risk indicators for ${topic}.`,
      'Synthesize findings into an actionable outlook and risk-based approach.',
    ];
  }
  return [
    `Collect recent, authoritative sources relevant to ${subject || 'the question'}.`,
    'Gather the key data and context needed to answer it.',
    'Analyze the strongest evidence, including uncertainty and opposing signals.',
    'Cross-check sources and identify conflicts or information gaps.',
    'Synthesize a cited answer with clear conclusions and limitations.',
  ];
}

function researchPlanSubject(value: string): string {
  const clean = value.replace(/\s+/g, ' ').trim();
  if (!clean) return '';
  const withoutLead = clean.replace(/^(?:analy[sz]e|research|investigate|review|summari[sz]e|compare|look into)\s+/i, '');
  const subject = withoutLead
    .split(/\b(?:and how|and whether|how should|is it|over the next|for the next|what should)\b/i, 1)[0]
    .split(/[?.!]/, 1)[0]
    .trim()
    .replace(/[,:;]+$/, '');
  if (!subject || /^(?:this|that|it|the topic)$/i.test(subject)) return '';
  return subject.charAt(0).toUpperCase() + subject.slice(1);
}

function isMarketResearchQuestion(value: string): boolean {
  return /\b(?:stock|stocks|share|shares|equity|ticker|invest|investment|buy|sell|portfolio|market|valuation|earnings|options|short interest)\b/i.test(value);
}

export function normalizedPageLimit(value: unknown): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(1, Math.min(100, Math.trunc(numeric)));
}

export function researchJobOutput(job: JobRecord): JobOutput | null {
  const output = asRecord(job.output_refs?.[0]);
  if (!Object.keys(output).length) return null;
  const snapshots = arrayRecords(output.snapshots);
  const snapshotBySource = new Map(snapshots.map((snapshot) => [stringValue(snapshot.source_record_id), snapshot]));
  const sources = arrayRecords(output.sources).map((source, index) => {
    const id = stringValue(source.source_record_id) || `source-${index + 1}`;
    const snapshot = snapshotBySource.get(id);
    return {
      id,
      title: stringValue(source.title) || `Source ${index + 1}`,
      url: stringValue(source.canonical_url ?? source.original_url) || undefined,
      citation: stringValue(snapshot?.citation_label) || undefined,
      extractionStatus: stringValue(snapshot?.extraction_status) || undefined,
    };
  });
  return {
    researchStatus: stringValue(output.research_status) || undefined,
    researchProvider: stringValue(output.research_provider) || undefined,
    plannerBackend: stringValue(output.planner_backend) || undefined,
    synthesisBackend: stringValue(output.synthesis_backend) || undefined,
    stopReason: stringValue(output.stop_reason) || undefined,
    logicalQueries: numberValue(output.logical_queries) ?? undefined,
    extractedPages: numberValue(output.extracted_pages) ?? undefined,
    searchDiagnostics: arrayRecords(output.search_diagnostics).map((diagnostic) => ({
      query: stringValue(diagnostic.query) || undefined,
      provider: stringValue(diagnostic.provider) || undefined,
      status: stringValue(diagnostic.status) || undefined,
      results: numberValue(diagnostic.results) ?? undefined,
      coverage: stringValue(diagnostic.coverage) || undefined,
      error: stringValue(diagnostic.error) || undefined,
      extractionFailures: numberValue(diagnostic.extraction_failures) ?? undefined,
    })),
    sources,
    conflicts: arrayRecords(output.conflicts).map((conflict, index) => ({
      id: stringValue(conflict.conflict_id) || `conflict-${index + 1}`,
      summary: stringValue(conflict.summary) || 'Unresolved source conflict.',
    })),
    warnings: stringList(output.warnings),
  };
}

function dispatchRefreshSignal(): void {
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  emitOmnixEvent('omnix:research-job-settled', { jobId: activeJob?.id });
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function arrayRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

export function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(stringValue).filter(Boolean) : [];
}

export function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function humanizeCode(value: string): string {
  const text = value.replace(/[_-]+/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Unknown';
}

