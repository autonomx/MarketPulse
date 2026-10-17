import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../../design/theme';
import { CharacterManagementPanel } from './CharacterManagementPanel';
import type { CharacterProfile, SessionInteraction } from './characterClient';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const maya: CharacterProfile = {
  id: 'maya',
  display_name: 'Maya',
  description: 'Warm assistant.',
  personality_prompt: 'Be warm.',
  default_greeting: 'Hello.',
  default_voice_asset_id: 'voice-cloning:maya',
  speech_style: {},
  identity_policy: {},
  shared_memory_policy: {},
  active_version: 2,
  enabled: true,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
};

const jinx: CharacterProfile = {
  id: 'jinx',
  display_name: 'Jinx',
  description: 'Chaotic inventor.',
  personality_prompt: 'Be chaotic, theatrical, and unmistakably Jinx.',
  default_greeting: 'Make it interesting.',
  default_voice_asset_id: 'voice-cloning:Jinx',
  speech_style: {},
  identity_policy: {},
  shared_memory_policy: {},
  active_version: 5,
  enabled: true,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-05T00:00:00Z',
};

function pathOf(input: RequestInfo | URL): string {
  return new URL(input.toString(), 'http://localhost').pathname;
}

function characterData(character: CharacterProfile) {
  return {
    character,
    versions: [{
      character_id: character.id,
      version: character.active_version,
      personality_prompt: character.personality_prompt,
      created_at: character.updated_at,
    }],
    memories: [],
    pending_suggestions: [],
    sessions: [],
    generated_at: character.updated_at,
  };
}

function voiceGovernance(assetId: string, displayName: string) {
  return {
    asset_id: assetId,
    subject_owner: displayName,
    source_type: 'user_recording',
    source_reference: '',
    creator_id: 'local-user',
    consent_status: 'granted',
    allowed_uses: ['character', 'live_call'],
    deletion_state: 'active',
    deletion_reason: '',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('CharacterManagementPanel selection', () => {
  it('preserves a manual Jinx selection through a character-list refetch and activates Jinx', async () => {
    let characterListRevision = 0;
    const interactionBodies: Array<Record<string, unknown>> = [];
    let interaction: SessionInteraction = {
      id: 'chat:voice',
      title: 'Live voice call',
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:maya',
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
      character_profile_version: 2,
      effective_identity_hash: null,
      messages: [],
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path === '/api/characters') {
        characterListRevision += 1;
        return Response.json({
          characters: [
            { ...maya, updated_at: `2026-01-02T00:00:0${characterListRevision}Z` },
            { ...jinx, updated_at: `2026-01-05T00:00:0${characterListRevision}Z` },
          ],
        });
      }
      if (path === '/api/characters/maya/data') return Response.json(characterData(maya));
      if (path === '/api/characters/jinx/data') return Response.json(characterData(jinx));
      if (path.endsWith('/avatar-pack')) return new Response('not found', { status: 404 });
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/voice-profiles/voice-cloning%3Amaya/governance') {
        return Response.json(voiceGovernance('voice-cloning:maya', 'Maya'));
      }
      if (path === '/api/voice-profiles/voice-cloning%3AJinx/governance') {
        return Response.json(voiceGovernance('voice-cloning:Jinx', 'Jinx'));
      }
      if (path === '/api/chat/sessions/chat%3Avoice/interaction') {
        if (init?.method === 'POST') {
          const body = JSON.parse(String(init.body)) as Record<string, unknown>;
          interactionBodies.push(body);
          interaction = {
            ...interaction,
            interaction_mode: 'character',
            character_id: String(body.character_id),
            voice_asset_id: String(body.voice_asset_id),
            character_profile_version: body.character_id === 'jinx' ? 5 : 2,
          };
        }
        return Response.json(interaction);
      }
      return new Response('not found', { status: 404 });
    }));

    const queryClient = createTestQueryClient();
    render(
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <QueryClientProvider client={queryClient}>
          <CharacterManagementPanel sessionId="chat:voice" />
        </QueryClientProvider>
      </MantineProvider>,
    );

    const selector = await screen.findByRole('combobox', { name: 'Select character' });
    await waitFor(() => expect(selector).toHaveValue('maya'));

    fireEvent.change(selector, { target: { value: 'jinx' } });
    expect(selector).toHaveValue('jinx');

    await queryClient.invalidateQueries({
      queryKey: ['feature', 'chatbot', 'characters', 'management'],
    });
    await waitFor(() => expect(characterListRevision).toBeGreaterThan(1));
    expect(selector).toHaveValue('jinx');

    fireEvent.click(await screen.findByRole('button', { name: 'Use Jinx' }));

    await waitFor(() => expect(interactionBodies).toHaveLength(1));
    expect(interactionBodies[0]).toMatchObject({
      interaction_mode: 'character',
      character_id: 'jinx',
      voice_asset_id: 'voice-cloning:Jinx',
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
    });
    expect(await screen.findByRole('button', { name: 'Active in Live Voice' })).toBeDisabled();
  });
});
