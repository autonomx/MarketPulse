import { useEffect, useMemo, useState } from 'react';
import { omnixApiClient, type AssetListResponse } from '../../api/client';
import { voiceOptionsFromAssets } from './StoryAudioPanel';
import { deriveStoryCast, loadStoryCast, type StoryCharacter } from './storyCast';
import { buildStoryDocumentFromText } from './storyDocument';
import { loadStoryVoiceCastAny, removeVoiceAssignment, saveStoryVoiceCastAliases, upsertVoiceAssignment, voiceAssignmentFor, type VoiceCastOption } from './storyVoiceCast';
import { useStorySnapshot } from './storySnapshotStore';

const styleOptions = ['Story narrator', 'Warm', 'Dramatic', 'Soft', 'Gravelly', 'Playful'];

export function StoryVoiceCastPanel() {
  const snapshot = useStorySnapshot();
  const [characters, setCharacters] = useState<StoryCharacter[]>(() => deriveStoryCast(snapshot.text, loadStoryCast(snapshot.fingerprint)));
  const [voices, setVoices] = useState<VoiceCastOption[]>([]);
  const [documentId, setDocumentId] = useState(() => buildStoryDocumentFromText({ title: snapshot.title, text: snapshot.text }).id);
  const [assignments, setAssignments] = useState(() => loadStoryVoiceCastAny([snapshot.fingerprint, documentId]));
  const assignedCount = useMemo(() => assignments.filter((assignment) => assignment.voiceId).length, [assignments]);

  useEffect(() => {
    let active = true;
    omnixApiClient.listAssets().then((payload) => {
      if (!active) return;
      setVoices(voiceOptionsFromAssets((payload.assets ?? []) as AssetListResponse['assets']));
    }).catch(() => {
      if (active) setVoices([]);
    });
    return () => { active = false; };
  }, []);

  // The cast panel's saved characters advance the snapshot revision.
  useEffect(() => {
    const nextDocumentId = buildStoryDocumentFromText({ title: snapshot.title, text: snapshot.text }).id;
    setDocumentId(nextDocumentId);
    setCharacters(deriveStoryCast(snapshot.text, loadStoryCast(snapshot.fingerprint)));
    setAssignments(loadStoryVoiceCastAny([snapshot.fingerprint, nextDocumentId]));
  }, [snapshot.title, snapshot.text, snapshot.fingerprint, snapshot.revision]);

  function persistAssignments(next: ReturnType<typeof loadStoryVoiceCastAny>): void {
    setAssignments(next);
    saveStoryVoiceCastAliases([snapshot.fingerprint, documentId], next);
  }

  function updateVoice(character: StoryCharacter, voiceId: string): void {
    const voiceLabel = voices.find((voice) => voice.id === voiceId)?.label ?? '';
    const existing = voiceAssignmentFor(assignments, character.id);
    const next = voiceId
      ? upsertVoiceAssignment(assignments, { characterId: character.id, voiceId, voiceLabel, style: existing?.style ?? defaultStyleFor(character), updatedAt: new Date().toISOString() })
      : removeVoiceAssignment(assignments, character.id);
    persistAssignments(next);
  }

  function updateStyle(character: StoryCharacter, style: string): void {
    const existing = voiceAssignmentFor(assignments, character.id);
    if (!existing) return;
    const next = upsertVoiceAssignment(assignments, { ...existing, style, updatedAt: new Date().toISOString() });
    persistAssignments(next);
  }

  return (
    <section className="storyteller-cast-panel" aria-label="Story voice cast">
      <div className="storyteller-cast-heading">
        <div><p className="eyebrow">Voice cast</p><h3>Assign cloned voices</h3></div>
        <strong>{assignedCount}/{characters.length} assigned</strong>
      </div>
      <p>Assign cloned voices by stable character id. Characters without a selected voice fall back to the narrator voice during audio generation.</p>
      <div className="storyteller-voice-cast-table">
        {characters.map((character) => {
          const assignment = voiceAssignmentFor(assignments, character.id);
          return (
            <article className="storyteller-voice-row" key={character.id}>
              <div><strong>{character.displayName}</strong><span>{character.role}</span></div>
              <select aria-label={`${character.displayName} cloned voice`} value={assignment?.voiceId ?? ''} onChange={(event) => updateVoice(character, event.currentTarget.value)}>
                <option value="">Narrator fallback</option>
                {voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
              </select>
              <select aria-label={`${character.displayName} voice style`} value={assignment?.style ?? defaultStyleFor(character)} disabled={!assignment?.voiceId} onChange={(event) => updateStyle(character, event.currentTarget.value)}>
                {styleOptions.map((style) => <option key={style} value={style}>{style}</option>)}
              </select>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function defaultStyleFor(character: StoryCharacter): string {
  if (character.role === 'narrator') return 'Story narrator';
  if (character.role === 'protagonist') return 'Warm';
  return 'Dramatic';
}
