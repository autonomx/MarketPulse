import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules, type OmnixModuleDefinition } from '../app/modules';
import { omnixTheme } from '../design/theme';
import { ImageGenerationWorkspace } from '../features/image-generation/ImageGenerationWorkspace';
import { PodcastWorkspace } from '../features/podcast/PodcastWorkspace';
import { RpgWorkspace } from '../features/rpg/RpgWorkspace';
import { SttWorkspace } from '../features/stt/SttWorkspace';
import { StorytellerWorkspace } from '../features/storyteller/StorytellerWorkspace';
import { VoiceCloningWorkspace } from '../features/voice-cloning/VoiceCloningWorkspace';
import { VoiceWorkspace } from '../features/voice/VoiceWorkspace';
import { createTestQueryClient } from './renderWithProviders';

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

function moduleById(moduleId: OmnixModuleDefinition['id']): OmnixModuleDefinition {
  const module = omnixModules.find((entry) => entry.id === moduleId);

  if (!module) {
    throw new Error(`Missing module ${moduleId}`);
  }

  return module;
}

function renderWithProviders(children: ReactNode) {
  const queryClient = createTestQueryClient();

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MantineProvider>,
  );
}

function installFailingJobApiMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);

    if (path === '/api/providers') {
      return Response.json({
        providers: [
          {
            id: 'lmstudio',
            label: 'LM Studio',
            family: 'llm',
            source: 'settings',
            status: 'configured',
            capabilities: ['chat', 'completion'],
          },
          {
            id: 'qwen-tts',
            label: 'Qwen TTS',
            family: 'tts',
            source: 'settings',
            status: 'configured',
            capabilities: ['tts', 'voice_cloning'],
          },
          {
            id: 'parakeet',
            label: 'Parakeet STT',
            family: 'stt',
            source: 'settings',
            status: 'configured',
            capabilities: ['stt'],
          },
          {
            id: 'flux',
            label: 'FLUX',
            family: 'image',
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
        local_model: {
          ok: true,
          exists: true,
          complete: true,
          missing: [],
          local_dir: 'resources/models/image/flux2-klein-4b',
        },
      });
    }

    if (path === '/api/jobs' && init?.method === 'POST') {
      return new Response('gateway unavailable', { status: 500 });
    }

    if (path === '/api/jobs') {
      return Response.json({ jobs: [] });
    }

    if (path === '/api/assets') {
      return Response.json({ assets: [] });
    }

    if (path === '/api/reports') {
      return Response.json({ reports: [] });
    }

    if (path === '/api/replay/persistence/inventory') {
      return Response.json({ sessions: [] });
    }

    return new Response('not found', { status: 404 });
  });

  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('provider-backed feature submit feedback', () => {
  it('surfaces Storyteller job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<StorytellerWorkspace module={moduleById('storyteller')} />);

    expect(await screen.findByText('Story controls')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Premise/), { target: { value: 'A memory orchard.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue story' }));

    expect(await screen.findByText(/Story request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces Podcast job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<PodcastWorkspace module={moduleById('podcast')} />);

    expect(await screen.findByRole('heading', { name: '1. Episode setup' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Episode brief/), { target: { value: 'Discuss local model routing.' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate live podcast/i }));

    expect(await screen.findByText(/Podcast request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces TTS job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<VoiceWorkspace module={moduleById('voice')} />);

    expect(await screen.findByRole('heading', { name: 'Text-to-Speech (Multi-Voice)' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Script'), { target: { value: 'Narrator: Read this aloud.' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate Speech/ }));

    expect(await screen.findByText(/TTS request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces STT job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<SttWorkspace module={moduleById('stt')} />);

    expect(await screen.findByRole('heading', { name: 'Transcription' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Source path'), { target: { value: 'resources/data/input.wav' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue transcription' }));

    expect(await screen.findByText(/STT request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces voice-cloning job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<VoiceCloningWorkspace module={moduleById('voice-cloning')} />);

    expect(await screen.findByRole('heading', { name: 'Voice profile' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Narrator profile' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue voice profile' }));

    expect(await screen.findByText(/Voice profile request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces image generation job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<ImageGenerationWorkspace module={moduleById('image-generation')} />);

    expect(await screen.findByRole('heading', { name: 'Image request' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'A glowing nebula.' } });
    const generateButton = screen.getByRole('button', { name: 'Generate image' });
    await waitFor(() => expect(generateButton).toBeEnabled());
    fireEvent.click(generateButton);

    expect(await screen.findByText(/Image request failed with status 500/)).toBeInTheDocument();
  });

  it('surfaces RPG turn job failures', async () => {
    installFailingJobApiMock();
    renderWithProviders(<RpgWorkspace module={moduleById('rpg')} />);

    expect(await screen.findByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'Look around the tavern.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));

    expect(await screen.findByText(/RPG turn request failed with status 500/)).toBeInTheDocument();
  });
});
