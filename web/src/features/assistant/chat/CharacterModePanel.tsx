/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { omnixApiClient } from '../../../api/client';
import { characterClient } from './characterClient';
import './CharacterModePanel.css';

export function CharacterModePanel({ sessionId, onSessionResolved }: { sessionId: string; onSessionResolved?: (sessionId: string) => void }) {
  const queryClient = useQueryClient();
  const [selectedCharacterId, setSelectedCharacterId] = useState('');
  const [readMemory, setReadMemory] = useState(false);
  const [writeMemory, setWriteMemory] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const charactersQuery = useQuery({ queryKey: ['feature', 'chatbot', 'characters'], queryFn: () => characterClient.list() });
  const interactionQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'interaction', sessionId],
    queryFn: () => characterClient.session(sessionId),
  });
  const characters = useMemo(
    () => charactersQuery.data?.characters.filter((item) => item.enabled && item.status === 'active') ?? [],
    [charactersQuery.data?.characters],
  );
  const interaction = interactionQuery.data;
  const defaultCharacterId = characters[0]?.id ?? '';
  const effectiveSelectedCharacterId = selectedCharacterId || defaultCharacterId;
  const activeCharacter = useMemo(
    () => characters.find((item) => item.id === interaction?.character_id),
    [characters, interaction?.character_id],
  );

  useEffect(() => {
    setSelectedCharacterId(interaction?.character_id || defaultCharacterId);
    if (interaction) {
      setReadMemory(interaction.read_memory);
      setWriteMemory(interaction.write_memory);
    }
  }, [defaultCharacterId, interaction?.character_id, interaction?.read_memory, interaction?.write_memory, sessionId]);

  const mutation = useMutation({
    mutationFn: async (mode: 'system' | 'character') => {
      const characterId = mode === 'character' ? effectiveSelectedCharacterId : null;
      const character = characters.find((item) => item.id === characterId);
      const input = {
        interaction_mode: mode,
        character_id: characterId,
        voice_asset_id: mode === 'character' ? character?.default_voice_asset_id ?? null : null,
        read_memory: mode === 'character' ? readMemory : false,
        write_memory: mode === 'character' ? writeMemory : false,
      } as const;
      try {
        return { next: await characterClient.setSession(sessionId, input), resolvedSessionId: sessionId };
      } catch (error) {
        const message = error instanceof Error ? error.message.toLowerCase() : '';
        if (mode !== 'character' || !message.includes('chat session not found')) throw error;
        const created = await omnixApiClient.createChatSession({ title: `Live Chat with ${character?.display_name ?? 'character'}` });
        const next = await characterClient.setSession(created.id, input);
        return { next, resolvedSessionId: created.id };
      }
    },
    onSuccess: async ({ next, resolvedSessionId }) => {
      queryClient.setQueryData(['feature', 'chatbot', 'interaction', resolvedSessionId], next);
      if (next.character_id) setSelectedCharacterId(next.character_id);
      if (resolvedSessionId !== sessionId) onSessionResolved?.(resolvedSessionId);
      const memoryLabel = next.read_memory && next.write_memory
        ? 'Memory read/write on'
        : next.read_memory
          ? 'Memory read-only'
          : next.write_memory
            ? 'Memory write-only'
            : 'Memory off';
      const character = characters.find((item) => item.id === next.character_id);
      setStatus(next.interaction_mode === 'character'
        ? `Character Mode enabled. ${character?.display_name ?? 'Character'} loaded${next.voice_asset_id ? ' with linked voice' : ''}. ${memoryLabel}.`
        : 'System Assistant enabled. Character identity and memory are inactive.');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'interaction', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'session', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'live-call-runtime'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-state'] }),
      ]);
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Character Mode update failed.'),
  });

  const characterMode = interaction?.interaction_mode === 'character';
  const currentMemoryLabel = interaction?.read_memory && interaction?.write_memory
    ? 'Read and save'
    : interaction?.read_memory
      ? 'Read only'
      : interaction?.write_memory
        ? 'Save only'
        : 'Off';
  return (
    <article className="character-mode-card" aria-label="Character Mode settings">
      <header>
        <div><p className="eyebrow">Identity</p><h3>Character Mode</h3><p>Use a server-owned character personality, linked voice, and isolated memory.</p></div>
        <span className={characterMode ? 'active' : ''}>{characterMode ? `Talking to ${activeCharacter?.display_name ?? 'character'}` : 'System Assistant'}</span>
      </header>
      <div className="character-mode-controls">
        <label>Character<select aria-label="Character" value={effectiveSelectedCharacterId} disabled={!characters.length || mutation.isPending} onChange={(event) => setSelectedCharacterId(event.currentTarget.value)}>{!characters.length ? <option value="">No characters created</option> : null}{characters.map((character) => <option key={character.id} value={character.id}>{character.display_name}</option>)}</select></label>
        <div className="character-mode-actions">
          <button type="button" disabled={!effectiveSelectedCharacterId || mutation.isPending} onClick={() => mutation.mutate('character')}>{characterMode ? 'Apply Character Settings' : 'Enable Character Mode'}</button>
          <button type="button" disabled={mutation.isPending || !characterMode} onClick={() => mutation.mutate('system')}>Use System Assistant</button>
        </div>
      </div>
      <fieldset className="character-memory-permissions" disabled={mutation.isPending}>
        <legend>Character memory permissions</legend>
        <label><input type="checkbox" checked={readMemory} onChange={(event) => setReadMemory(event.currentTarget.checked)} />Read past memories</label>
        <label><input type="checkbox" checked={writeMemory} onChange={(event) => setWriteMemory(event.currentTarget.checked)} />Save new memories</label>
      </fieldset>
      {activeCharacter ? <dl className="character-mode-details"><div><dt>Voice</dt><dd>{activeCharacter.default_voice_asset_id ?? 'Current system voice'}</dd></div><div><dt>Profile version</dt><dd>{activeCharacter.active_version}</dd></div><div><dt>Memory</dt><dd>{currentMemoryLabel}</dd></div></dl> : null}
      {status ? <p className="character-mode-status" role="status">{status}</p> : null}
    </article>
  );
}
