import { QueryClientProvider } from '@tanstack/react-query';
import type { ComponentProps } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ChatIdentityModeControl } from './ChatIdentityModeControl';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const maya = {
  id: 'maya',
  display_name: 'Maya',
  description: 'Warm character',
  personality_prompt: 'Be Maya.',
  default_greeting: 'Hey.',
  default_voice_asset_id: 'voice-cloning:maya',
  speech_style: {},
  identity_policy: {},
  shared_memory_policy: {},
  active_version: 2,
  enabled: true,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const sofia = {
  ...maya,
  id: 'sofia',
  display_name: 'Sofia',
  personality_prompt: 'Be Sofia.',
  default_voice_asset_id: 'voice-cloning:sofia',
  active_version: 3,
};

const voices = [
  { assetId: 'voice-cloning:aurora', value: 'aurora', label: 'Aurora' },
  { assetId: 'voice-cloning:maya', value: 'maya', label: 'Maya Voice' },
  { assetId: 'voice-cloning:sofia', value: 'sofia', label: 'Sofia Voice' },
];

function renderControl(props: Partial<ComponentProps<typeof ChatIdentityModeControl>> = {}) {
  const queryClient = createTestQueryClient();
  const defaults = {
    sessionId: 'chat:one',
    systemVoiceId: 'aurora',
    defaultVoiceLabel: 'Default voice',
    voiceOptions: voices,
    onSystemVoiceChange: vi.fn(),
    onSessionResolved: vi.fn(),
    onOpenSystemSettings: vi.fn(),
    onOpenCharacterSettings: vi.fn(),
  };
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <ChatIdentityModeControl {...defaults} {...props} />
      </QueryClientProvider>,
    ),
    props: { ...defaults, ...props },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('ChatIdentityModeControl', () => {
  it('uses the shared selector as a voice picker in System mode', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya, sofia] });
      if (url.pathname.endsWith('/interaction')) return Response.json({
        id: 'chat:one',
        title: 'Chat',
        interaction_mode: 'system',
        character_id: null,
        voice_asset_id: null,
        read_memory: false,
        write_memory: false,
        shared_memory_access: 'none',
        transcript_policy: 'persistent',
        messages: [],
      });
      return new Response('not found', { status: 404 });
    }));

    const { props } = renderControl();
    const voiceSelect = await screen.findByRole('combobox', { name: 'System voice' });
    expect(voiceSelect).toHaveValue('aurora');
    expect(screen.getByRole('button', { name: 'System' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Personality' })).toBeInTheDocument();

    fireEvent.change(voiceSelect, { target: { value: 'maya' } });
    expect(props.onSystemVoiceChange).toHaveBeenCalledWith('maya');
  });

  it('switches to Character mode and changing the dropdown changes the server-owned character and linked voice', async () => {
    const posted: Array<Record<string, unknown>> = [];
    let interaction = {
      id: 'chat:one',
      title: 'Chat',
      interaction_mode: 'system',
      character_id: null,
      voice_asset_id: null,
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
      messages: [],
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya, sofia] });
      if (url.pathname.endsWith('/interaction') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        posted.push(body);
        interaction = { ...interaction, ...body } as typeof interaction;
        return Response.json(interaction);
      }
      if (url.pathname.endsWith('/interaction')) return Response.json(interaction);
      return new Response('not found', { status: 404 });
    }));

    renderControl();
    const characterButton = await screen.findByRole('button', { name: 'Character' });
    await waitFor(() => expect(characterButton).not.toBeDisabled());
    fireEvent.click(characterButton);

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:maya',
      read_memory: false,
      write_memory: false,
    });

    const characterSelect = await screen.findByRole('combobox', { name: 'Character' });
    expect(characterSelect).toHaveValue('maya');
    expect(characterSelect.querySelector('option[value="maya"]')?.textContent).toBe('Maya');
    expect(characterSelect.querySelector('option[value="sofia"]')?.textContent).toBe('Sofia');
    const settingsButton = screen.getByRole('button', { name: 'Character Settings' });
    expect(settingsButton).toHaveAttribute('title', 'Character Settings');
    expect(screen.getByText('Character Settings')).toBeInTheDocument();

    fireEvent.change(characterSelect, { target: { value: 'sofia' } });
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1]).toMatchObject({
      interaction_mode: 'character',
      character_id: 'sofia',
      voice_asset_id: 'voice-cloning:sofia',
    });
  });

  it('switches back to System mode without overwriting the independent system voice', async () => {
    const posted: Array<Record<string, unknown>> = [];
    let interaction = {
      id: 'chat:one',
      title: 'Chat',
      interaction_mode: 'character',
      character_id: 'sofia',
      voice_asset_id: 'voice-cloning:sofia',
      read_memory: true,
      write_memory: true,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
      messages: [],
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya, sofia] });
      if (url.pathname.endsWith('/interaction') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        posted.push(body);
        interaction = { ...interaction, ...body } as typeof interaction;
        return Response.json(interaction);
      }
      if (url.pathname.endsWith('/interaction')) return Response.json(interaction);
      return new Response('not found', { status: 404 });
    }));

    renderControl({ systemVoiceId: 'aurora' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Character' })).toHaveAttribute('aria-pressed', 'true'));
    fireEvent.click(screen.getByRole('button', { name: 'System' }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      interaction_mode: 'system',
      character_id: null,
      voice_asset_id: null,
      read_memory: false,
      write_memory: false,
    });

    const voiceSelect = await screen.findByRole('combobox', { name: 'System voice' });
    expect(voiceSelect).toHaveValue('aurora');
  });
});
