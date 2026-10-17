import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CharacterModePanel } from './CharacterModePanel';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel(onSessionResolved?: (sessionId: string) => void) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}><CharacterModePanel sessionId="chat:one" onSessionResolved={onSessionResolved} /></QueryClientProvider>);
}

const maya = {
  id: 'maya', display_name: 'Maya', description: 'Easygoing character',
  personality_prompt: 'Be warm and easygoing.', default_greeting: 'Hey.',
  default_voice_asset_id: 'voice-cloning:maya', speech_style: {}, identity_policy: {},
  shared_memory_policy: {}, active_version: 2, enabled: true, status: 'active',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

const jinx = {
  id: 'jinx', display_name: 'Jinx', description: 'Chaotic inventor',
  personality_prompt: 'Be chaotic, theatrical, and unmistakably Jinx.', default_greeting: 'Make it interesting.',
  default_voice_asset_id: 'voice-cloning:Jinx', speech_style: {}, identity_policy: {},
  shared_memory_policy: {}, active_version: 5, enabled: true, status: 'active',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-05T00:00:00Z',
};

afterEach(() => vi.unstubAllGlobals());

describe('CharacterModePanel', () => {
  it('enables a server-owned character and keeps memory off', async () => {
    const posted: unknown[] = [];
    let interaction = {
      id: 'chat:one', title: 'Chat', interaction_mode: 'system', character_id: null, voice_asset_id: null,
      read_memory: false, write_memory: false, shared_memory_access: 'none', transcript_policy: 'persistent', messages: [],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya] });
      if (url.pathname.endsWith('/interaction') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        posted.push(body);
        interaction = { ...interaction, interaction_mode: body.interaction_mode, character_id: body.character_id, voice_asset_id: body.voice_asset_id, read_memory: body.read_memory, write_memory: body.write_memory };
        return Response.json(interaction);
      }
      if (url.pathname.endsWith('/interaction')) return Response.json(interaction);
      return new Response('not found', { status: 404 });
    }));

    renderPanel();
    expect(await screen.findByRole('option', { name: 'Maya' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Enable Character Mode' }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      interaction_mode: 'character', character_id: 'maya', voice_asset_id: 'voice-cloning:maya',
      read_memory: false, write_memory: false, transcript_policy: 'persistent',
    });
    expect(await screen.findByRole('status')).toHaveTextContent('Character Mode enabled. Maya loaded with linked voice. Memory off.');
  });

  it('does not reset a manual character choice to the currently active character', async () => {
    const posted: Array<Record<string, unknown>> = [];
    let interaction = {
      id: 'chat:one', title: 'Chat', interaction_mode: 'character', character_id: 'maya',
      voice_asset_id: 'voice-cloning:maya', read_memory: false, write_memory: false,
      shared_memory_access: 'none', transcript_policy: 'persistent', character_profile_version: 2, messages: [],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya, jinx] });
      if (url.pathname.endsWith('/interaction') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        posted.push(body);
        interaction = {
          ...interaction,
          interaction_mode: String(body.interaction_mode),
          character_id: String(body.character_id),
          voice_asset_id: String(body.voice_asset_id),
        };
        return Response.json(interaction);
      }
      if (url.pathname.endsWith('/interaction')) return Response.json(interaction);
      return new Response('not found', { status: 404 });
    }));

    renderPanel();
    const selector = await screen.findByRole('combobox', { name: 'Character' });
    await waitFor(() => expect(selector).toHaveValue('maya'));

    fireEvent.change(selector, { target: { value: 'jinx' } });
    expect(selector).toHaveValue('jinx');
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(selector).toHaveValue('jinx');

    fireEvent.click(screen.getByRole('button', { name: 'Apply Character Settings' }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      interaction_mode: 'character',
      character_id: 'jinx',
      voice_asset_id: 'voice-cloning:Jinx',
    });
  });

  it('creates a persisted session when the selected chat is missing and confirms the loaded character', async () => {
    const resolved = vi.fn();
    const postedSessionIds: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya] });
      if (url.pathname === '/api/chat/sessions' && init?.method === 'POST') {
        return Response.json({ id: 'chat:created', title: 'Live Chat with Maya', messages: [], message_count: 0 });
      }
      if (url.pathname.endsWith('/interaction') && init?.method === 'POST') {
        const sessionId = decodeURIComponent(url.pathname.split('/')[4] ?? '');
        postedSessionIds.push(sessionId);
        if (sessionId === 'chat:one') return Response.json({ detail: 'chat session not found' }, { status: 404 });
        const body = JSON.parse(String(init.body));
        return Response.json({
          id: sessionId, title: 'Live Chat with Maya', interaction_mode: body.interaction_mode,
          character_id: body.character_id, voice_asset_id: body.voice_asset_id,
          read_memory: body.read_memory, write_memory: body.write_memory,
          shared_memory_access: 'none', transcript_policy: 'persistent', messages: [],
        });
      }
      if (url.pathname.endsWith('/interaction')) return Response.json({ detail: 'chat session not found' }, { status: 404 });
      return new Response('not found', { status: 404 });
    }));

    renderPanel(resolved);
    expect(await screen.findByRole('option', { name: 'Maya' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Enable Character Mode' }));

    await waitFor(() => expect(resolved).toHaveBeenCalledWith('chat:created'));
    expect(postedSessionIds).toEqual(['chat:one', 'chat:created']);
    expect(await screen.findByRole('status')).toHaveTextContent('Character Mode enabled. Maya loaded with linked voice. Memory off.');
  });

  it('shows the persisted character identity badge and memory policy', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/characters') return Response.json({ characters: [maya] });
      if (url.pathname.endsWith('/interaction')) return Response.json({
        id: 'chat:one', title: 'Chat', interaction_mode: 'character', character_id: 'maya',
        voice_asset_id: 'voice-cloning:maya', read_memory: false, write_memory: false,
        shared_memory_access: 'none', transcript_policy: 'persistent', character_profile_version: 2, messages: [],
      });
      return new Response('not found', { status: 404 });
    }));

    renderPanel();
    expect(await screen.findByText('Talking to Maya')).toBeInTheDocument();
    expect(screen.getByText('voice-cloning:maya')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
  });
});
