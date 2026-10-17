import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { PlatformModuleWorkspace } from './PlatformModuleWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderSettings() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'settings');

  if (!module) {
    throw new Error('Settings module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <PlatformModuleWorkspace module={module} />
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

describe('provider settings controls', () => {
  it('exposes legacy provider choices and saves selected defaults', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/settings' && init?.method === 'POST') {
        return Response.json({ success: true });
      }

      if (path === '/api/settings') {
        return Response.json({
          success: true,
          provider: 'lmstudio',
          audio_provider_tts: 'faster-qwen3-tts',
          audio_provider_stt: 'parakeet',
          image_enabled: true,
          rpg_visual_enabled: false,
          worker_urls: { tts: 'http://127.0.0.1:5101' },
          revisions: { provider: 2, audio_provider_tts: 1, audio_provider_stt: 0 },
          settings: {
            lmstudio: { base_url: 'http://localhost:1234', direct: false },
            openrouter: { model: 'openai/gpt-4o-mini', api_key: '***1234' },
            cerebras: { model: 'llama-3.3-70b-versatile' },
            llamacpp: { base_url: 'http://localhost:8080' },
          },
        });
      }

      if (path === '/api/providers') {
        return Response.json({
          providers: [
            { id: 'lmstudio', label: 'LM Studio', family: 'llm', source: 'settings', status: 'configured', capabilities: ['chat'] },
            { id: 'openrouter', label: 'OpenRouter', family: 'llm', source: 'settings', status: 'configured', capabilities: ['chat'] },
          ],
          models: [],
        });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderSettings();

    expect(await screen.findByLabelText('LLM provider')).toHaveValue('lmstudio');
    expect(screen.getByRole('option', { name: 'OpenRouter' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Cerebras' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'llama.cpp' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('LLM provider'), { target: { value: 'openrouter' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save provider defaults' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/settings',
        expect.objectContaining({
          body: JSON.stringify({
            values: { provider: 'openrouter', audio_provider_tts: 'faster-qwen3-tts', audio_provider_stt: 'parakeet' },
            revisions: { provider: 2, audio_provider_tts: 1, audio_provider_stt: 0 },
          }),
          method: 'POST',
        }),
      );
    });
    expect(await screen.findByText('Provider settings saved.')).toBeInTheDocument();
  });
});
