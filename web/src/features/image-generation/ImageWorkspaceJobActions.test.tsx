import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { ImageGenerationWorkspace } from './ImageGenerationWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

function renderWorkspace() {
  const module = omnixModules.find((entry) => entry.id === 'image-generation');
  if (!module) throw new Error('Image Generation module is missing');
  const queryClient = createTestQueryClient();
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <ImageGenerationWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('Image workspace job actions', () => {
  it('sends cancel and retry requests to the live job routes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') {
        return Response.json({
          providers: [{ id: 'image:flux_klein', label: 'FLUX.2 [klein] 4B', family: 'image', source: 'settings', status: 'configured', capabilities: ['image'] }],
          models: [],
        });
      }
      if (path === '/api/workers/health') return Response.json({ ok: true, status: 'ok', workers: [] });
      if (path === '/api/image-generation/model/status') {
        return Response.json({
          ok: true,
          service: 'image',
          enabled: true,
          provider: 'flux_klein',
          model: 'FLUX.2 [klein] 4B',
          loaded: true,
          state: 'loaded',
          local_model: { complete: true, missing: [], local_dir: 'resources/models/image/flux2-klein-4b' },
        });
      }
      if (path === '/api/image-generation/assets') return Response.json({ assets: [] });
      if (path === '/api/image-generation/jobs') {
        return Response.json({
          jobs: [
            {
              id: 'job-running', module: 'image-generation', type: 'image.generate', status: 'running', resource_class: 'gpu:image', priority: 0,
              created_at: '2026-06-14T00:00:00Z', updated_at: '2026-06-14T00:00:00Z', input_payload: { prompt: 'Running image' },
            },
            {
              id: 'job-failed', module: 'image-generation', type: 'image.generate', status: 'failed', resource_class: 'gpu:image', priority: 0,
              created_at: '2026-06-14T00:01:00Z', updated_at: '2026-06-14T00:01:00Z', input_payload: { prompt: 'Failed image' },
            },
          ],
        });
      }
      if (path === '/api/jobs/job-running/cancel' && init?.method === 'POST') {
        return Response.json({
          id: 'job-running', module: 'image-generation', type: 'image.generate', status: 'cancel_requested', resource_class: 'gpu:image', priority: 0,
          created_at: '2026-06-14T00:00:00Z', updated_at: '2026-06-14T00:02:00Z',
        });
      }
      if (path === '/api/image-generation/jobs/job-failed/retry' && init?.method === 'POST') {
        return Response.json({
          id: 'job-retry', module: 'image-generation', type: 'image.generate', status: 'queued', resource_class: 'gpu:image', priority: 0,
          created_at: '2026-06-14T00:02:00Z', updated_at: '2026-06-14T00:02:00Z',
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => requestPath(input as RequestInfo | URL) === '/api/jobs/job-running/cancel')).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => requestPath(input as RequestInfo | URL) === '/api/image-generation/jobs/job-failed/retry')).toBe(true));
  });
});
