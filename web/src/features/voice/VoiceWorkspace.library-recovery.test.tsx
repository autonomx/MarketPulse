import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { VoiceWorkspace } from './VoiceWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderVoice() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'voice');
  if (!module) throw new Error('Voice module is missing');
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
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
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe('VoiceWorkspace library recovery', () => {
  it('shows an asset-index failure and restores voices after retry', async () => {
    let assetRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/settings/profile') return Response.json({
        success: true,
        provider: 'lmstudio',
        audio_provider_tts: 'faster-qwen3-tts',
        audio_provider_stt: 'parakeet',
        settings: {
settings_control_center: {
  revision: 'voice-library-recovery-r1',
  global: { providers: { tts: 'faster-qwen3-tts', voiceCloning: 'faster-qwen3-tts' } },
  voice: {},
},
        },
      });
      if (path === '/api/providers') return Response.json({ providers: [], models: [] });
      if (path === '/api/jobs') return Response.json({ jobs: [] });
      if (path === '/api/assets') {
        assetRequests += 1;
        if (assetRequests === 1) return new Response('asset index unavailable', { status: 500 });
        return Response.json({
assets: [{
  id: 'voice-cloning:Maya-Recovery',
  module: 'voice-cloning',
  type: 'voice_profile',
  mime_type: 'audio/webm',
  file_name: 'maya.webm',
  metadata: { profile_name: 'Maya Recovery', voice_id: 'maya' },
  created_at: '2026-07-30T22:00:00Z',
}],
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderVoice();

    expect(await screen.findByRole('alert')).toHaveTextContent('Voice Library failed to load');
    expect(screen.getByRole('button', { name: 'Refresh voice library' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry voice library' }));

    expect(await screen.findByText('Maya Recovery')).toBeInTheDocument();
    await waitFor(() => expect(assetRequests).toBe(2));
  });
});
