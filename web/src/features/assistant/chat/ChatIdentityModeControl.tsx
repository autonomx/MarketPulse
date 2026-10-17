import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { omnixApiClient } from '../../../api/client';
import { characterClient, type CharacterProfile, type SessionInteraction } from './characterClient';
import './ChatIdentityModeControl.css';

export type ChatIdentityVoiceOption = {
  assetId: string;
  value: string;
  label: string;
};

type InteractionMode = 'system' | 'character';

type ModeMutationInput = {
  mode: InteractionMode;
  characterId?: string;
};

type ModeMutationResult = {
  interaction: SessionInteraction;
  resolvedSessionId: string;
};

export type ChatIdentityModeControlProps = {
  sessionId: string | null;
  systemVoiceId: string;
  defaultVoiceLabel: string;
  voiceOptions: ChatIdentityVoiceOption[];
  onSystemVoiceChange: (voiceId: string) => void;
  onSessionResolved?: (sessionId: string) => void;
  onOpenSystemSettings: () => void;
  onOpenCharacterSettings: () => void;
};

function isActiveCharacter(character: CharacterProfile): boolean {
  return character.enabled && character.status === 'active';
}

function isMissingSessionError(error: unknown): boolean {
  return error instanceof Error && error.message.toLowerCase().includes('chat session not found');
}

export function ChatIdentityModeControl({
  sessionId,
  systemVoiceId,
  defaultVoiceLabel,
  voiceOptions,
  onSystemVoiceChange,
  onSessionResolved,
  onOpenSystemSettings,
  onOpenCharacterSettings,
}: ChatIdentityModeControlProps) {
  const queryClient = useQueryClient();
  const [selectedCharacterId, setSelectedCharacterId] = useState('');
  const [status, setStatus] = useState<string | null>(null);

  const charactersQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'characters'],
    queryFn: () => characterClient.list(),
  });
  const interactionQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'interaction', sessionId],
    queryFn: () => characterClient.session(sessionId ?? ''),
    enabled: Boolean(sessionId),
  });

  const characters = useMemo(
    () => charactersQuery.data?.characters.filter(isActiveCharacter) ?? [],
    [charactersQuery.data?.characters],
  );
  const interaction = interactionQuery.data;
  const mode: InteractionMode = interaction?.interaction_mode ?? 'system';
  const defaultCharacterId = characters[0]?.id ?? '';
  const effectiveSelectedCharacterId = selectedCharacterId || interaction?.character_id || defaultCharacterId;
  const activeCharacter = characters.find((character) => character.id === interaction?.character_id) ?? null;

  useEffect(() => {
    if (interaction?.character_id) {
      setSelectedCharacterId(interaction.character_id);
      return;
    }
    setSelectedCharacterId((current) => {
      if (current && characters.some((character) => character.id === current)) return current;
      return defaultCharacterId;
    });
  }, [defaultCharacterId, interaction?.character_id, characters]);

  const mutation = useMutation({
    mutationFn: async ({ mode: nextMode, characterId }: ModeMutationInput): Promise<ModeMutationResult> => {
      const resolvedCharacterId = nextMode === 'character'
        ? characterId || effectiveSelectedCharacterId
        : '';
      const character = characters.find((candidate) => candidate.id === resolvedCharacterId) ?? null;
      if (nextMode === 'character' && !character) throw new Error('Create a character before enabling Character Mode.');

      const input = nextMode === 'character'
        ? {
            interaction_mode: 'character' as const,
            character_id: character?.id ?? null,
            voice_asset_id: character?.default_voice_asset_id ?? null,
            read_memory: interaction?.interaction_mode === 'character' ? interaction.read_memory : false,
            write_memory: interaction?.interaction_mode === 'character' ? interaction.write_memory : false,
            shared_memory_access: interaction?.interaction_mode === 'character' ? interaction.shared_memory_access : 'none' as const,
            transcript_policy: interaction?.transcript_policy ?? 'persistent' as const,
          }
        : {
            interaction_mode: 'system' as const,
            character_id: null,
            voice_asset_id: null,
            read_memory: false,
            write_memory: false,
            shared_memory_access: 'none' as const,
            transcript_policy: interaction?.transcript_policy ?? 'persistent' as const,
          };

      const persist = async (targetSessionId: string): Promise<ModeMutationResult> => ({
        interaction: await characterClient.setSession(targetSessionId, input),
        resolvedSessionId: targetSessionId,
      });

      if (sessionId) {
        try {
          return await persist(sessionId);
        } catch (error) {
          if (!isMissingSessionError(error)) throw error;
        }
      }

      const created = await omnixApiClient.createChatSession({
        title: nextMode === 'character' ? `Chat with ${character?.display_name ?? 'character'}` : 'New chat',
      });
      return persist(created.id);
    },
    onSuccess: async ({ interaction: nextInteraction, resolvedSessionId }) => {
      queryClient.setQueryData(['feature', 'chatbot', 'interaction', resolvedSessionId], nextInteraction);
      if (nextInteraction.character_id) setSelectedCharacterId(nextInteraction.character_id);
      if (resolvedSessionId !== sessionId) onSessionResolved?.(resolvedSessionId);
      setStatus(nextInteraction.interaction_mode === 'character'
        ? `Character Mode · ${characters.find((character) => character.id === nextInteraction.character_id)?.display_name ?? 'Character'}`
        : 'System Assistant');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'interaction', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'session', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'live-call-runtime'] }),
      ]);
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Identity update failed.'),
  });

  function chooseMode(nextMode: InteractionMode): void {
    if (nextMode === mode) return;
    mutation.mutate({ mode: nextMode, characterId: effectiveSelectedCharacterId });
  }

  function chooseCharacter(characterId: string): void {
    setSelectedCharacterId(characterId);
    if (mode === 'character') mutation.mutate({ mode: 'character', characterId });
  }

  function voiceLabel(character: CharacterProfile): string {
    const voiceId = character.default_voice_asset_id;
    if (!voiceId) return 'Default voice';
    return voiceOptions.find((voice) => voice.assetId === voiceId || voice.value === voiceId)?.label ?? voiceId.replace(/^voice-cloning:/, '');
  }

  const mutationBusy = mutation.isPending;
  const selectedCharacter = characters.find((character) => character.id === effectiveSelectedCharacterId) ?? activeCharacter;
  const characterSelectLabel = selectedCharacter
    ? `${selectedCharacter.display_name} — ${voiceLabel(selectedCharacter)}`
    : 'Select character';

  return (
    <div role="group" className="chat-identity-mode-control" aria-label="Chat identity controls">
      <div className="chat-identity-mode-toggle" role="group" aria-label="Chat mode">
        <button
          type="button"
          className={mode === 'system' ? 'active' : undefined}
          aria-pressed={mode === 'system'}
          disabled={mutationBusy}
          onClick={() => chooseMode('system')}
        >
          System
        </button>
        <button
          type="button"
          className={mode === 'character' ? 'active' : undefined}
          aria-pressed={mode === 'character'}
          disabled={mutationBusy || !characters.length}
          onClick={() => chooseMode('character')}
        >
          Character
        </button>
      </div>

      {mode === 'character' ? (
        <label className="chat-identity-context-select character">
          <span aria-hidden="true">♙</span>
          <span className="chat-identity-context-label">Character</span>
          <select
            aria-label="Character"
            title={characterSelectLabel}
            value={effectiveSelectedCharacterId}
            disabled={mutationBusy || !characters.length}
            onChange={(event) => chooseCharacter(event.currentTarget.value)}
          >
            {!characters.length ? <option value="">No characters created</option> : null}
            {characters.map((character) => (
              <option key={character.id} value={character.id}>
                {character.display_name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label className="chat-identity-context-select system">
          <span aria-hidden="true">◉</span>
          <span className="chat-identity-context-label">Voice</span>
          <select
            aria-label="System voice"
            value={systemVoiceId}
            onChange={(event) => onSystemVoiceChange(event.currentTarget.value)}
          >
            <option value="">{defaultVoiceLabel}</option>
            {voiceOptions.map((voice) => <option key={voice.assetId} value={voice.value}>{voice.label}</option>)}
          </select>
        </label>
      )}

      <button
        className="assistant-header-pill chat-identity-settings-button"
        type="button"
        aria-label={mode === 'character' ? 'Character Settings' : 'Personality'}
        title={mode === 'character' ? 'Character Settings' : 'Personality'}
        onClick={mode === 'character' ? onOpenCharacterSettings : onOpenSystemSettings}
      >
        <span aria-hidden="true" className="chat-identity-settings-icon">⚙</span>
        <span>{mode === 'character' ? 'Character Settings' : 'Personality'}</span>
      </button>

      <span className="chat-identity-mode-status" role="status" aria-live="polite">
        {status ?? (mode === 'character' ? `Character Mode · ${activeCharacter?.display_name ?? selectedCharacter?.display_name ?? 'Character'}` : 'System Assistant')}
      </span>
    </div>
  );
}
