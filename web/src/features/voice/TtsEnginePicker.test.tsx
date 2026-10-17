import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { createTestQueryClient } from '../../test/renderWithProviders';
import type { TtsEnginesResponse } from './api/voiceClient';
import { TtsEnginePicker } from './TtsEnginePicker';

function engines(selected: string, phase: TtsEnginesResponse['switch']['phase'], overrides: Partial<TtsEnginesResponse['switch']> = {}): TtsEnginesResponse {
  return {
    selected,
    engines: [
      { id: 'faster-qwen3-tts', label: 'Faster Qwen3 TTS', description: '', selected: selected === 'faster-qwen3-tts', installed: true, unavailable_reason: '', needs_transcripts: false, voice_controls: ['stability', 'similarity', 'style', 'speed', 'pitch', 'volume'] },
      { id: 'fish-s2-pro', label: 'Fish Audio S2 Pro', description: '', selected: selected === 'fish-s2-pro', installed: true, unavailable_reason: '', needs_transcripts: true, voice_controls: ['stability', 'style', 'speed', 'pitch', 'volume'] },
    ],
    switch: { provider: phase === 'idle' ? null : selected, phase, error: null, voices: { prepared: [], failed: [], already_ready: 0 }, ...overrides },
  };
}

function renderPicker() {
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark" env="test">
      <QueryClientProvider client={createTestQueryClient()}>
        <TtsEnginePicker />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

afterAll(async () => {
  await new Promise((resolve) => setTimeout(resolve, 400));
});

describe('TtsEnginePicker', () => {
  it('selects Fish Audio S2 Pro and reports the switch until the engine is loaded', async () => {
    const posted: unknown[] = [];
    let listed = engines('faster-qwen3-tts', 'idle');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), 'http://localhost'), init);
      const path = new URL(request.url).pathname;
      if (path === '/api/tts/engines/select' && request.method === 'POST') {
        posted.push(await request.json());
        listed = engines('fish-s2-pro', 'ready', { voices: { prepared: ['voice-cloning:Jinx'], failed: [{ voice_id: 'voice-cloning:default_ref', error: 'no speech' }], already_ready: 5 } });
        return Response.json(engines('fish-s2-pro', 'preparing_voices'));
      }
      if (path === '/api/tts/engines') return Response.json(listed);
      return Response.json({}, { status: 404 });
    }));

    renderPicker();
    const select = await screen.findByLabelText('TTS engine');
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('faster-qwen3-tts'));

    fireEvent.change(select, { target: { value: 'fish-s2-pro' } });

    await waitFor(() => expect(posted).toEqual([{ provider_id: 'fish-s2-pro' }]));
    expect(await screen.findByText('Transcribing voice samples that have no transcript…')).toBeTruthy();
    expect(select).toHaveProperty('disabled', true);
    expect(await screen.findByText(/Fish Audio S2 Pro is loaded\. 1 voice transcribed for cloning; 1 could not be: default_ref/, {}, { timeout: 4000 })).toBeTruthy();
    expect(select).toHaveProperty('disabled', false);
  });

  it('shows why an engine failed to load', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(engines('fish-s2-pro', 'failed', { error: 'the Fish API server exited during startup' }))));

    renderPicker();

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Fish Audio S2 Pro did not load: the Fish API server exited during startup');
  });
});
