import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules, type OmnixModuleId } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { PlatformModuleWorkspace } from './PlatformModuleWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  private readonly listeners = new Map<string, Set<EventListenerOrEventListenerObject>>();

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
    if (!listener) {
      return;
    }

    const listenersForType = this.listeners.get(type) ?? new Set<EventListenerOrEventListenerObject>();
    listenersForType.add(listener);
    this.listeners.set(type, listenersForType);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
    if (!listener) {
      return;
    }

    this.listeners.get(type)?.delete(listener);
  }

  close() {}

  emitMessage(type: string, data: string) {
    const event = new MessageEvent(type, { data });

    for (const listener of this.listeners.get(type) ?? []) {
      if (typeof listener === 'function') {
        listener(event);
      } else {
        listener.handleEvent(event);
      }
    }
  }
}

function renderPlatform(moduleId: OmnixModuleId, prepare?: (queryClient: QueryClient) => void) {
  vi.stubGlobal('EventSource', MockEventSource);
  const queryClient = createTestQueryClient();
  prepare?.(queryClient);
  const module = omnixModules.find((entry) => entry.id === moduleId);

  if (!module) {
    throw new Error(`Unknown module ${moduleId}`);
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <PlatformModuleWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function mockGateway(payloads: Record<string, unknown>) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
    const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
    const payload = payloads[path];

    if (payload === undefined) {
      return new Response('not found', { status: 404 });
    }

    return Response.json(payload);
  });

  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function jobPayload(status: string, message: string) {
  return {
    jobs: [
      {
        id: 'job-1',
        type: 'tts.generate',
        module: 'voice',
        status,
        resource_class: 'gpu:tts',
        created_at: '2026-06-14T00:00:00Z',
        updated_at: '2026-06-14T00:00:01Z',
        priority: 0,
        progress: { current: status === 'completed' ? 4 : 1, total: 4, message },
        stages: [{ id: 's1', label: 'Synthesis', status, resource_class: 'gpu:tts' }],
        logs: [{ level: 'info', message: 'started' }],
      },
    ],
  };
}

function assetPayload(includeAsset: boolean) {
  return {
    assets: includeAsset
      ? [
          {
            id: 'asset-1',
            module: 'image-generation',
            type: 'image',
            mime_type: 'image/png',
            file_name: 'image.png',
            created_at: '2026-06-14T00:00:00Z',
            source_job_id: 'job-1',
          },
        ]
      : [],
  };
}

function reportPayload(includeReport: boolean) {
  return {
    reports: includeReport
      ? [{ id: 'autoplay-report', kind: 'rpg_autoplay', path: 'reports/run.json', size_bytes: 512 }]
      : [],
  };
}

function diagnosticsPayload(status: string, logMessage: string) {
  return {
    ok: status === 'ready',
    status,
    event_stream: { status },
    workers: {
      contract_version: 'omnix_worker_health_contract_v1',
      format_version: 'omnix_gateway_foundation_v1',
      ok: status === 'ready',
      status,
      workers: [{ id: 'llm', ok: true, mocked: true, source_env: 'OMNIX_LLM_WORKER_URL', status: 'mocked', url: '' }],
    },
    logs: [{ level: 'info', message: logMessage }],
  };
}

afterEach(() => {
  MockEventSource.instances = [];
  vi.unstubAllGlobals();
});

describe('PlatformModuleWorkspace', () => {
  it('renders provider and model registry data from the generated API surface', async () => {
    mockGateway({
      '/api/providers': {
        providers: [
          {
            id: 'openai',
            label: 'OpenAI compatible',
            family: 'llm',
            source: 'settings',
            status: 'configured',
            capabilities: ['chat', 'model_discovery'],
            metadata: { latency_ms: 42 },
          },
        ],
        models: [],
      },
      '/api/models': {
        providers: [],
        models: [
          {
            id: 'local-mistral',
            label: 'Local Mistral',
            provider_id: 'local',
            location: 'local',
            capabilities: ['chat'],
            vram_hint_mb: 8192,
            metadata: { default_for: 'chat' },
          },
        ],
      },
    });

    renderPlatform('providers');
    expect(await screen.findByRole('heading', { name: 'OpenAI compatible' })).toBeInTheDocument();
    expect(screen.getByText('configured')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();

    renderPlatform('models');
    expect(await screen.findByRole('heading', { name: 'Local Mistral' })).toBeInTheDocument();
    expect(screen.getByText('8192 MB')).toBeInTheDocument();
  });

  it('enqueues provider refresh jobs from the providers module', async () => {
    const fetchMock = mockGateway({
      '/api/providers': {
        providers: [
          {
            id: 'openai',
            label: 'OpenAI compatible',
            family: 'llm',
            source: 'settings',
            status: 'configured',
            capabilities: ['chat', 'model_discovery'],
            metadata: {},
          },
        ],
        models: [],
      },
      '/api/providers/refresh': {
        id: 'job-refresh',
        type: 'providers.models.refresh',
        module: 'platform',
        status: 'queued',
        resource_class: 'cpu',
        created_at: '2026-06-14T00:00:00Z',
        updated_at: '2026-06-14T00:00:00Z',
        priority: 0,
      },
    });

    renderPlatform('providers');

    expect(await screen.findByRole('heading', { name: 'OpenAI compatible' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/providers/refresh',
        expect.objectContaining({
          body: JSON.stringify({ scope: 'providers', reason: 'web.providers.refresh', priority: 0 }),
          method: 'POST',
        }),
      );
    });
    expect(await screen.findByText('queued')).toBeInTheDocument();
  });

  it('refreshes providers and models when shared refresh job events arrive', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
      const providersCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/providers';
      }).length;
      const modelsCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/models';
      }).length;

      if (path === '/api/providers') {
        return Response.json({
          providers: [
            {
              id: 'openai',
              label: providersCallCount <= 1 ? 'OpenAI compatible' : 'OpenAI compatible refreshed',
              family: 'llm',
              source: 'settings',
              status: 'configured',
              capabilities: ['chat', 'model_discovery'],
              metadata: {},
            },
          ],
          models: [],
        });
      }

      if (path === '/api/models') {
        return Response.json({
          providers: [],
          models: [
            {
              id: 'local-mistral',
              label: modelsCallCount <= 1 ? 'Local Mistral' : 'Local Mistral refreshed',
              provider_id: 'local',
              location: 'local',
              capabilities: ['chat'],
              metadata: {},
            },
          ],
        });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('providers');
    expect(await screen.findByRole('heading', { name: 'OpenAI compatible' })).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.completed', '{"job_id":"refresh-job"}');
    expect(await screen.findByRole('heading', { name: 'OpenAI compatible refreshed' })).toBeInTheDocument();

    renderPlatform('models');
    expect(await screen.findByRole('heading', { name: 'Local Mistral' })).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.completed', '{"job_id":"refresh-job"}');
    expect(await screen.findByRole('heading', { name: 'Local Mistral refreshed' })).toBeInTheDocument();
  });

  it('renders jobs with progress and cancellation through the shared queue API', async () => {
    const fetchMock = mockGateway({
      '/api/jobs': jobPayload('running', 'Synthesizing'),
      '/api/jobs/job-1/cancel': {
        id: 'job-1',
        type: 'tts.generate',
        module: 'voice',
        status: 'cancel_requested',
        resource_class: 'gpu:tts',
        created_at: '2026-06-14T00:00:00Z',
        updated_at: '2026-06-14T00:00:02Z',
        priority: 0,
      },
    });

    renderPlatform('jobs');

    expect(await screen.findByRole('heading', { name: 'tts.generate' })).toBeInTheDocument();
    expect(screen.getByText('Synthesizing')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/jobs/job-1/cancel', expect.objectContaining({ method: 'POST' }));
    });
  });

  it('loads jobs one cursor page at a time', async () => {
    const page = (id: string, extra: Record<string, unknown>) => ({
      jobs: [{ ...jobPayload('completed', 'Done').jobs[0], id, type: `job.${id}` }],
      ...extra,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname !== '/api/jobs') return new Response('not found', { status: 404 });
      return Response.json(url.searchParams.get('cursor') === 'page-2'
        ? page('second', { has_more: false, next_cursor: null })
        : page('first', { has_more: true, next_cursor: 'page-2' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('jobs');

    expect(await screen.findByRole('heading', { name: 'job.first' })).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain('limit=50');
    fireEvent.click(screen.getByRole('button', { name: 'Load more jobs' }));

    expect(await screen.findByRole('heading', { name: 'job.second' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'job.first' })).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1][0])).toContain('cursor=page-2');
    expect(screen.queryByRole('button', { name: 'Load more jobs' })).not.toBeInTheDocument();
  });

  it('pages assets apart from the plain asset list other workspaces cache', async () => {
    mockGateway({ '/api/assets': assetPayload(true) });

    renderPlatform('assets', (queryClient) => queryClient.setQueryData(['platform', 'assets'], { assets: [] }));

    expect(await screen.findByRole('heading', { name: 'image / image-generation' })).toBeInTheDocument();
  });

  it('refreshes jobs when shared job events arrive', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
      const jobsCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/jobs';
      }).length;

      if (path === '/api/jobs') {
        return Response.json(jobsCallCount <= 1 ? jobPayload('running', 'Synthesizing') : jobPayload('completed', 'Finished'));
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('jobs');

    expect(await screen.findByText('Synthesizing')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.updated', '{"job_id":"job-1"}');

    expect(await screen.findByText('Finished')).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/jobs'))).toHaveLength(2);
  });

  it('refreshes assets when shared job completion events arrive', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
      const assetsCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/assets';
      }).length;

      if (path === '/api/assets') {
        return Response.json(assetPayload(assetsCallCount > 1));
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('assets');

    expect(await screen.findByText('No assets indexed in the shared library.')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.completed', '{"job_id":"job-1"}');

    expect(await screen.findByRole('heading', { name: 'image / image-generation' })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/assets'))).toHaveLength(2);
  });

  it('refreshes reports when shared job completion events arrive', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
      const reportsCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/reports';
      }).length;

      if (path === '/api/reports') {
        return Response.json(reportPayload(reportsCallCount > 1));
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('reports');

    expect(await screen.findByText('No generated reports found.')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.completed', '{"job_id":"job-1"}');

    expect(await screen.findByRole('heading', { name: 'autoplay-report' })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/reports'))).toHaveLength(2);
  });

  it('refreshes diagnostics when shared job events arrive', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
      const diagnosticsCallCount = fetchMock.mock.calls.filter(([callInput]) => {
        const callPath = typeof callInput === 'string' ? new URL(callInput, 'http://localhost').pathname : new URL(callInput.toString()).pathname;
        return callPath === '/api/diagnostics';
      }).length;

      if (path === '/api/diagnostics') {
        return Response.json(
          diagnosticsCallCount > 1
            ? diagnosticsPayload('ready', 'after-event')
            : diagnosticsPayload('degraded', 'before-event'),
        );
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPlatform('diagnostics');

    expect(await screen.findByText(/before-event/)).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.instances[0].emitMessage('job.updated', '{"job_id":"job-1"}');

    expect(await screen.findByText(/after-event/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/diagnostics'))).toHaveLength(2);
  });

  it('renders assets, reports, settings, and diagnostics data', async () => {
    mockGateway({
      '/api/assets': assetPayload(true),
      '/api/reports': reportPayload(true),
      '/api/settings': {
        provider: 'openai',
        audio_provider_tts: 'piper',
        audio_provider_stt: 'whisper',
        image_enabled: true,
        rpg_visual_enabled: false,
        worker_urls: { llm: 'http://127.0.0.1:9001' },
      },
      '/api/diagnostics': diagnosticsPayload('ready', 'ready'),
    });

    renderPlatform('assets');
    expect(await screen.findByRole('heading', { name: 'image / image-generation' })).toBeInTheDocument();

    renderPlatform('reports');
    expect(await screen.findByRole('heading', { name: 'autoplay-report' })).toBeInTheDocument();

    renderPlatform('settings');
    expect(await screen.findByText('openai')).toBeInTheDocument();
    expect(screen.getByLabelText('Image generation')).toBeChecked();

    renderPlatform('diagnostics');
    expect(await screen.findByRole('heading', { name: 'Gateway status' })).toBeInTheDocument();
    expect(screen.getByText('mocked')).toBeInTheDocument();
  });

  it('saves provider defaults with the revisions it loaded', async () => {
    const fetchMock = mockGateway({
      '/api/settings': {
        provider: 'lmstudio',
        audio_provider_tts: 'faster-qwen3-tts',
        audio_provider_stt: 'parakeet',
        image_enabled: false,
        rpg_visual_enabled: false,
        worker_urls: {},
        settings: {},
        revisions: { provider: 4, audio_provider_tts: 0, audio_provider_stt: 2 },
      },
    });

    renderPlatform('settings');
    await screen.findByText('lmstudio');
    fireEvent.click(screen.getByRole('button', { name: 'Save provider defaults' }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true);
    });
    const [, init] = fetchMock.mock.calls.find(([, call]) => call?.method === 'POST')!;
    expect(JSON.parse(String(init?.body))).toEqual({
      values: { provider: 'lmstudio', audio_provider_tts: 'faster-qwen3-tts', audio_provider_stt: 'parakeet' },
      revisions: { provider: 4, audio_provider_tts: 0, audio_provider_stt: 2 },
    });
  });

  it('renders core empty states when platform APIs return empty collections', async () => {
    mockGateway({
      '/api/providers': { providers: [], models: [] },
    });

    renderPlatform('providers');

    expect(await screen.findByText('No providers returned by gateway.')).toBeInTheDocument();
  });
});
