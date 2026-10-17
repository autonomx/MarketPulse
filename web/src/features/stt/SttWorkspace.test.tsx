import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { SttWorkspace } from './SttWorkspace';
import { createTestQueryClient, renderWithProviders, stubGateway } from '../../test/renderWithProviders';

function renderStt() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'stt');

  if (!module) {
    throw new Error('STT module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <SttWorkspace module={module} />
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

describe('SttWorkspace', () => {
  it('queues STT transcription jobs through the shared jobs API', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json({
          providers: [
            {
              id: 'parakeet',
              label: 'Parakeet STT',
              family: 'stt',
              source: 'settings',
              status: 'configured',
              capabilities: ['stt'],
            },
          ],
          models: [],
        });
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json({
          id: 'job:stt',
          module: 'stt',
          type: 'stt.transcribe',
          status: 'queued',
          resource_class: 'gpu:stt',
          created_at: '2026-06-14T00:00:01Z',
          updated_at: '2026-06-14T00:00:01Z',
          priority: 0,
        });
      }

      if (path === '/api/jobs') {
        return Response.json({ jobs: [] });
      }

      if (path === '/api/assets') {
        return Response.json({
          assets: [
            {
              id: 'asset:audio',
              module: 'voice',
              type: 'audio',
              mime_type: 'audio/wav',
              file_name: 'input.wav',
              created_at: '2026-06-14T00:00:00Z',
            },
            {
              id: 'asset:transcript',
              module: 'stt',
              type: 'transcript',
              mime_type: 'text/plain',
              file_name: 'transcript.txt',
              created_at: '2026-06-14T00:00:00Z',
            },
          ],
        });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderStt();

    expect(await screen.findByRole('heading', { name: 'Transcription' })).toBeInTheDocument();
    expect(await screen.findByText('Parakeet STT')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'transcript / stt' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'parakeet' } });
    fireEvent.change(screen.getByLabelText('Audio asset'), { target: { value: 'asset:audio' } });
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'en' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue transcription' }));

    expect(await screen.findByText('STT job queued: job:stt')).toBeInTheDocument();

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST',
      );
      expect(createCall?.[1]?.body).toContain('"module":"stt"');
      expect(createCall?.[1]?.body).toContain('"type":"stt.transcribe"');
      expect(createCall?.[1]?.body).toContain('"resource_class":"gpu:stt"');
      expect(createCall?.[1]?.body).toContain('"asset_id":"asset:audio"');
      expect(createCall?.[1]?.body).toContain('"provider_id":"parakeet"');
    });
  });

  it('shows why a transcription could not be queued', async () => {
    stubGateway({
      '/api/providers': () => ({ providers: [{ id: 'parakeet', label: 'Parakeet STT', family: 'stt', source: 'settings', status: 'configured', capabilities: ['stt'] }], models: [] }),
      'GET /api/jobs': () => ({ jobs: [] }),
      'POST /api/jobs': () => Response.json({ detail: 'The STT worker is offline.' }, { status: 503 }),
      '/api/assets': () => ({ assets: [{ id: 'asset:audio', module: 'voice', type: 'audio', mime_type: 'audio/wav', file_name: 'input.wav', created_at: '2026-06-14T00:00:00Z' }] }),
    });
    const module = omnixModules.find((entry) => entry.id === 'stt');
    if (!module) throw new Error('STT module is missing');
    renderWithProviders(<SttWorkspace module={module} />);

    expect(await screen.findByText('Parakeet STT')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'parakeet' } });
    fireEvent.change(screen.getByLabelText('Audio asset'), { target: { value: 'asset:audio' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue transcription' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('STT request');
    expect(screen.queryByText(/STT job queued/)).not.toBeInTheDocument();
  });
});
