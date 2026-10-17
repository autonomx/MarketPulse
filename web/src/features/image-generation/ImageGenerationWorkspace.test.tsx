import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { JobListResponse, JobRecord } from '../../api/client';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { completedImageAssetIds, hasActiveImageJobs, ImageGenerationWorkspace, isCompletedImageJobEventPayload, isImageJobEventPayload } from './ImageGenerationWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderImageGeneration() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'image-generation');

  if (!module) {
    throw new Error('Image Generation module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <ImageGenerationWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ImageGenerationWorkspace', () => {
  it('queues image jobs, keeps the model resident, and reads bounded workspace projections', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json({
          providers: [
            {
              id: 'image:flux_klein',
              label: 'FLUX.2 [klein] 4B',
              family: 'image',
              source: 'settings',
              status: 'configured',
              capabilities: ['image'],
            },
            {
              id: 'rpg_visual:flux',
              label: 'RPG visual provider',
              family: 'rpg_visual',
              source: 'settings',
              status: 'configured',
              capabilities: ['image'],
            },
          ],
          models: [],
        });
      }

      if (path === '/api/image-generation/model/status') {
        return Response.json({
          ok: true,
          service: 'image',
          enabled: true,
          provider: 'flux_klein',
          model: 'FLUX.2 [klein] 4B',
          loaded: true,
          state: 'loaded',
          local_model: { ok: true, exists: true, complete: true, missing: [], local_dir: 'resources/models/image/flux2-klein-4b' },
        });
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json({
          id: 'job:image',
          module: 'image-generation',
          type: 'image.generate',
          status: 'queued',
          resource_class: 'gpu:image',
          created_at: '2026-06-14T00:00:01Z',
          updated_at: '2026-06-14T00:00:01Z',
          priority: 0,
        });
      }

      if (path === '/api/image-generation/jobs') {
        return Response.json({ jobs: [] });
      }

      if (path === '/api/image-generation/assets') {
        return Response.json({
          assets: [
            {
              id: 'asset:image',
              module: 'image-generation',
              type: 'image',
              mime_type: 'image/png',
              file_name: 'image.png',
              created_at: '2026-06-14T00:00:00Z',
            },
          ],
        });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderImageGeneration();

    expect(await screen.findByRole('heading', { name: 'Image request' })).toBeInTheDocument();
    expect(await screen.findByText('FLUX.2 [klein] 4B')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Unload Model' })).toBeInTheDocument();
    expect(screen.queryByText('RPG visual provider')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Size preset')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select Generated image' })).toBeInTheDocument();

    expect(screen.getByLabelText('Provider')).toHaveValue('image:flux_klein');
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'A bright workstation render.' } });
    fireEvent.change(screen.getByLabelText('Style'), { target: { value: 'cinematic' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate image' }));

    expect(await screen.findByText('Image job queued: job:image')).toBeInTheDocument();

    await waitFor(() => {
      const paths = fetchMock.mock.calls.map(([input]) => requestPath(input as RequestInfo | URL));
      expect(paths).toContain('/api/image-generation/model/status');
      expect(paths).toContain('/api/image-generation/jobs');
      expect(paths).toContain('/api/image-generation/assets');
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST',
      );
      expect(createCall?.[1]?.body).toContain('"module":"image-generation"');
      expect(createCall?.[1]?.body).toContain('"type":"image.generate"');
      expect(createCall?.[1]?.body).toContain('"resource_class":"gpu:image"');
      expect(createCall?.[1]?.body).toContain('"provider_id":"image:flux_klein"');
      expect(createCall?.[1]?.body).toContain('"style":"cinematic"');
      expect(createCall?.[1]?.body).toContain('"unload_after_generation":false');
    });
  });
});

describe('image job synchronization helpers', () => {
  it('recognizes image events and rejects unrelated events', () => {
    expect(isImageJobEventPayload({ payload: { type: 'image.generate', module: 'image-generation' } })).toBe(true);
    expect(isImageJobEventPayload({ payload: { type: 'tts.synthesize', module: 'voice' } })).toBe(false);
    expect(isImageJobEventPayload({ payload: null })).toBe(false);
  });

  it('recognizes completed image data even when it arrives as job.updated', () => {
    const completedPayload = {
      payload: {
        type: 'image.generate',
        module: 'image-generation',
        status: 'completed',
        output_refs: [{ type: 'image', asset_id: 'asset:new' }],
      },
    };
    expect(isCompletedImageJobEventPayload(completedPayload)).toBe(true);
    expect(isCompletedImageJobEventPayload({ payload: { status: 'running' } })).toBe(false);
  });

  it('extracts unique completed output assets for immediate gallery refresh', () => {
    const jobs = [
      {
        id: 'job-one',
        status: 'completed',
        output_refs: [{ type: 'image', asset_id: 'asset-one' }],
      },
      {
        id: 'job-two',
        status: 'completed',
        output_refs: [
          { type: 'image', asset_id: 'asset-one' },
          { type: 'image', asset_id: 'asset-two' },
        ],
      },
      {
        id: 'job-running',
        status: 'running',
        output_refs: [{ type: 'image', asset_id: 'asset-ignored' }],
      },
    ] as unknown as JobRecord[];

    expect(completedImageAssetIds(jobs)).toEqual(['asset-one', 'asset-two']);
  });

  it('polls only while an image job is active', () => {
    const active = { jobs: [{ status: 'running' }] } as unknown as JobListResponse;
    const completed = { jobs: [{ status: 'completed' }] } as unknown as JobListResponse;

    expect(hasActiveImageJobs(active)).toBe(true);
    expect(hasActiveImageJobs(completed)).toBe(false);
    expect(hasActiveImageJobs(undefined)).toBe(false);
  });
});
