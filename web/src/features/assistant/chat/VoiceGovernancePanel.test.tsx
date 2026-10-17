 
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { liveConversationStore } from '../workspace/live-conversation-store';
import { VoiceGovernancePanel } from './VoiceGovernancePanel';
import type { CharacterProfile } from './characterClient';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel(
  assetId = 'voice-cloning:maya',
  character?: CharacterProfile,
  characterIsActive = false,
) {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <VoiceGovernancePanel
        assetId={assetId}
        character={character}
        characterIsActive={characterIsActive}
        onActivateCharacter={characterIsActive ? () => undefined : undefined}
      />
    </QueryClientProvider>,
  );
}

const mayaCharacter: CharacterProfile = {
  id: 'maya', display_name: 'Maya', description: '', personality_prompt: '', default_greeting: '',
  default_voice_asset_id: 'voice-cloning:maya', speech_style: {}, identity_policy: {}, shared_memory_policy: {},
  active_version: 8, enabled: true, status: 'active',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
};

afterEach(() => {
  liveConversationStore.reset();
  vi.unstubAllGlobals();
});

describe('VoiceGovernancePanel', () => {
  it('marks every linked cloned voice ready without rendering consent or provenance controls', () => {
    renderPanel();

    expect(screen.getByText('Ready for use')).toBeInTheDocument();
    expect(screen.getByText('All cloned voices are automatically available for characters, live calls, System Assistant, and text-to-speech.')).toBeInTheDocument();
    expect(screen.queryByText('View / edit consent and provenance')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Voice consent status')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save voice governance' })).not.toBeInTheDocument();
  });

  it('does not invent a linked voice for an unlinked character', () => {
    renderPanel('');
    expect(screen.getByText('No default voice is linked to this character.')).toBeInTheDocument();
  });

  it('shows that the selected cloned voice is already active without requiring governance metadata', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(input.toString(), 'http://localhost').pathname;
      if (path === '/api/assets') return Response.json({
        assets: [{
          id: 'voice-cloning:maya',
          module: 'voice-cloning',
          type: 'voice_profile',
          metadata: { voice_governance: { consent_status: 'unverified', deletion_state: 'deleted', allowed_uses: [] } },
        }],
      });
      return new Response('not found', { status: 404 });
    }));

    renderPanel('voice-cloning:maya', mayaCharacter);

    const button = await screen.findByRole('button', { name: 'Currently used for live calls' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', "maya is already used for this character's live calls.");
  });

  it('assigns any cloned voice to the current character regardless of legacy governance metadata', async () => {
    const updateBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(input.toString(), 'http://localhost').pathname;
      if (path === '/api/assets') return Response.json({ assets: [
        { id: 'voice-cloning:maya', module: 'voice-cloning', type: 'voice_profile', metadata: {} },
        {
          id: 'voice-cloning:anaka',
          module: 'voice-cloning',
          type: 'voice_profile',
          metadata: { voice_governance: { consent_status: 'revoked', deletion_state: 'deleted', allowed_uses: [] } },
        },
      ] });
      if (path === '/api/characters/maya' && init?.method === 'PATCH') {
        updateBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return Response.json({ ...mayaCharacter, default_voice_asset_id: 'voice-cloning:anaka', active_version: 9 });
      }
      return new Response('not found', { status: 404 });
    }));

    renderPanel('voice-cloning:maya', mayaCharacter);
    await screen.findByRole('option', { name: 'anaka' });
    fireEvent.change(screen.getByLabelText('Character live-call voice'), { target: { value: 'voice-cloning:anaka' } });
    const assignButton = screen.getByRole('button', { name: 'Use for character live calls' });
    await waitFor(() => expect(assignButton).toBeEnabled());
    fireEvent.click(assignButton);

    await waitFor(() => expect(updateBodies).toEqual([{
      expected_version: 8,
      default_voice_asset_id: 'voice-cloning:anaka',
    }]));
    expect(await screen.findByRole('status')).toHaveTextContent("anaka is now Maya's live-call voice");
    expect(screen.getByRole('button', { name: 'Currently used for live calls' })).toBeDisabled();
  });

  it('synchronizes a changed voice into the active session and current playback runtime', async () => {
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:active' });
    liveConversationStore.dispatch({
      type: 'identity',
      identity: { characterId: 'maya', displayName: 'Maya', voiceId: 'Maya', profileVersion: 8 },
    });
    const sessionBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = decodeURIComponent(new URL(input.toString(), 'http://localhost').pathname);
      if (path === '/api/assets') return Response.json({ assets: [
        {
          id: 'voice-cloning:maya',
          module: 'voice-cloning',
          type: 'voice_profile',
          metadata: { voice_clone_id: 'Maya' },
        },
        {
          id: 'voice-cloning:inigo',
          module: 'voice-cloning',
          type: 'voice_profile',
          metadata: { voice_clone_id: 'Inigo' },
        },
      ] });
      if (path === '/api/characters/maya' && init?.method === 'PATCH') {
        return Response.json({
          ...mayaCharacter,
          default_voice_asset_id: 'voice-cloning:inigo',
          active_version: 9,
        });
      }
      if (path === '/api/chat/sessions/chat:active/interaction' && (!init?.method || init.method === 'GET')) {
        return Response.json({
          id: 'chat:active',
          title: 'Maya call',
          interaction_mode: 'character',
          character_id: 'maya',
          voice_asset_id: 'voice-cloning:maya',
          read_memory: true,
          write_memory: false,
          shared_memory_access: 'read_only',
          transcript_policy: 'persistent',
          character_profile_version: 8,
          messages: [],
        });
      }
      if (path === '/api/chat/sessions/chat:active/interaction' && init?.method === 'POST') {
        sessionBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return Response.json({
          id: 'chat:active',
          title: 'Maya call',
          interaction_mode: 'character',
          character_id: 'maya',
          voice_asset_id: 'voice-cloning:inigo',
          read_memory: true,
          write_memory: false,
          shared_memory_access: 'read_only',
          transcript_policy: 'persistent',
          character_profile_version: 9,
          messages: [],
        });
      }
      if (path === '/api/chat/sessions/chat:active/live-call/runtime') {
        return Response.json({
          session_id: 'chat:active',
          interaction_mode: 'character',
          display_name: 'Maya',
          character_id: 'maya',
          character_profile_version: 9,
          effective_identity_hash: 'b'.repeat(64),
          voice_asset_id: 'voice-cloning:inigo',
          voice_speaker_id: 'Inigo',
          greeting: '',
          speech_style: {
            speed: 1,
            temperature: 0.6,
            top_k: 20,
            top_p: 0.85,
            repetition_penalty: 1,
            expressiveness: 'neutral',
            emotion: 'neutral',
            interruption_style: 'balanced',
          },
          read_memory: true,
          write_memory: false,
          shared_memory_access: 'read_only',
          preload: {
            profile_loaded: true,
            voice_resolved: true,
            memory_snapshot_loaded: false,
            memory_record_count: 0,
            preload_ms: 1,
            resolved_at: '2026-07-31T00:00:00Z',
          },
        });
      }
      return new Response('not found', { status: 404 });
    }));

    renderPanel('voice-cloning:maya', mayaCharacter, true);
    await screen.findByRole('option', { name: 'Inigo' });
    fireEvent.change(screen.getByLabelText('Character live-call voice'), { target: { value: 'voice-cloning:inigo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use for character live calls' }));

    expect(await screen.findByRole('status')).toHaveTextContent(
      "Inigo is now Maya's live-call voice and is active for the current call.",
    );
    expect(sessionBodies).toEqual([{
      transcript_policy: 'persistent',
      read_memory: true,
      write_memory: false,
      shared_memory_access: 'read_only',
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:inigo',
      continue_topic: true,
    }]);
    expect(liveConversationStore.getState().identity.voiceId).toBe('Inigo');
    expect(screen.getByText(/active voice \(Inigo\)/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Currently used for live calls' })).toBeDisabled();
  });
});
