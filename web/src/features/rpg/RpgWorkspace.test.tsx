import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { buildSubmittedTurnStoryMessages, RpgWorkspace } from './RpgWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderRpg() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'rpg');

  if (!module) {
    throw new Error('RPG module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <RpgWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

const emptyWorkspaceResponses = {
  inventory: {
    sessions: [{ session_id: 'rpg-session-1', updated_at: '2026-06-14T00:00:00Z' }],
    diagnostics: [],
  },
  jobs: { jobs: [] },
  assets: {
    assets: [
      {
        id: 'asset:checkpoint',
        module: 'rpg',
        type: 'rpg_checkpoint',
        mime_type: 'application/json',
        file_name: 'session.json',
        created_at: '2026-06-14T00:00:00Z',
      },
    ],
  },
  reports: {
    reports: [{ id: 'rpg/autoplay.json', kind: 'rpg_autoplay', path: 'reports/rpg/autoplay.json', size_bytes: 32 }],
  },
};

function stubEmptyWorkspaceFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);

    if (path === '/api/rpg/sessions') {
      return Response.json(emptyWorkspaceResponses.inventory);
    }

    if (path === '/api/jobs') {
      return Response.json(emptyWorkspaceResponses.jobs);
    }

    if (path === '/api/assets') {
      return Response.json(emptyWorkspaceResponses.assets);
    }

    if (path === '/api/reports') {
      return Response.json(emptyWorkspaceResponses.reports);
    }

    return new Response(init?.method ?? 'not found', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
  vi.unstubAllGlobals();
  document.documentElement.classList.remove('rpg-play-focus-mode');
});

describe('RpgWorkspace', () => {
  it('does not render foreground-record job content as a second story response', () => {
    const job = {
      type: 'rpg.turn.foreground_record',
      status: 'completed',
      output_refs: [{ content: 'Stale combined fallback response.' }],
    } as unknown as Parameters<typeof buildSubmittedTurnStoryMessages>[0];

    expect(buildSubmittedTurnStoryMessages(job, 'Alyndra', 'A', 'session:bran')).toEqual([]);
  });

  it('restores the last selected live session after refresh instead of defaulting to the newest demo', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'ongoing-session');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Response.json({
          sessions: [
            {
              session_id: 'demo-session',
              title: 'Demo Session',
              location: 'Glimmerdeep Pass',
              updated_at: '2026-06-24T02:00:00Z',
            },
            {
              session_id: 'ongoing-session',
              title: 'Ongoing Campaign',
              location: 'Rusty Flagon Tavern',
              updated_at: '2026-06-23T02:00:00Z',
            },
          ],
          diagnostics: [],
        });
      }

      if (path === '/api/rpg/sessions/ongoing-session') {
        return Response.json({
          ok: true,
          session_id: 'ongoing-session',
          session: {
            session_id: 'ongoing-session',
            title: 'Ongoing Campaign',
            location: 'Rusty Flagon Tavern',
            updated_at: '2026-06-23T02:00:00Z',
            turn_count: 4,
          },
        });
      }

      if (path === '/api/rpg/sessions/demo-session') {
        return Response.json({
          ok: true,
          session_id: 'demo-session',
          session: {
            session_id: 'demo-session',
            title: 'Demo Session',
            location: 'Glimmerdeep Pass',
            updated_at: '2026-06-24T02:00:00Z',
            turn_count: 12,
          },
        });
      }

      if (path === '/api/jobs') return Response.json(emptyWorkspaceResponses.jobs);
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/reports') return Response.json({ reports: [] });

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('ongoing-session'));
    expect(window.localStorage.getItem('omnix:rpg:selected-session-id')).toBe('ongoing-session');

    await waitFor(() => {
      const sessionPaths = fetchMock.mock.calls
        .map(([input]) => requestPath(input as RequestInfo | URL))
        .filter((path) => path.startsWith('/api/rpg/sessions/'));
      expect(sessionPaths).toContain('/api/rpg/sessions/ongoing-session');
      expect(sessionPaths).not.toContain('/api/rpg/sessions/demo-session');
    });
  });

  it('repairs a stale stored RPG session id to the first indexed live session', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'missing-session');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Response.json({
          sessions: [
            {
              session_id: 'indexed-session',
              title: 'Indexed Campaign',
              location: 'North Road',
              updated_at: '2026-06-24T02:00:00Z',
            },
          ],
          diagnostics: [],
        });
      }

      if (path === '/api/rpg/sessions/indexed-session') {
        return Response.json({
          ok: true,
          session_id: 'indexed-session',
          session: {
            session_id: 'indexed-session',
            title: 'Indexed Campaign',
            location: 'North Road',
            updated_at: '2026-06-24T02:00:00Z',
            turn_count: 8,
          },
        });
      }

      if (path === '/api/jobs') return Response.json(emptyWorkspaceResponses.jobs);
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/reports') return Response.json({ reports: [] });

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('indexed-session'));
    await waitFor(() => expect(window.localStorage.getItem('omnix:rpg:selected-session-id')).toBe('indexed-session'));
  });

  it('uses replay inventory and queues RPG turns through shared jobs', async () => {
    let turnQueued = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Response.json(emptyWorkspaceResponses.inventory);
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        turnQueued = true;
        return Response.json({
          id: 'job:rpg',
          module: 'rpg',
          type: 'rpg.turn',
          status: 'queued',
          resource_class: 'gpu:llm',
          created_at: '2026-06-14T00:00:01Z',
          updated_at: '2026-06-14T00:00:01Z',
          priority: 0,
        });
      }

      if (path === '/api/jobs') {
        if (turnQueued) {
          return new Promise<Response>(() => undefined);
        }
        return Response.json(emptyWorkspaceResponses.jobs);
      }

      if (path === '/api/assets') {
        return Response.json(emptyWorkspaceResponses.assets);
      }

      if (path === '/api/reports') {
        return Response.json(emptyWorkspaceResponses.reports);
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    expect(await screen.findByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'rpg-session-1' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'rpg_checkpoint / rpg' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));

    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Look around the tavern.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    expect(await screen.findByText('RPG turn job queued: job:rpg')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Queue RPG turn' })).toBeEnabled();

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST',
      );
      expect(createCall?.[1]?.body).toContain('"module":"rpg"');
      expect(createCall?.[1]?.body).toContain('"type":"rpg.turn"');
      expect(createCall?.[1]?.body).toContain('"determinism_policy":"replay_preserving"');
      expect(createCall?.[1]?.body).toContain('"session_id":"rpg-session-1"');
    });
  });

  it('surfaces a timeout when the RPG turn queue request is not acknowledged', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.inventory));
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
        });
      }

      if (path === '/api/jobs') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.jobs));
      }

      if (path === '/api/assets') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.assets));
      }

      if (path === '/api/reports') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.reports));
      }

      return Promise.resolve(new Response('not found', { status: 404 }));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    expect(await screen.findByRole('heading', { name: 'Turn request' })).toBeInTheDocument();

    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Look around the tavern.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    await vi.waitFor(() => {
      expect(screen.getByText('Queueing RPG turn job…')).toBeInTheDocument();
    });

    await vi.advanceTimersByTimeAsync(45_000);

    await vi.waitFor(() => {
      expect(screen.getByText('Still checking the RPG job queue for this turn...')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Gateway did not acknowledge/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Queueing RPG turn' })).toBeDisabled();
  });

  it('recovers a timed out RPG turn queue request from the matching polled job', async () => {
    const failedJob = {
      id: 'job:rpg-recovered-failed',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'failed',
      resource_class: 'gpu:llm',
      created_at: '',
      updated_at: '',
      completed_at: '',
      priority: 0,
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: {
        command: 'Ask Bran how business is going.',
        determinism_policy: 'replay_preserving',
      },
      output_refs: [],
      error: {
        code: 'inline_job_failed',
        message: 'Authoritative RPG turn did not produce a visible response',
        retryable: true,
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.inventory));
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return Promise.reject(
          new Error(
            'The RPG turn queue request is taking longer than expected. The workspace will keep checking the RPG job queue for this turn.',
          ),
        );
      }

      if (path === '/api/jobs') {
        return Promise.resolve(Response.json({ jobs: [failedJob] }));
      }

      if (path === '/api/assets') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.assets));
      }

      if (path === '/api/reports') {
        return Promise.resolve(Response.json(emptyWorkspaceResponses.reports));
      }

      return Promise.resolve(new Response('not found', { status: 404 }));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    expect(await screen.findByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));

    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Ask Bran how business is going.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'RPG turn failed: Authoritative RPG turn did not produce a visible response',
      );
    });
    expect(screen.queryByText(/Gateway did not acknowledge/)).not.toBeInTheDocument();
  });

  it('keeps a newly submitted RPG turn at the end of the conversation while pending', async () => {
    const previousTurnJob = {
      id: 'job:rpg-previous-turn',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'completed',
      resource_class: 'gpu:llm',
      priority: 0,
      stages: [],
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: { command: 'Ask Bran about yesterday.' },
      output_refs: [{
        type: 'rpg_turn_response',
        content: 'Bran nods at the ledger.',
      }],
      created_at: '2026-06-14T00:00:01Z',
      updated_at: '2026-06-14T00:00:04Z',
      completed_at: '2026-06-14T00:00:04Z',
    };
    const pendingTurnJob = {
      id: 'job:rpg-new-pending',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'queued',
      resource_class: 'gpu:llm',
      priority: 0,
      stages: [],
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: { command: 'Ask Bran how business is going.' },
      output_refs: [],
      created_at: '2026-06-14T00:00:05Z',
      updated_at: '2026-06-14T00:00:05Z',
      completed_at: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') return Response.json(emptyWorkspaceResponses.inventory);
      if (path === '/api/jobs' && init?.method === 'POST') return Response.json(pendingTurnJob);
      if (path === '/api/jobs') return Response.json({ jobs: [previousTurnJob] });
      if (path.startsWith('/api/jobs/job%3Arpg-new-pending')) return Response.json(pendingTurnJob);
      if (path === '/api/assets') return Response.json(emptyWorkspaceResponses.assets);
      if (path === '/api/reports') return Response.json(emptyWorkspaceResponses.reports);
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));

    const conversation = screen.getByLabelText('Conversation');
    const previousResponse = await within(conversation).findByText('Bran nods at the ledger.');

    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Ask Bran how business is going.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    const pendingCommand = await within(conversation).findByText('Ask Bran how business is going.');
    expect(previousResponse.compareDocumentPosition(pendingCommand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders the submitted RPG turn from the direct job poll when the job list is stale', async () => {
    let turnQueued = false;
    const completedJob = {
      id: 'job:rpg-direct-completed',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'completed',
      resource_class: 'gpu:llm',
      priority: 0,
      stages: [],
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: { command: 'Ask Bran how business is going.' },
      output_refs: [{
        type: 'rpg_turn_response',
        content: 'Bran glances toward the hearth.\n\nBran: "Business is steady enough tonight."',
      }],
      created_at: '2026-06-14T00:00:01Z',
      updated_at: '2026-06-14T00:00:04Z',
      completed_at: '2026-06-14T00:00:04Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') return Response.json(emptyWorkspaceResponses.inventory);
      if (path === '/api/jobs' && init?.method === 'POST') {
        turnQueued = true;
        return Response.json({ ...completedJob, status: 'queued', output_refs: [] });
      }
      if (path === '/api/jobs') return Response.json({ jobs: [] });
      if (path.startsWith('/api/jobs/job%3Arpg-direct-completed') && turnQueued) {
        return Response.json(completedJob);
      }
      if (path === '/api/assets') return Response.json(emptyWorkspaceResponses.assets);
      if (path === '/api/reports') return Response.json(emptyWorkspaceResponses.reports);
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));
    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Ask Bran how business is going.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    expect(await screen.findByText('Ask Bran how business is going.')).toBeInTheDocument();
    expect(await screen.findByText(/Business is steady enough tonight/)).toBeInTheDocument();
  });

  it('does not render empty array submitted RPG responses as narrator text', async () => {
    let turnQueued = false;
    const completedJob = {
      id: 'job:rpg-empty-response',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'completed',
      resource_class: 'gpu:llm',
      priority: 0,
      stages: [],
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: { command: 'Listen to the hearth-side conversation' },
      output_refs: [{
        type: 'rpg_turn_response',
        content: '[]',
      }],
      created_at: '2026-06-14T00:00:01Z',
      updated_at: '2026-06-14T00:00:04Z',
      completed_at: '2026-06-14T00:00:04Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') return Response.json(emptyWorkspaceResponses.inventory);
      if (path === '/api/jobs' && init?.method === 'POST') {
        turnQueued = true;
        return Response.json({ ...completedJob, status: 'queued', output_refs: [] });
      }
      if (path === '/api/jobs') return Response.json({ jobs: [] });
      if (path.startsWith('/api/jobs/job%3Arpg-empty-response') && turnQueued) {
        return Response.json(completedJob);
      }
      if (path === '/api/assets') return Response.json(emptyWorkspaceResponses.assets);
      if (path === '/api/reports') return Response.json(emptyWorkspaceResponses.reports);
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));
    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Listen to the hearth-side conversation' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    const conversation = screen.getByLabelText('Conversation');
    expect(await within(conversation).findByText('Listen to the hearth-side conversation')).toBeInTheDocument();
    expect(within(conversation).queryByText('[]')).not.toBeInTheDocument();
    expect(within(conversation).queryByText('Omnix (Narrator)')).not.toBeInTheDocument();
  });

  it('surfaces a terminal RPG turn job failure', async () => {
    let turnQueued = false;
    const failedJob = {
      id: 'job:rpg-failed',
      module: 'rpg',
      type: 'rpg.turn',
      status: 'failed',
      resource_class: 'gpu:llm',
      created_at: '2026-06-14T00:00:01Z',
      updated_at: '2026-06-14T00:00:02Z',
      completed_at: '2026-06-14T00:00:02Z',
      priority: 0,
      input_ref: { session_id: 'rpg-session-1' },
      input_payload: { command: 'Ask Bran about business.' },
      error: {
        code: 'inline_job_failed',
        message: 'Progression state is invalid',
        retryable: false,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') return Response.json(emptyWorkspaceResponses.inventory);
      if (path === '/api/jobs' && init?.method === 'POST') {
        turnQueued = true;
        return Response.json({ ...failedJob, status: 'queued', error: null, completed_at: null });
      }
      if (path === '/api/jobs') return Response.json({ jobs: turnQueued ? [failedJob] : [] });
      if (path === '/api/assets') return Response.json(emptyWorkspaceResponses.assets);
      if (path === '/api/reports') return Response.json(emptyWorkspaceResponses.reports);
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();
    await waitFor(() => expect(screen.getByLabelText('Session')).toHaveValue('rpg-session-1'));
    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Ask Bran about business.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'RPG turn failed: Progression state is invalid',
    );
    expect(screen.queryByText('RPG turn job queued: job:rpg-failed')).not.toBeInTheDocument();
  });

  it('wires checkpoint and autoplay controls through replay-safe APIs', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Response.json({
          sessions: [
            {
              session_id: 'rpg-session-2',
              title: 'North Road Campaign',
              location: 'North Road',
              summary: 'The party waits near the northern milestone.',
              turn_count: 8,
              checkpoint_id: 'checkpoint:last',
              updated_at: '2026-06-14T00:05:00Z',
            },
          ],
          diagnostics: [],
        });
      }

      if (path === '/api/replay/checkpoints' && init?.method === 'POST') {
        return Response.json({ checkpoint_id: 'checkpoint:manual', metadata: {}, payload: {} });
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json({
          id: 'job:autoplay',
          module: 'rpg',
          type: 'rpg.autoplay',
          status: 'queued',
          resource_class: 'gpu:llm',
          created_at: '2026-06-14T00:05:01Z',
          updated_at: '2026-06-14T00:05:01Z',
          priority: 0,
        });
      }

      if (path === '/api/jobs') {
        return Response.json(emptyWorkspaceResponses.jobs);
      }

      if (path === '/api/assets') {
        return Response.json({ assets: [] });
      }

      if (path === '/api/reports') {
        return Response.json({ reports: [] });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    expect(await screen.findByText('North Road')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Create checkpoint' }));
    expect(await screen.findByText('Checkpoint created: checkpoint:manual')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' }));

    await waitFor(() => {
      const checkpointCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/replay/checkpoints' && init?.method === 'POST',
      );
      const autoplayCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST',
      );

      expect(String(checkpointCall?.[1]?.body)).toContain('"source":"rpg-workspace"');
      expect(String(checkpointCall?.[1]?.body)).toContain('"session_id":"rpg-session-2"');
      expect(String(autoplayCall?.[1]?.body)).toContain('"type":"rpg.autoplay"');
      expect(String(autoplayCall?.[1]?.body)).toContain('"determinism_policy":"replay_preserving"');
      expect(String(autoplayCall?.[1]?.body)).toContain('"session_id":"rpg-session-2"');
    });
  });

  it('keeps both rails expanded while preserving accessible hide controls', async () => {
    stubEmptyWorkspaceFetch();

    renderRpg();

    expect(screen.getByRole('button', { name: 'Hide header' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide player rail' })).toBeInTheDocument();
    expect(await screen.findByRole('complementary', { name: 'Player, party, and quests' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'World, jobs, and reports' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Contain player rail' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Contain world rail' })).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Player, party, and quests' })).toHaveClass('rpg-rail-expanded');
    expect(screen.getByRole('complementary', { name: 'World, jobs, and reports' })).toHaveClass('rpg-rail-expanded');

    fireEvent.click(screen.getByRole('button', { name: 'Hide player rail' }));
    fireEvent.click(screen.getByRole('button', { name: 'Hide world rail' }));

    expect(screen.queryByRole('complementary', { name: 'Player, party, and quests' })).not.toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'World, jobs, and reports' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show player rail' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Show world rail' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('heading', { name: 'Turn request' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show player rail' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show world rail' }));

    expect(screen.getByRole('complementary', { name: 'Player, party, and quests' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'World, jobs, and reports' })).toBeInTheDocument();
  });

  it('keeps campaign and layout controls in one header while runtime context can be hidden', async () => {
    stubEmptyWorkspaceFetch();

    renderRpg();

    expect(await screen.findByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.documentElement).not.toHaveClass('rpg-play-focus-mode');
    });
    expect(screen.queryByRole('button', { name: 'New Campaign' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Campaign Menu' })).toBeInTheDocument();
    expect(within(screen.getByRole('banner', { name: 'Campaign menu header' })).getByRole('button', { name: 'Campaign Menu' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide player rail' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand live data' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Expand header' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide header' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Hide header' }));

    await waitFor(() => {
      expect(document.documentElement).toHaveClass('rpg-play-focus-mode');
    });
    expect(screen.getByRole('button', { name: 'Show header' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Campaign Menu' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide player rail' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand live data' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
  });

  it('surfaces live data empty and error states while keeping preview fallback usable', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/rpg/sessions') {
        return Response.json({ sessions: [], diagnostics: [] });
      }

      if (path === '/api/jobs') {
        return new Response('job queue unavailable', { status: 500 });
      }

      if (path === '/api/assets') {
        return Response.json({ assets: [] });
      }

      if (path === '/api/reports') {
        return Response.json({ reports: [] });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();

    expect(screen.getByRole('button', { name: 'Expand live data' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('region', { name: 'RPG live data status' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand live data' }));

    expect(await screen.findByRole('region', { name: 'RPG live data status' })).toBeInTheDocument();
    expect(await screen.findByText('1 source need attention')).toBeInTheDocument();
    expect(await screen.findByText('Omnix API request failed with status 500')).toBeInTheDocument();
    expect(screen.getByLabelText('Sessions status')).toHaveTextContent('Empty');
    expect(screen.getByLabelText('Jobs status')).toHaveTextContent('Error');
    expect(screen.getByLabelText('Checkpoints status')).toHaveTextContent('Empty');
    expect(screen.getByLabelText('Reports status')).toHaveTextContent('Empty');
    expect(screen.getByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
  });
});
