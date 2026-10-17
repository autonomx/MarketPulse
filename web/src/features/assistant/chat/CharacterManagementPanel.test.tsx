import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../../design/theme';
import { CharacterManagementPanel } from './CharacterManagementPanel';
import type { SessionInteraction } from './characterClient';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel(props: { sessionId?: string | null; onSessionResolved?: (sessionId: string) => void } = {}) {
  const queryClient = createTestQueryClient();
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <CharacterManagementPanel {...props} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

const legacyCharacter = {
  id: 'maya',
  display_name: 'Maya',
  description: 'Easygoing',
  personality_prompt: 'Be warm and easygoing.',
  default_greeting: 'Hey.',
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

const legacyData = {
  character: legacyCharacter,
  versions: [{ character_id: 'maya', version: 2, personality_prompt: 'Be warm.', created_at: '2026-01-02T00:00:00Z' }],
  memories: [{ id: 'memory:one', category: 'relationship', scope: 'global', content: 'Rainy hike joke.', pinned: false, revision: 1 }],
  pending_suggestions: [{ id: 'candidate:one', proposed_category: 'fact', proposed_content: 'User likes tea.', confidence: 0.9, created_at: '2026-01-02T00:00:00Z' }],
  sessions: [{ id: 'chat:one', title: 'Maya call', message_count: 4, character_message_count: 3, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' }],
  generated_at: '2026-01-02T00:00:00Z',
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('CharacterManagementPanel dashboard', () => {
  it('renders the production character dashboard sections and precise viseme strip', async () => {
    const character = {
      id: 'character:maya',
      display_name: 'Maya',
      description: 'Calm conversational companion.',
      personality_prompt: 'Be warm, curious, and supportive.',
      default_greeting: 'Hi, I am Maya.',
      default_voice_asset_id: 'voice-profile-maya',
      speech_style: {},
      identity_policy: {},
      shared_memory_policy: {},
      active_version: 3,
      enabled: true,
      status: 'active',
      created_at: '2026-07-10T00:00:00Z',
      updated_at: '2026-07-10T00:00:00Z',
    };
    const pack = {
      character_id: character.id,
      version: 2,
      render_mode: 'viseme',
      renderer: 'sprite',
      rig_asset_id: null,
      base_asset_id: 'image-maya-base',
      mouth_frames: {
        closed: 'image-maya-closed',
        A: 'image-maya-a',
        E: 'image-maya-e',
        O: 'image-maya-o',
        U: 'image-maya-u',
        MBP: 'image-maya-mbp',
        FV: 'image-maya-fv',
        L: 'image-maya-l',
        WQ: 'image-maya-wq',
        other: 'image-maya-other',
      },
      blink_frames: {},
      expression_frames: {},
      outfit_frames: {},
      background_asset_ids: {},
      active_outfit: null,
      active_background: null,
      mouth_anchor: {},
      created_at: '2026-07-10T00:00:00Z',
      updated_at: '2026-07-10T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/characters') return Response.json({ characters: [character] });
      if (path === '/api/characters/character%3Amaya/data') return Response.json({
        character,
        versions: [{ character_id: character.id, version: 3, personality_prompt: character.personality_prompt, created_at: character.created_at }],
        memories: [{ id: 'memory-1', category: 'relationship', scope: 'character', content: 'Prefers quiet conversations.', pinned: false, revision: 1 }],
        pending_suggestions: [{ id: 'suggestion-1', proposed_category: 'preference', proposed_content: 'Likes stargazing.', confidence: 0.8, created_at: character.created_at }],
        sessions: [{ id: 'session-1', title: 'Hello Maya', message_count: 4, character_message_count: 2, created_at: character.created_at, updated_at: character.updated_at }],
        generated_at: character.updated_at,
      });
      if (path === '/api/characters/character%3Amaya/avatar-pack/optional') return Response.json(pack);
      if (path === '/api/voice-profiles/voice-profile-maya/governance') return Response.json({
        asset_id: 'voice-profile-maya',
        subject_owner: 'Maya',
        source_type: 'user_recording',
        source_reference: 'local',
        creator_id: 'local-user',
        consent_status: 'granted',
        consent_recorded_at: '2026-07-10T00:00:00Z',
        allowed_uses: ['character', 'live_call'],
        source_sha256: 'a'.repeat(64),
        deletion_state: 'active',
        deletion_reason: '',
        updated_at: '2026-07-10T00:00:00Z',
      });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();

    expect(await screen.findByRole('heading', { name: 'Character profile' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Voice governance' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Live avatar' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Cloned-voice backfill' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Character data / relationship data' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Danger zone / cleanup' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Select character' })).toHaveValue('character:maya');
    expect(await screen.findByAltText('Maya avatar preview')).toBeInTheDocument();
    expect(screen.getByText('Use your own image')).toBeInTheDocument();
    expect(screen.getByLabelText('Upload source image')).toBeInTheDocument();
    expect(screen.getByText('Viseme support (9 mouth shapes)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create characters from cloned voices' })).toBeInTheDocument();
  });

  it('activates the selected character for the current chat and Live Voice runtime', async () => {
    const interactionBodies: Array<Record<string, unknown>> = [];
    let interaction: SessionInteraction = {
      id: 'chat:voice', title: 'Live voice call', interaction_mode: 'system', character_id: null,
      voice_asset_id: null, read_memory: false, write_memory: false, shared_memory_access: 'none',
      transcript_policy: 'persistent', character_profile_version: null, effective_identity_hash: null, messages: [],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/characters') return Response.json({ characters: [legacyCharacter] });
      if (path === '/api/characters/maya/data') return Response.json(legacyData);
      if (path === '/api/characters/maya/avatar-pack') return new Response('not found', { status: 404 });
      if (path === '/api/assets') return Response.json({ assets: [{
        id: 'voice-cloning:maya', module: 'voice-cloning', type: 'voice_profile',
        metadata: { voice_governance: { consent_status: 'granted', deletion_state: 'active', allowed_uses: ['character', 'live_call'] } },
      }] });
      if (path === '/api/voice-profiles/voice-cloning%3Amaya/governance') return Response.json({
        asset_id: 'voice-cloning:maya', subject_owner: 'Maya', source_type: 'user_recording', source_reference: '',
        creator_id: 'local-user', consent_status: 'granted', allowed_uses: ['character', 'live_call'],
        deletion_state: 'active', deletion_reason: '', updated_at: '2026-01-01T00:00:00Z',
      });
      if (path === '/api/chat/sessions/chat%3Avoice/interaction') {
        if (init?.method === 'POST') {
          interactionBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
          interaction = {
            ...interaction,
            interaction_mode: 'character',
            character_id: 'maya',
            voice_asset_id: 'voice-cloning:maya',
            character_profile_version: 2,
          };
        }
        return Response.json(interaction);
      }
      return new Response('not found', { status: 404 });
    }));

    renderPanel({ sessionId: 'chat:voice' });
    fireEvent.click(await screen.findByRole('button', { name: 'Use Maya' }));

    await waitFor(() => expect(interactionBodies).toEqual([{
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:maya',
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
    }]));
    expect(await screen.findByRole('button', { name: 'Active in Live Voice' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Maya is now active in this chat and Live Voice with its linked voice');
  });

  it('uploads an owned image and queues the full avatar pipeline', async () => {
    const generationBodies: Array<Record<string, unknown>> = [];
    const batch = {
      id: 'avatar-generation:upload',
      character_id: 'maya',
      status: 'generating_base',
      request: {},
      base_job_id: 'job:base',
      variant_job_ids: {},
      asset_ids: {},
      avatar_pack_version: null,
      error: '',
      created_at: '2026-07-10T00:00:00Z',
      updated_at: '2026-07-10T00:00:00Z',
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/characters') return Response.json({ characters: [legacyCharacter] });
      if (path === '/api/characters/maya/data') return Response.json(legacyData);
      if (path === '/api/characters/maya/avatar-pack') return new Response('not found', { status: 404 });
      if (path === '/api/voice-profiles/voice-cloning%3Amaya/governance') return new Response('not found', { status: 404 });
      if (path === '/api/image-generation/references') return Response.json({
        ok: true,
        asset: {
          id: 'image-reference:user-face',
          module: 'image-reference',
          type: 'image',
          mime_type: 'image/png',
          file_name: 'user-face.png',
          metadata: { reference_upload: true },
        },
      });
      if (path === '/api/characters/maya/avatar-generations') {
        generationBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return Response.json(batch, { status: 202 });
      }
      if (path === '/api/character-avatar-generations/avatar-generation%3Aupload') return Response.json(batch);
      return new Response('not found', { status: 404 });
    }));

    renderPanel();
    await screen.findByRole('heading', { name: 'Character profile' });
    const submit = screen.getByRole('button', { name: 'Upload image and generate avatar pack' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Upload source image'), {
      target: { files: [new File(['avatar'], 'my-face.png', { type: 'image/png' })] },
    });
    fireEvent.click(screen.getByLabelText('Confirm avatar source image rights'));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(generationBodies).toHaveLength(1));
    expect(generationBodies[0]).toMatchObject({
      source_asset_id: 'image-reference:user-face',
      source_image_consent_confirmed: true,
      include_blink: true,
      include_expressions: true,
    });
    expect(await screen.findByText(/Uploaded image accepted/)).toBeInTheDocument();
  });

  it('shows profile and owned backend data', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/characters') return Response.json({ characters: [legacyCharacter] });
      if (path === '/api/characters/maya/data') return Response.json(legacyData);
      return new Response('not found', { status: 404 });
    }));

    renderPanel();

    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Select character' })).toHaveValue('maya'));
    expect(await screen.findByText('Rainy hike joke.')).toBeInTheDocument();
    expect(screen.getByText('User likes tea.')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('requires typed confirmation and sends independent cleanup choices', async () => {
    const actionBodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/characters') return Response.json({ characters: [legacyCharacter] });
      if (path === '/api/characters/maya/data/actions') {
        actionBodies.push(JSON.parse(String(init?.body)));
        return Response.json({
          ok: true,
          character_id: 'maya',
          deleted_memory_records: 1,
          deleted_memory_candidates: 1,
          deleted_memory_snapshots: 1,
          deleted_transcript_messages: 0,
          voice_unlinked: false,
          profile_archived: false,
        });
      }
      if (path === '/api/characters/maya/data') return Response.json(legacyData);
      return new Response('not found', { status: 404 });
    }));

    renderPanel();
    await screen.findByRole('heading', { name: 'Danger zone / cleanup' });
    fireEvent.click(screen.getByLabelText('Delete character memories and pending suggestions'));
    const apply = screen.getByRole('button', { name: 'Apply selected actions' });
    expect(apply).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Confirm character id'), { target: { value: 'maya' } });
    fireEvent.click(apply);

    await waitFor(() => expect(actionBodies).toHaveLength(1));
    expect(actionBodies[0]).toMatchObject({
      confirm_character_id: 'maya',
      delete_memories: true,
      delete_transcripts: false,
      unlink_voice: false,
      archive_profile: false,
    });
  });
});
