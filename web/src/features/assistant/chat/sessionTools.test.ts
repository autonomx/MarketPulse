import { describe, expect, it } from 'vitest';

import type { ChatSession } from '../../../api/client';
import type { SessionInteraction } from './characterClient';
import { preservedNewChatRequest, visibleChatSessions } from './sessionTools';

describe('preservedNewChatRequest', () => {
  it('carries the active character setup into a blank chat', () => {
    const session = {
      id: 'chat:old',
      title: 'Existing conversation',
      provider_id: 'lm-studio',
      model_id: 'local-model',
    } as ChatSession;
    const interaction = {
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:Maya',
      read_memory: true,
      write_memory: false,
      shared_memory_access: 'read_only',
      transcript_policy: 'temporary',
    } as SessionInteraction;

    expect(preservedNewChatRequest(session, interaction)).toEqual({
      title: 'New chat',
      provider_id: 'lm-studio',
      model_id: 'local-model',
      interaction_mode: 'character',
      character_id: 'maya',
      voice_asset_id: 'voice-cloning:Maya',
      read_memory: true,
      write_memory: false,
      shared_memory_access: 'read_only',
      transcript_policy: 'temporary',
    });
  });

  it('does not carry a fully-qualified model from a different provider', () => {
    const session = {
      id: 'chat:old',
      title: 'Existing conversation',
      provider_id: 'chatgpt_codex',
      model_id: 'llm:openrouter:stealth/ox-alpha',
    } as ChatSession;
    const interaction = {
      interaction_mode: 'system',
      character_id: null,
      voice_asset_id: null,
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
    } as SessionInteraction;

    expect(preservedNewChatRequest(session, interaction)).toEqual({
      title: 'New chat',
      provider_id: 'chatgpt_codex',
      interaction_mode: 'system',
      character_id: null,
      voice_asset_id: null,
      read_memory: false,
      write_memory: false,
      shared_memory_access: 'none',
      transcript_policy: 'persistent',
    });
  });
});

describe('visibleChatSessions', () => {
  it('leaves Podcast script sessions out of the chat list', () => {
    const payload = { sessions: [{ id: 'a', title: 'Trip plan' }, { id: 'b', title: 'Podcast script: episode 1' }], next_cursor: null };

    expect(visibleChatSessions(payload)).toEqual({ sessions: [{ id: 'a', title: 'Trip plan' }], next_cursor: null });
  });
});
