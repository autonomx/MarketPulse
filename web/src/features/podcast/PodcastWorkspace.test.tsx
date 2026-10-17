import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { PodcastWorkspace } from './PodcastWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

const GENERATED_AUDIO_DATA_URL = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=';
const STITCHED_AUDIO_BLOB_URL = 'blob:stitched-podcast-preview';

function renderPodcast() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'podcast');

  if (!module) {
    throw new Error('Podcast module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <PodcastWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

function requestBody(init: RequestInit | undefined): Record<string, unknown> {
  return JSON.parse(String(init?.body || '{}'));
}

function responseFromDataUrl(value: string): Response {
  const [, metadata = '', payload = ''] = value.match(/^data:([^,]*),(.*)$/) ?? [];
  const isBase64 = metadata.includes(';base64');
  const binary = isBase64 ? atob(payload) : decodeURIComponent(payload);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const mimeType = metadata.split(';')[0] || 'application/octet-stream';
  return new Response(bytes, { headers: { 'Content-Type': mimeType } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('PodcastWorkspace', () => {
  it('queues podcast voice generation through the Voice Studio TTS path and exposes playback audio', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue(STITCHED_AUDIO_BLOB_URL);
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (typeof input === 'string' && input.startsWith('data:audio/')) {
        return responseFromDataUrl(input);
      }

      const path = requestPath(input);

      if (path === '/api/assets') {
        return Response.json({
          assets: [
            {
              id: 'voice-cloning:alex',
              module: 'voice-cloning',
              type: 'voice_profile',
              mime_type: 'audio/wav',
              file_name: 'alex.wav',
              metadata: { profile_name: 'Alex Voice' },
              created_at: '2026-06-14T00:00:00Z',
            },
          ],
        });
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        return Response.json({
          id: 'job:podcast',
          module: 'podcast',
          type: body.type,
          status: 'completed',
          resource_class: body.resource_class,
          input_payload: body.input_payload,
          stages: body.stages,
          output_refs: [
            {
              type: 'audio',
              asset_id: 'audio:podcast-job',
              title: 'Generated podcast audio',
              data_url: GENERATED_AUDIO_DATA_URL,
              duration: 2,
              mime_type: 'audio/wav',
            },
          ],
          created_at: '2026-06-14T00:00:01Z',
          updated_at: '2026-06-14T00:00:01Z',
          priority: 0,
        });
      }

      if (path === '/api/jobs') {
        return Response.json({ jobs: [] });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPodcast();

    expect(await screen.findByRole('heading', { name: '1. Episode setup' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /New podcast/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole('option', { name: '2 min' })).toBeInTheDocument();
    expect((await screen.findAllByText('Alex Voice')).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: '2. Participants and voice casting' })).toBeInTheDocument();
    expect(screen.getByText('Voice')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Topic / Episode title'), { target: { value: 'Signals' } });
    fireEvent.change(screen.getByLabelText(/Episode brief/), { target: { value: 'Discuss local AI workstation design.' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate live podcast/i }));

    expect((await screen.findAllByText('Podcast audio ready: job:podcast')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Live preview stitched|Stitched live preview/i)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Podcast audio player').querySelectorAll('audio')).toHaveLength(1);
    await waitFor(() => expect(screen.getByLabelText('Podcast audio player').querySelector('audio')?.getAttribute('src')).toBe(STITCHED_AUDIO_BLOB_URL));

    await waitFor(() => {
      const previewCall = fetchMock.mock.calls.find(
        ([input, init]) => typeof input === 'string'
          && requestPath(input as RequestInfo | URL) === '/api/jobs'
          && init?.method === 'POST'
          && requestBody(init).type === 'tts.synthesize',
      );
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => typeof input === 'string'
          && requestPath(input as RequestInfo | URL) === '/api/jobs'
          && init?.method === 'POST'
          && requestBody(init).type === 'tts.multi_speaker_synthesize',
      );
      expect(previewCall?.[1]?.body).toContain('"renderer":"podcast-live-preview"');
      expect(previewCall?.[1]?.body).toContain('"voice_mapping"');
      expect(createCall?.[1]?.body).toContain('"module":"podcast"');
      expect(createCall?.[1]?.body).toContain('"type":"tts.multi_speaker_synthesize"');
      expect(createCall?.[1]?.body).toContain('"resource_class":"gpu:tts"');
      expect(createCall?.[1]?.body).toContain('"script_segments"');
      expect(createCall?.[1]?.body).toContain('"character_voice_assignments"');
      expect(createCall?.[1]?.body).toContain('"voice_id":"voice-cloning:alex"');
      expect(createCall?.[1]?.body).toContain('"voice_mapping"');
      expect(createCall?.[1]?.body).toContain('"speakerInstructions"');
      expect(createCall?.[1]?.body).toContain('"maxSpeakerTurnSeconds":45');
    });
  });
});
