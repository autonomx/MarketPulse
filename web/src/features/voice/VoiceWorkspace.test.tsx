import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { VoiceWorkspace } from './VoiceWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderVoice() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'voice');

  if (!module) {
    throw new Error('Voice module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark" env="test">
      <QueryClientProvider client={queryClient}>
        <VoiceWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// Mantine transitions schedule state updates on timers. Let the last ones
// run while jsdom still exists; otherwise they can fire after teardown
// ("window is not defined") and fail an otherwise green run.
afterAll(async () => {
  await new Promise((resolve) => setTimeout(resolve, 400));
});

describe('VoiceWorkspace', () => {
  it('plays the completed speech when the job list still has a queued summary', async () => {
    let detailRequests = 0;
    const queuedJob = {
      id: 'job:new', module: 'voice', type: 'tts.synthesize', status: 'queued',
      resource_class: 'gpu:tts', created_at: '2026-06-14T00:00:01Z',
      updated_at: '2026-06-14T00:00:01Z', priority: 0,
      progress: { current: 0, total: 1 },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/settings/profile') return Response.json({ success: true, settings: {} });
      if (path === '/api/providers') return Response.json({ providers: [], models: [] });
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/jobs' && init?.method === 'POST') return Response.json(queuedJob);
      if (path === '/api/jobs' || path === '/api/jobs/voice-summaries') {
        return Response.json({ jobs: [
          queuedJob,
          { ...queuedJob, id: 'job:old', status: 'completed', output_refs: [{ title: 'Old speech', data_url: 'data:audio/wav;base64,b2xk' }] },
        ] });
      }
      if (path === '/api/jobs/job%3Anew') {
        detailRequests += 1;
        return Response.json(detailRequests === 1 ? queuedJob : {
          // Current contract: audio is served from the stored asset.
          ...queuedJob, status: 'completed', output_refs: [{ title: 'New speech', asset_id: 'audio:new', audio_url: '/api/assets/audio%3Anew/audio' }],
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});

    renderVoice();
    expect(await screen.findByText('Old speech · 3 speakers')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Script'), { target: { value: 'Dave: New line.' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate Speech/ }));

    expect(await screen.findByText('Generating new speech… · 1 speaker')).toBeInTheDocument();
    expect(await screen.findByText('New speech · 1 speaker', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(document.querySelector('audio')).toHaveAttribute('src', '/api/assets/audio%3Anew/audio');
  });

  it('loads central defaults, resets local edits, and queues TTS through the shared jobs API', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/settings/profile') {
        return Response.json({
          success: true,
          provider: 'lmstudio',
          audio_provider_tts: 'faster-qwen3-tts',
          audio_provider_stt: 'parakeet',
          settings: {
            settings_control_center: {
              revision: 'voice-studio-r1',
              global: { providers: { tts: 'faster-qwen3-tts', voiceCloning: 'qwen-voice' } },
              voice: {
                language: 'English',
                stability: 0.61,
                similarity: 0.72,
                style: 0.22,
                speed: 1.25,
                pitch: 1,
                volume: -2,
                effects: ['Compression'],
                cloningLanguage: 'French',
                cloningQuality: 'Standard',
              },
            },
          },
        });
      }

      if (path === '/api/providers') {
        return Response.json({
          providers: [
            {
              id: 'faster-qwen3-tts',
              label: 'Faster Qwen TTS',
              family: 'tts',
              source: 'settings',
              status: 'configured',
              capabilities: ['tts', 'voice_cloning'],
            },
            {
              id: 'qwen-voice',
              label: 'Qwen Voice',
              family: 'tts',
              source: 'settings',
              status: 'configured',
              capabilities: ['voice_cloning'],
            },
          ],
          models: [],
        });
      }

      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json({
          id: 'job:tts',
          module: 'voice',
          type: 'tts.synthesize',
          status: 'queued',
          resource_class: 'gpu:tts',
          created_at: '2026-06-14T00:00:01Z',
          updated_at: '2026-06-14T00:00:01Z',
          priority: 0,
          progress: { current: 0, total: 1 },
        });
      }

      if (path === '/api/voice-cloning/assets/voice-cloning%3Ajinx2' && init?.method === 'DELETE') {
        return Response.json({ ok: true, asset_id: 'voice-cloning:jinx2', deleted: true, file_deleted: true });
      }

      if (path === '/api/jobs' || path === '/api/jobs/voice-summaries') {
        return Response.json({
          jobs: [
            {
              id: 'job:existing',
              module: 'voice',
              type: 'tts.synthesize',
              status: 'running',
              resource_class: 'gpu:tts',
              created_at: '2026-06-14T00:00:00Z',
              updated_at: '2026-06-14T00:00:01Z',
              priority: 0,
              progress: { current: 1, total: 2 },
            },
          ],
        });
      }

      if (path === '/api/assets') {
        return Response.json({
          assets: [
            {
              id: 'voice-cloning:Dave',
              module: 'voice-cloning',
              type: 'voice_profile',
              mime_type: 'application/octet-stream',
              file_name: 'Dave.wav',
              created_at: '2026-06-14T00:00:00Z',
            },
            {
              id: 'voice-cloning:jinx2',
              module: 'voice-cloning',
              type: 'voice_profile',
              mime_type: 'audio/wav',
              file_name: 'jinx2.wav',
              metadata: { profile_name: 'jinx2', voice_id: 'jinx2' },
              created_at: '2026-06-14T00:00:01Z',
            },
            {
              id: 'asset:audio',
              module: 'voice',
              type: 'audio',
              mime_type: 'audio/wav',
              file_name: 'voice.wav',
              created_at: '2026-06-14T00:00:00Z',
            },
          ],
        });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderVoice();

    expect(await screen.findByRole('heading', { name: 'Clone a Voice' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Voice Library' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Text-to-Speech (Multi-Voice)' })).toBeInTheDocument();
    expect(screen.getAllByText('Dave').length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(screen.getByLabelText('Output speed')).toHaveValue('1.25');
      expect(screen.getByLabelText('Language / Accent')).toHaveValue('French');
      expect(screen.getByLabelText('Quality')).toHaveValue('Standard');
      expect(screen.getByRole('button', { name: 'Compression' })).toHaveClass('active');
      expect(screen.getByRole('button', { name: 'Equalizer' })).not.toHaveClass('active');
    });

    fireEvent.change(screen.getByLabelText('Output speed'), { target: { value: '1.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset tuning' }));
    expect(screen.getByLabelText('Output speed')).toHaveValue('1.25');

    fireEvent.click(screen.getByRole('button', { name: 'Equalizer' }));
    expect(screen.getByRole('button', { name: 'Equalizer' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'Reset effects' }));
    expect(screen.getByRole('button', { name: 'Compression' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Equalizer' })).not.toHaveClass('active');

    fireEvent.change(screen.getByLabelText('Language / Accent'), { target: { value: 'Spanish' } });
    fireEvent.change(screen.getByLabelText('Quality'), { target: { value: 'Draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset defaults' }));
    expect(screen.getByLabelText('Language / Accent')).toHaveValue('French');
    expect(screen.getByLabelText('Quality')).toHaveValue('Standard');

    fireEvent.change(screen.getByLabelText('Script'), { target: { value: 'Narrator: A short line for synthesis.' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Use' })[0]);
    fireEvent.change(screen.getByLabelText('Narrator voice'), { target: { value: 'voice-cloning:jinx2' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate Speech/ }));

    expect(await screen.findByText('TTS job queued: job:tts')).toBeInTheDocument();

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST',
      );
      expect(createCall?.[1]?.body).toContain('"module":"voice"');
      expect(createCall?.[1]?.body).toContain('"type":"tts.synthesize"');
      expect(createCall?.[1]?.body).toContain('"resource_class":"gpu:tts"');
      expect(createCall?.[1]?.body).toContain('"provider_id":"faster-qwen3-tts"');
      expect(createCall?.[1]?.body).toContain('"language":"English"');
      expect(createCall?.[1]?.body).toContain('"voice_id":"voice-cloning:jinx2"');
      expect(createCall?.[1]?.body).toContain('"speed":1.25');
      expect(createCall?.[1]?.body).toContain('"audio_effects":["Compression"]');
      expect(createCall?.[1]?.body).toContain('"character_voice_assignments"');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Delete jinx2' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => requestPath(input as RequestInfo | URL) === '/api/voice-cloning/assets/voice-cloning%3Ajinx2' && init?.method === 'DELETE')).toBe(true));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('permanently removes'));
    confirmSpy.mockRestore();
  });

  it('disables the sliders the selected TTS engine has no setting for', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/tts/engines') {
        return Response.json({
          selected: 'fish-s2-pro',
          engines: [{
            id: 'fish-s2-pro', label: 'Fish Audio S2 Pro', description: '', selected: true, installed: true,
            unavailable_reason: '', needs_transcripts: true, voice_controls: ['stability', 'style', 'speed', 'pitch', 'volume'],
          }],
          switch: { provider: null, phase: 'idle', error: null, voices: { prepared: [], failed: [], already_ready: 0 } },
        });
      }
      if (path === '/api/providers') return Response.json({ providers: [], models: [] });
      if (path === '/api/jobs' || path === '/api/jobs/voice-summaries') return Response.json({ jobs: [] });
      if (path === '/api/assets') return Response.json({ assets: [] });
      return new Response('not found', { status: 404 });
    }));

    renderVoice();

    await waitFor(() => expect(screen.getByLabelText('Output similarity')).toBeDisabled());
    expect(screen.getByLabelText('Output similarity').closest('label')).toHaveTextContent('Not used');
    expect(screen.getByLabelText('Output stability')).toBeEnabled();
    expect(screen.getByLabelText('Output speed')).toHaveAttribute('min', '0.5');
    expect(screen.getByLabelText('Output pitch')).toHaveAttribute('step', '1');
  });

  it('transcribes a clone sample for review before creating the voice', async () => {
    let cloneRequest: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/settings/profile') {
        return Response.json({
          success: true,
          provider: 'lmstudio',
          audio_provider_tts: 'faster-qwen3-tts',
          audio_provider_stt: 'parakeet',
          settings: {
            settings_control_center: {
              revision: 'voice-transcript-r1',
              global: { providers: { tts: 'faster-qwen3-tts', stt: 'parakeet', voiceCloning: 'faster-qwen3-tts' } },
              voice: { cloningLanguage: 'English', cloningQuality: 'High' },
            },
          },
        });
      }
      if (path === '/api/providers') {
        return Response.json({ providers: [], models: [] });
      }
      if (path === '/api/jobs' && init?.method === 'POST') {
        const request = JSON.parse(String(init.body)) as Record<string, unknown>;
        if (request.type === 'voice-cloning.transcribe-sample') {
          return Response.json({
            id: 'job:transcribe-sample',
            module: 'voice-cloning',
            type: 'voice-cloning.transcribe-sample',
            status: 'completed',
            resource_class: 'gpu:stt',
            created_at: '2026-07-20T00:00:00Z',
            updated_at: '2026-07-20T00:00:01Z',
            priority: 0,
            progress: { current: 1, total: 1 },
            output_refs: [{ type: 'transcript', content: 'Sofia speaks these exact words.' }],
          });
        }
        cloneRequest = request;
        return Response.json({
          id: 'job:clone-transcript',
          module: 'voice-cloning',
          type: 'voice-cloning.create-profile',
          status: 'completed',
          resource_class: 'gpu:tts',
          created_at: '2026-07-20T00:00:00Z',
          updated_at: '2026-07-20T00:00:01Z',
          priority: 0,
          progress: { current: 5, total: 5 },
        });
      }
      if (path === '/api/jobs' || path === '/api/jobs/voice-summaries') return Response.json({ jobs: [] });
      if (path === '/api/assets') return Response.json({ assets: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderVoice();

    expect(await screen.findByRole('checkbox', { name: /Generate transcript with STT/ })).toBeChecked();
    expect(screen.getByLabelText('Reference transcript (optional)')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Voice Name'), { target: { value: 'Maya Transcript' } });
    fireEvent.change(screen.getByLabelText(/Audio sample/), {
      target: { files: [new File(['voice sample'], 'maya.wav', { type: 'audio/wav' })] },
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate Transcript' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Generate Transcript' }));
    await waitFor(() => expect(screen.getByLabelText('Reference transcript (optional)')).toHaveValue('Sofia speaks these exact words.'));
    expect(screen.getByRole('checkbox', { name: /Generate transcript with STT/ })).not.toBeChecked();

    await waitFor(() => expect(screen.getByRole('button', { name: 'Create Clone' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Create Clone' }));

    await waitFor(() => expect(cloneRequest).toBeDefined());
    const payload = cloneRequest?.input_payload as Record<string, unknown>;
    const stages = cloneRequest?.stages as Array<Record<string, unknown>>;
    expect(payload.reference_text).toBe('Sofia speaks these exact words.');
    expect(payload.generate_transcript).toBe(false);
    expect(payload.stt_provider_id).toBe('parakeet');
    expect(stages.some((stage) => stage.id === 'transcribe-sample')).toBe(false);
  });
});
