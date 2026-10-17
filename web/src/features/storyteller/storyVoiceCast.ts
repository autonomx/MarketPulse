import type { StoryVoiceAssignment } from './storyDocument';
import { writeStoryData } from './storySnapshotStore';

const STORY_VOICE_CAST_STORAGE_PREFIX = 'omnix.storyteller.voiceCast:';

export interface VoiceCastOption {
  id: string;
  label: string;
}

export function storyVoiceCastStorageKey(storyFingerprint: string): string {
  return `${STORY_VOICE_CAST_STORAGE_PREFIX}${storyFingerprint || 'draft'}`;
}

export function loadStoryVoiceCast(storyFingerprint: string): StoryVoiceAssignment[] {
  try {
    const raw = window.localStorage.getItem(storyVoiceCastStorageKey(storyFingerprint));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isVoiceAssignment);
  } catch {
    return [];
  }
}

export function loadStoryVoiceCastAny(keys: string[]): StoryVoiceAssignment[] {
  for (const key of dedupeKeys(keys)) {
    const assignments = loadStoryVoiceCast(key);
    if (assignments.length) return assignments;
  }
  return [];
}

export function saveStoryVoiceCast(storyFingerprint: string, assignments: StoryVoiceAssignment[]): void {
  writeStoryData(storyVoiceCastStorageKey(storyFingerprint), JSON.stringify(assignments.filter(isVoiceAssignment)));
}

export function saveStoryVoiceCastAliases(keys: string[], assignments: StoryVoiceAssignment[]): void {
  for (const key of dedupeKeys(keys)) saveStoryVoiceCast(key, assignments);
}

export function upsertVoiceAssignment(assignments: StoryVoiceAssignment[], next: StoryVoiceAssignment): StoryVoiceAssignment[] {
  return [...assignments.filter((assignment) => assignment.characterId !== next.characterId), next]
    .sort((left, right) => left.characterId.localeCompare(right.characterId));
}

export function removeVoiceAssignment(assignments: StoryVoiceAssignment[], characterId: string): StoryVoiceAssignment[] {
  return assignments.filter((assignment) => assignment.characterId !== characterId);
}

export function voiceAssignmentFor(assignments: StoryVoiceAssignment[], characterId: string): StoryVoiceAssignment | undefined {
  return assignments.find((assignment) => assignment.characterId === characterId);
}

export function voiceCastFingerprint(assignments: StoryVoiceAssignment[]): string {
  const packed = assignments.map((assignment) => `${assignment.characterId}:${assignment.voiceId}:${assignment.style ?? ''}`).sort().join('|');
  return `${packed.length}:${packed.slice(0, 80)}:${packed.slice(-80)}`;
}

function dedupeKeys(keys: string[]): string[] {
  return [...new Set(keys.map((key) => key.trim()).filter(Boolean))];
}

function isVoiceAssignment(value: unknown): value is StoryVoiceAssignment {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<StoryVoiceAssignment>;
  return typeof record.characterId === 'string' && typeof record.voiceId === 'string' && typeof record.voiceLabel === 'string' && typeof record.updatedAt === 'string';
}
