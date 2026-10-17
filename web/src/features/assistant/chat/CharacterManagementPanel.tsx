/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { omnixApiClient } from '../../../api/client';
import { CharacterAvatarPanel } from './CharacterAvatarPanel';
import { characterAvatarAssetUrl, characterAvatarClient } from './characterAvatarClient';
import { characterClient, type CharacterDataExport, type CharacterProfile } from './characterClient';
import { CharacterHermesPanel } from './CharacterHermesPanel';
import { CharacterVoiceBackfillButton } from './CharacterVoiceBackfillButton';
import { VoiceGovernancePanel } from './VoiceGovernancePanel';
import './CharacterManagementPanel.css';
import { downloadJson } from '../../../shared/download';

const SELECTED_CHARACTER_STORAGE_KEY = 'omnix.chatbot.selectedCharacterId';

export function CharacterManagementPanel({
  sessionId = null,
  onSessionResolved,
}: {
  sessionId?: string | null;
  onSessionResolved?: (sessionId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState(() => window.localStorage.getItem(SELECTED_CHARACTER_STORAGE_KEY) ?? '');
  const [draft, setDraft] = useState({ display_name: '', description: '', personality_prompt: '', default_greeting: '', gender: '' });
  const [confirmation, setConfirmation] = useState('');
  const [actions, setActions] = useState({ delete_memories: false, delete_transcripts: false, unlink_voice: false, archive_profile: false });
  const [status, setStatus] = useState<string | null>(null);
  const [profileSaveStatus, setProfileSaveStatus] = useState<{ kind: 'saving' | 'saved' | 'error'; message: string } | null>(null);
  const lastSessionIdentitySyncRef = useRef('');

  const charactersQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'characters', 'management'],
    queryFn: () => characterClient.list(),
  });
  const characters = charactersQuery.data?.characters ?? [];
  const selected = characters.find((item) => item.id === selectedId) ?? characters[0];
  const dataQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-data', selected?.id],
    queryFn: () => characterClient.data(selected?.id ?? ''),
    enabled: Boolean(selected?.id),
  });
  const interactionQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'interaction', sessionId],
    queryFn: () => characterClient.session(sessionId ?? ''),
    enabled: Boolean(sessionId),
  });
  const activeSessionCharacterId = interactionQuery.data?.interaction_mode === 'character'
    ? interactionQuery.data.character_id
    : null;

  useEffect(() => {
    if (!selectedId && characters[0]) setSelectedId(characters[0].id);
  }, [characters, selectedId]);

  // Synchronize the editor with a newly selected chat once, then preserve the
  // user's manual profile choice while they decide which character to activate.
  // Background query refreshes must not snap the selector back to the currently
  // active Live Voice character.
  useEffect(() => {
    if (!sessionId || !activeSessionCharacterId) {
      lastSessionIdentitySyncRef.current = '';
      return;
    }
    if (!characters.some((character) => character.id === activeSessionCharacterId)) return;
    const syncKey = `${sessionId}:${activeSessionCharacterId}`;
    if (lastSessionIdentitySyncRef.current === syncKey) return;
    lastSessionIdentitySyncRef.current = syncKey;
    setSelectedId(activeSessionCharacterId);
  }, [activeSessionCharacterId, characters, sessionId]);

  useEffect(() => {
    if (selectedId) window.localStorage.setItem(SELECTED_CHARACTER_STORAGE_KEY, selectedId);
  }, [selectedId]);

  useEffect(() => {
    if (!selected) return;
    setDraft({
      display_name: selected.display_name,
      description: selected.description,
      personality_prompt: selected.personality_prompt,
      default_greeting: selected.default_greeting,
      gender: typeof selected.speech_style.gender === 'string' ? selected.speech_style.gender : '',
    });
    setConfirmation('');
    setProfileSaveStatus(null);
    setActions({ delete_memories: false, delete_transcripts: false, unlink_voice: false, archive_profile: false });
  }, [selected?.id, selected?.active_version]);

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'characters'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-data'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'interaction'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory'] }),
    ]);
  }

  const createMutation = useMutation({
    mutationFn: () => characterClient.create({
      display_name: 'New Character',
      personality_prompt: 'Be clear, warm, and consistent with this character profile.',
      description: '',
      default_greeting: 'Hello.',
    }),
    onSuccess: async (created) => {
      setSelectedId(created.id);
      setStatus('Character created.');
      await refresh();
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Character creation failed.'),
  });

  const updateMutation = useMutation({
    mutationFn: () => characterClient.update(selected?.id ?? '', {
      expected_version: selected?.active_version,
      display_name: draft.display_name,
      description: draft.description,
      personality_prompt: draft.personality_prompt,
      default_greeting: draft.default_greeting,
      speech_style: { ...(selected?.speech_style ?? {}), gender: draft.gender },
    }),
    onMutate: () => setProfileSaveStatus({ kind: 'saving', message: 'Saving profile…' }),
    onSuccess: async (updated) => {
      const message = `Profile saved successfully as version ${updated.active_version}.`;
      setProfileSaveStatus({ kind: 'saved', message });
      setStatus(message);
      await refresh();
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Character update failed.';
      setProfileSaveStatus({ kind: 'error', message });
      setStatus(message);
    },
  });

  const activateCharacterMutation = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error('Select a character first.');
      let resolvedSessionId: string;
      if (sessionId) {
        resolvedSessionId = sessionId;
      } else {
        const created = await omnixApiClient.createChatSession({ title: `Live Chat with ${selected.display_name}` });
        resolvedSessionId = created.id;
      }
      const interaction = await characterClient.setSession(resolvedSessionId, {
        interaction_mode: 'character',
        character_id: selected.id,
        voice_asset_id: selected.default_voice_asset_id ?? null,
        read_memory: false,
        write_memory: false,
        shared_memory_access: 'none',
      });
      return { interaction, resolvedSessionId };
    },
    onSuccess: async ({ interaction, resolvedSessionId }) => {
      queryClient.setQueryData(['feature', 'chatbot', 'interaction', resolvedSessionId], interaction);
      if (interaction.interaction_mode === 'character' && interaction.character_id) {
        setSelectedId(interaction.character_id);
        lastSessionIdentitySyncRef.current = `${resolvedSessionId}:${interaction.character_id}`;
      }
      onSessionResolved?.(resolvedSessionId);
      setStatus(`${selected?.display_name ?? 'Character'} is now active in this chat and Live Voice${interaction.voice_asset_id ? ' with its linked voice' : ''}.`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'interaction', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'session', resolvedSessionId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'live-call-runtime'] }),
      ]);
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Character activation failed.'),
  });

  const actionMutation = useMutation({
    mutationFn: () => characterClient.applyDataActions(selected?.id ?? '', {
      confirm_character_id: confirmation,
      ...actions,
    }),
    onSuccess: async (result) => {
      setStatus(`Character data updated: ${result.deleted_memory_records} memories, ${result.deleted_transcript_messages} transcript messages removed.`);
      setConfirmation('');
      await refresh();
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Character data action failed.'),
  });

  function exportData(data: CharacterDataExport): void {
    downloadJson(data, `${data.character.id}-character-export.json`);
    setStatus('Character export prepared from current backend state.');
  }

  const hasAction = Object.values(actions).some(Boolean);
  const selectedCharacterIsActive = interactionQuery.data?.interaction_mode === 'character'
    && interactionQuery.data.character_id === selected?.id;

  return (
    <article className="character-management-card" aria-label="Character management">
      <h3 className="character-management-sr-title">Profiles and relationship data</h3>
      <div className="character-management-layout">
        <section className="character-selector-bar" aria-label="Character profiles">
          <label>
            <span>Character</span>
            <select aria-label="Select character" value={selected?.id ?? ''} onChange={(event) => setSelectedId(event.currentTarget.value)}>
              {characters.map((character) => (
                <option value={character.id} key={character.id}>
                  {character.display_name} · {character.status} · v{character.active_version}
                </option>
              ))}
            </select>
          </label>
          <div>
            <small>{characters.length} {characters.length === 1 ? 'character' : 'characters'}</small>
            <button type="button" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>
              <span aria-hidden="true">＋</span>
              New character
            </button>
          </div>
        </section>

        <div className="character-management-editor">
          {charactersQuery.isPending ? <div className="character-dashboard-empty">Loading characters…</div> : selected ? (
            <div className="character-dashboard-grid">
              <header className="character-editor-summary">
                <CharacterRosterAvatar character={selected} />
                <div>
                  <span>Editing character</span>
                  <h3>{selected.display_name}</h3>
                  <p>{selected.description || 'Add a short description to make this character easier to identify.'}</p>
                </div>
                <dl>
                  <div><dt>Status</dt><dd>{selected.status}</dd></div>
                  <div><dt>Version</dt><dd>v{selected.active_version}</dd></div>
                </dl>
              </header>
              <section className="character-dashboard-section character-profile-section">
                <header className="character-section-heading">
                  <div><span>1</span><h4>Character profile</h4></div>
                  <aside className="character-profile-save-actions">
                    {profileSaveStatus ? <p className={`character-profile-save-confirmation ${profileSaveStatus.kind}`} role={profileSaveStatus.kind === 'error' ? 'alert' : 'status'}>{profileSaveStatus.message}</p> : null}
                    <button
                      type="button"
                      disabled={updateMutation.isPending || selected.status === 'archived'}
                      onClick={() => updateMutation.mutate()}
                    >
                      {updateMutation.isPending ? 'Saving…' : 'Save profile version'}
                    </button>
                  </aside>
                </header>
                <div className="character-form-grid">
                  <label>
                    <span>Display name <small>{draft.display_name.length} / 160</small></span>
                    <input aria-label="Character name" value={draft.display_name} onChange={(event) => setDraft({ ...draft, display_name: event.currentTarget.value })} />
                  </label>
                  <label>
                    <span>Gender</span>
                    <select aria-label="Character gender" value={draft.gender} onChange={(event) => setDraft({ ...draft, gender: event.currentTarget.value })}>
                      <option value="">Not specified</option>
                      <option value="female">Female</option>
                      <option value="male">Male</option>
                      <option value="nonbinary">Nonbinary</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label>
                    <span>Short description <small>{draft.description.length} / 1000</small></span>
                    <textarea aria-label="Character description" rows={2} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.currentTarget.value })} />
                  </label>
                  <label>
                    <span>Default greeting <small>{draft.default_greeting.length} / 2000</small></span>
                    <textarea aria-label="Character greeting" rows={2} value={draft.default_greeting} onChange={(event) => setDraft({ ...draft, default_greeting: event.currentTarget.value })} />
                  </label>
                  <label className="wide">
                    <span>Personality prompt <small>{draft.personality_prompt.length} characters</small></span>
                    <textarea aria-label="Character personality" rows={4} value={draft.personality_prompt} onChange={(event) => setDraft({ ...draft, personality_prompt: event.currentTarget.value })} />
                  </label>
                </div>
                <p className="character-section-note">Profile instructions apply to this Character Mode identity and its Omnix Chat conversations.</p>
              </section>

              <VoiceGovernancePanel
                assetId={selected.default_voice_asset_id}
                character={selected}
                characterIsActive={selectedCharacterIsActive}
                activationPending={activateCharacterMutation.isPending}
                onActivateCharacter={() => activateCharacterMutation.mutate()}
              />
              <CharacterAvatarPanel character={selected} />

              <section className="character-dashboard-section character-backfill-section">
                <header className="character-section-heading">
                  <div><span>4</span><h4>Cloned-voice backfill</h4></div>
                </header>
                <div className="character-backfill-content">
                  <span className="character-backfill-icon" aria-hidden="true">♙</span>
                  <div>
                    <strong>Create characters from cloned voices</strong>
                    <p>Discover governed voice profiles, create missing character identities, and queue their avatar and viseme packs.</p>
                  </div>
                  <CharacterVoiceBackfillButton />
                </div>
              </section>

              <CharacterDataSummary
                characterId={selected.id}
                data={dataQuery.data}
                loading={dataQuery.isPending}
                onExport={() => dataQuery.data && exportData(dataQuery.data)}
              />

              <section className="character-dashboard-section character-danger-zone">
                <header className="character-section-heading">
                  <div><span>6</span><h4>Danger zone / cleanup</h4></div>
                </header>
                <p>Choose only the independent resources to remove. Voice, profile, avatar, memories, and transcripts remain separately governed.</p>
                <div className="character-action-options">
                  <label><input aria-label="Delete character memories and pending suggestions" type="checkbox" checked={actions.delete_memories} onChange={(event) => setActions({ ...actions, delete_memories: event.currentTarget.checked })} /><span><strong>Delete memories</strong><small>Remove this character’s memories and pending suggestions.</small></span></label>
                  <label><input aria-label="Delete character transcript messages" type="checkbox" checked={actions.delete_transcripts} onChange={(event) => setActions({ ...actions, delete_transcripts: event.currentTarget.checked })} /><span><strong>Delete transcripts</strong><small>Remove this character’s transcript messages.</small></span></label>
                  <label><input aria-label="Unlink the default voice without deleting it" type="checkbox" checked={actions.unlink_voice} onChange={(event) => setActions({ ...actions, unlink_voice: event.currentTarget.checked })} /><span><strong>Unlink voice</strong><small>Keep the voice asset but remove it from this profile.</small></span></label>
                  <label><input aria-label="Archive the character profile" type="checkbox" checked={actions.archive_profile} onChange={(event) => setActions({ ...actions, archive_profile: event.currentTarget.checked })} /><span><strong>Archive profile</strong><small>Hide this character from active Character Mode selection.</small></span></label>
                </div>
                <div className="character-danger-confirmation">
                  <label>
                    Type <code>{selected.id}</code> to confirm
                    <input aria-label="Confirm character id" value={confirmation} onChange={(event) => setConfirmation(event.currentTarget.value)} />
                  </label>
                  <button type="button" className="danger" disabled={!hasAction || confirmation !== selected.id || actionMutation.isPending} onClick={() => actionMutation.mutate()}>
                    Apply selected actions
                  </button>
                </div>
                <small>Changes apply to Omnix Chat Character Mode and live-avatar experiences. RPG data is not modified.</small>
              </section>
            </div>
          ) : (
            <div className="character-dashboard-empty">
              <strong>No characters have been created.</strong>
              <p>Create a blank profile or use cloned-voice discovery to build governed character and avatar records.</p>
              <button type="button" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>Create first character</button>
            </div>
          )}
        </div>
      </div>
      {status ? <p className="character-management-status" role="status">{status}</p> : null}
    </article>
  );
}

function CharacterRosterAvatar({ character }: { character: CharacterProfile }) {
  const packQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id],
    queryFn: () => characterAvatarClient.optionalPack(character.id),
    retry: false,
  });
  const pack = packQuery.data;
  const assetId = pack?.mouth_frames.closed || pack?.mouth_frames.silence || pack?.base_asset_id || '';
  const initials = character.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || 'C';

  return <span className="character-roster-avatar" aria-hidden="true">
    {assetId ? <img src={characterAvatarAssetUrl(assetId)} alt="" /> : <strong>{initials}</strong>}
  </span>;
}

function CharacterDataSummary({
  characterId,
  data,
  loading,
  onExport,
}: {
  characterId: string;
  data?: CharacterDataExport;
  loading: boolean;
  onExport: () => void;
}) {
  const transcriptCount = data?.sessions.reduce((total, item) => total + item.character_message_count, 0) ?? 0;
  return <section className="character-dashboard-section character-data-section">
    <header className="character-section-heading">
      <div><span>5</span><h4>Character data / relationship data</h4></div>
      <button type="button" disabled={!data} onClick={onExport}>Export data</button>
    </header>
    {loading ? <p>Loading backend state…</p> : data ? <>
      <div className="character-data-metrics">
        <span><small>Memories</small><strong>{data.memories.length.toLocaleString()}</strong><em>{data.versions.length} profile versions</em></span>
        <span><small>Suggestions</small><strong>{data.pending_suggestions.length.toLocaleString()}</strong><em>Pending review</em></span>
        <span><small>Sessions</small><strong>{data.sessions.length.toLocaleString()}</strong><em>Character conversations</em></span>
        <span><small>Transcript messages</small><strong>{transcriptCount.toLocaleString()}</strong><em>Character-owned messages</em></span>
      </div>
      {(data.memories.length || data.pending_suggestions.length) ? <details>
        <summary>Review stored relationship data</summary>
        {data.memories.length ? <ul>{data.memories.map((memory, index) => <li key={String(memory.id ?? index)}><strong>{String(memory.category ?? '')}</strong> · <span>{String(memory.content ?? '')}</span></li>)}</ul> : null}
        {data.pending_suggestions.length ? <ul>{data.pending_suggestions.map((candidate, index) => <li key={String(candidate.id ?? index)}><strong>{String(candidate.proposed_category ?? '')}</strong> · <span>{String(candidate.proposed_content ?? '')}</span></li>)}</ul> : null}
      </details> : null}
      <CharacterHermesPanel characterId={characterId} />
    </> : <p>Character data is unavailable.</p>}
  </section>;
}