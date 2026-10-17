import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ResearchJobPanel, ResearchMessageDetails, ResearchProgressCard } from './ResearchProgressCard';
import { omnixApiClient } from '../../../api/client';
import type { ChatSession, JobRecord } from '../../../api/client';

let helpers: typeof import('./researchProgressController');

beforeAll(async () => {
  helpers = await import('./researchProgressController');
});

function researchJob(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: 'job:research-one',
    module: 'assistant',
    type: 'assistant.deep_research',
    status: 'running',
    resource_class: 'network',
    priority: 0,
    stages: [
      {
        id: 'planning',
        label: 'Planning research',
        status: 'completed',
        resource_class: 'cpu',
        progress: { current: 1, total: 1, message: 'completed' },
        output_refs: [],
        retry: { attempts: 0, max_attempts: 0, policy: 'none' },
      },
      {
        id: 'searching',
        label: 'Searching the web',
        status: 'running',
        resource_class: 'network',
        progress: { current: 0, total: 1, message: 'Searching: current release' },
        output_refs: [],
        retry: { attempts: 0, max_attempts: 0, policy: 'none' },
      },
      {
        id: 'synthesizing',
        label: 'Writing the answer',
        status: 'queued',
        resource_class: 'network',
        progress: { current: 0, total: 1 },
        output_refs: [],
        retry: { attempts: 0, max_attempts: 0, policy: 'none' },
      },
    ],
    progress: { current: 1, total: 3, message: 'Searching: current release' },
    logs: [],
    output_refs: [],
    created_at: '2026-07-07T00:00:00Z',
    updated_at: '2026-07-07T00:00:01Z',
    cancel: { requested: false },
    compat: {},
    ...overrides,
  } as JobRecord;
}

function researchSession(): ChatSession {
  return {
    id: 'chat:one',
    title: 'Research chat',
    message_count: 1,
    created_at: '2026-07-07T00:00:00Z',
    updated_at: '2026-07-07T00:00:01Z',
    messages: [
      {
        id: 'msg:user',
        role: 'user',
        content: 'Research this',
        created_at: '2026-07-07T00:00:01Z',
        metadata: {
          research_mode: 'deep',
          research_status: 'queued',
          research_job_id: 'job:research-one',
        },
      },
    ],
  } as unknown as ChatSession;
}

describe('research progress restoration', () => {
  it('recovers the durable job ID from persisted chat metadata', () => {
    expect(helpers.latestResearchJobId(researchSession().messages ?? [])).toBe('job:research-one');
  });

  it('keeps a newly created approval job ahead of a stale completed job in chat history', () => {
    const pending = researchJob({
      id: 'job:new-outline',
      status: 'queued',
      input_payload: { awaiting_plan_approval: true },
    });

    expect(helpers.preferredResearchJobId(researchSession().messages ?? [], pending)).toBe('job:new-outline');
  });

  it('recovers the newest active outline for the current session from the durable ledger', () => {
    const older = researchJob({
      id: 'job:older-outline',
      status: 'queued',
      created_at: '2026-08-27T02:31:37Z',
      input_payload: { session_id: 'chat:one', awaiting_plan_approval: true },
    });
    const newest = researchJob({
      id: 'job:newest-outline',
      status: 'queued',
      created_at: '2026-08-27T03:13:31Z',
      input_payload: { session_id: 'chat:one', awaiting_plan_approval: true },
    });
    const anotherSession = researchJob({
      id: 'job:other-chat',
      status: 'queued',
      created_at: '2026-08-27T03:20:00Z',
      input_payload: { session_id: 'chat:other', awaiting_plan_approval: true },
    });

    expect(helpers.latestActiveResearchJobForSession(
      [older, anotherSession, newest],
      'chat:one',
    )?.id).toBe('job:newest-outline');
  });

  it('uses the active stage label and bounded operational announcement', () => {
    const job = researchJob();
    expect(helpers.researchStageLabel(job)).toBe('Searching the web');
    expect(helpers.researchStageAnnouncement(job)).toBe('Searching: current release');
    expect(helpers.researchStageAnnouncement(job)).not.toContain('reasoning');
    expect(helpers.isActiveResearchJob(job)).toBe(true);
  });

  it('calculates progress from completed stages and caps active jobs below 100 percent', () => {
    expect(helpers.researchProgressPercent(researchJob())).toBe(33);
    expect(helpers.researchProgressPercent(researchJob({ status: 'completed' }))).toBe(100);
  });

  it('announces cancellation and completion without internal planner details', () => {
    const canceling = researchJob({ status: 'cancel_requested' });
    const complete = researchJob({ status: 'completed' });
    expect(helpers.researchStageAnnouncement(canceling)).toContain('Cancellation requested');
    expect(helpers.researchStageAnnouncement(complete)).toContain('Research complete');
    expect(helpers.isActiveResearchJob(complete)).toBe(false);
  });

  it('announces limited evidence for completed jobs with empty research output', () => {
    const complete = researchJob({
      status: 'completed',
      output_refs: [
        {
          type: 'research',
          research_status: 'partial',
          stop_reason: 'no_reliable_sources',
          research_provider: 'duckduckgo',
          search_diagnostics: [
            {
              query: 'rtx 4090 coding llm',
              provider: 'duckduckgo',
              status: 'empty',
              results: 0,
            },
          ],
          warnings: ['limited_search_provider', 'quick_search_empty'],
        },
      ],
    });

    expect(helpers.researchStageAnnouncement(complete)).toContain('limited evidence');
    const { container: panel } = render(<ResearchJobPanel job={complete} />);
    expect(panel.textContent).toContain('Search diagnostics');
    expect(panel.textContent).toContain('rtx 4090 coding llm');
    expect(panel.textContent).toContain('0 results');
  });

  it('renders a close button for completed progress panels', () => {

    const { container: panel } = render(<ResearchJobPanel job={researchJob({ status: 'completed' })} />);
    const close = panel.querySelector<HTMLButtonElement>('[data-omnix-research-close]');

    expect(close).not.toBeNull();
    fireEvent.click(close!);
    expect(helpers.researchProgressStore.getState().dismissedJobIds.has('job:research-one')).toBe(true);
  });

  it('keeps active progress panels focused on cancellation instead of dismissal', () => {

    const { container: panel } = render(<ResearchJobPanel job={researchJob()} />);

    expect(panel.querySelector('[data-omnix-research-close]')).toBeNull();
    expect(panel.querySelector('[data-omnix-research-cancel]')).not.toBeNull();
  });

  it('shows a ChatGPT-style outline and hard page limit before a deep-research job starts', async () => {
    const job = researchJob({
      status: 'queued',
      input_payload: {
        awaiting_plan_approval: true,
        max_sources: 4,
        question: 'analyze Nvidia stock and how it may play out next month. is it a buy?',
        research_plan: {
          steps: [
            'AI planner step: collect recent Nvidia filings and financial news.',
            'AI planner step: gather current market data and price history.',
            'AI planner step: analyze earnings, guidance, and valuation.',
            'AI planner step: assess sentiment and downside risks.',
            'AI planner step: synthesize a risk-based investment approach.',
          ],
          operations: [
            { operation: 'web_search', query: 'current Iran US conflict latest' },
            { operation: 'evaluate_evidence', reason: 'Compare source claims.' },
            { operation: 'stop', reason: 'Plan complete.' },
          ],
        },
      },
    });

    const { container: panel } = render(<ResearchJobPanel job={job} />);

    expect(panel.textContent).toContain('Nvidia stock Deep Research');
    expect(panel.textContent).toContain('AI planner step: collect recent Nvidia filings');
    expect(panel.textContent).toContain('AI planner step: synthesize a risk-based investment approach');
    expect(panel.textContent).not.toContain('current Iran US conflict latest');
    expect(panel.textContent).toContain('Max pages to search');
    expect(panel.querySelector('[data-omnix-research-plan-start]')).not.toBeNull();
    const pageInput = panel.querySelector<HTMLInputElement>('[aria-label="Research plan maximum pages"]');
    expect(pageInput?.value).toBe('4');
    expect(pageInput?.readOnly).toBe(true);
    const edit = panel.querySelector<HTMLButtonElement>('[data-omnix-research-plan-update]');
    expect(edit?.textContent).toBe('Edit');
    fireEvent.click(edit!);
    expect(pageInput?.readOnly).toBe(false);
    expect(edit?.textContent).toBe('Save');
    fireEvent.click(edit!);
    await waitFor(() => expect(pageInput?.readOnly).toBe(true));
    expect(edit?.textContent).toBe('Edit');
    expect(edit?.disabled).toBe(false);
  });

  it('keeps the approved outline visible while marking completed areas', () => {
    const job = researchJob({
      status: 'running',
      input_payload: {
        max_sources: 5,
        research_plan: {
          title: 'NVIDIA Stock Buy Assessment',
          steps: [
            'Gather recent NVIDIA financial results.',
            'Investigate AI-chip demand and expected earnings.',
            'Assess competition and regulatory risks.',
            'Compare valuation with semiconductor peers.',
            'Frame conclusions for different risk tolerances.',
          ],
        },
      },
      stages: (researchJob().stages ?? []).map((stage, index) => ({
        ...stage,
        status: index === 0 ? 'completed' : index === 1 ? 'running' : 'queued',
      })),
    });

    const { container: panel } = render(<ResearchJobPanel job={job} />);

    expect(panel.textContent).toContain('NVIDIA Stock Buy Assessment');
    expect(panel.textContent).toContain('Gather recent NVIDIA financial results.');
    expect(panel.textContent).toContain('1 of 5 outline areas complete');
    expect(panel.querySelectorAll('.assistant-research-plan-list li.is-completed')).toHaveLength(1);
    expect(panel.querySelector('[data-omnix-research-cancel]')).not.toBeNull();
  });

  it('uses an explicit plan title when the planner provides one', () => {
    const job = researchJob({
      input_payload: {
        question: 'research this',
        research_plan: { title: 'Custom Research Outline' },
      },
    });

    expect(helpers.researchPlanTitle(job)).toBe('Custom Research Outline');
  });

  it('offers to restart a worker that stopped before entering its first stage', () => {
    const job = researchJob({
      input_payload: { max_sources: 5 },
      stages: (researchJob().stages ?? []).map((stage) => ({ ...stage, status: 'queued' })),
    });

    expect(helpers.isStalledResearchJob(job)).toBe(true);
    const { container: panel } = render(<ResearchJobPanel job={job} />);

    expect(panel.textContent).toContain('Research needs restarting');
    expect(panel.textContent).toContain('5-page limit');
    expect(panel.querySelector('[data-omnix-research-restart]')).not.toBeNull();
  });
});

afterEach(() => cleanup());

describe('chat workspace reports', () => {
  afterEach(() => vi.restoreAllMocks());

  it('follows a deep research job only while the controller is installed', async () => {
    const getJob = vi.spyOn(omnixApiClient, 'getJob').mockResolvedValue(researchJob({ status: 'completed' }));
    const session = { id: 'chat:research', messages: [] } as unknown as ChatSession;
    const sent = { session, user_message: {}, job: researchJob() } as unknown as Parameters<typeof helpers.noteChatMessageSent>[1];

    expect(helpers.noteChatMessageSent('chat:research', sent)).toBe(sent);
    expect(getJob).not.toHaveBeenCalled();

    const dispose = helpers.installResearchProgressController();
    try {
      helpers.noteChatMessageSent('chat:research', sent);
      await vi.waitFor(() => expect(getJob).toHaveBeenCalledWith('job:research-one'));
    } finally {
      dispose();
    }
  });
});

describe('research progress card', () => {
  afterEach(() => vi.restoreAllMocks());

  it('shows the followed job below the transcript and hides it once the answer is complete', async () => {
    const running = researchJob();
    let current = running;
    vi.spyOn(omnixApiClient, 'getJob').mockImplementation(async () => current);
    vi.spyOn(omnixApiClient, 'listJobs').mockResolvedValue({ jobs: [] } as unknown as Awaited<ReturnType<typeof omnixApiClient.listJobs>>);
    vi.spyOn(omnixApiClient, 'getChatSession').mockResolvedValue({ id: 'chat:research', messages: [] } as unknown as ChatSession);
    const dispose = helpers.installResearchProgressController();
    const { container } = render(<ResearchProgressCard />);
    try {
      helpers.noteChatMessageSent('chat:research', { session: { id: 'chat:research', messages: [] }, user_message: {}, job: running } as unknown as Parameters<typeof helpers.noteChatMessageSent>[1]);
      await waitFor(() => expect(container).toHaveTextContent('Searching the web'));

      current = researchJob({ status: 'completed' });
      await waitFor(() => expect(container).toBeEmptyDOMElement(), { timeout: 3_000 });
    } finally {
      dispose();
    }
  });

  it('lists search details under a quick-search answer only', () => {
    const quick = { id: 'm1', role: 'assistant', content: 'Answer', created_at: '', metadata: { research_mode: 'quick', research_provider: 'duckduckgo', web_search_source_count: 3 } };
    const { container, rerender } = render(<ResearchMessageDetails message={quick as never} />);
    expect(container).toHaveTextContent('Quick search details · completed');
    expect(container).toHaveTextContent('Duckduckgo');

    rerender(<ResearchMessageDetails message={{ ...quick, metadata: {} } as never} />);
    expect(container).toBeEmptyDOMElement();
  });
});
