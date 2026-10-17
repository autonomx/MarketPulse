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

describe('Image Generation model residency wiring', () => {
  it('loads and unloads FLUX through the gateway and gates generation', async () => {
    let loaded = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') {
        return Response.json({
          providers: [{ id: 'image:flux_klein', label: 'FLUX.2 [klein] 4B', family: 'image', source: 'settings', status: 'configured', capabilities: ['image'] }],
          models: [],
        });
      }
      if (path === '/api/settings/profile') return Response.json({ success: true, provider: '', audio_provider_tts: '', audio_provider_stt: '', settings: {} });
      if (path === '/api/workers/health') {
        return Response.json({
          ok: true,
          status: 'ready',
          workers: [{ id: 'image', ok: true, status: 'ready', capabilities: ['image'], mocked: false }],
        });
      }
      if (path === '/api/image-generation/model/status') {
        return Response.json({
          ok: true,
          service: 'image',
          enabled: true,
          provider: 'flux_klein',
          model: 'FLUX.2 [klein] 4B',
          loaded,
          state: loaded ? 'loaded' : 'unloaded',
          explicit_load_required: true,
          local_model: { ok: true, exists: true, complete: true, missing: [], local_dir: 'resources/models/image/flux2-klein-4b' },
        });
      }
      if (path === '/api/image-generation/model/load' && init?.method === 'POST') {
        loaded = true;
        return Response.json({ ok: true, provider: 'flux_klein', loaded: true });
      }
      if (path === '/api/image-generation/model/unload' && init?.method === 'POST') {
        loaded = false;
        return Response.json({ ok: true, provider: 'flux_klein', loaded: false, unloaded: true });
      }
      if (path === '/api/image-generation/jobs') return Response.json({ jobs: [] });
      if (path === '/api/image-generation/assets') return Response.json({ assets: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();

    const loadButton = await screen.findByRole('button', { name: 'Load Model' });
    await waitFor(() => expect(loadButton).toBeEnabled());
    const generateButton = screen.getByRole('button', { name: 'Generate image' });
    expect(generateButton).toBeDisabled();
    expect(generateButton).toHaveAttribute('title', 'Load FLUX.2 [klein] 4B before generating an image.');

    fireEvent.click(loadButton);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unload Model' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Generate image' })).not.toBeDisabled();
    expect(fetchMock.mock.calls.some(([input, init]) => requestPath(input as RequestInfo | URL) === '/api/image-generation/model/load' && init?.method === 'POST')).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Unload Model' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Load Model' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Generate image' })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([input, init]) => requestPath(input as RequestInfo | URL) === '/api/image-generation/model/unload' && init?.method === 'POST')).toBe(true);
  });
});
