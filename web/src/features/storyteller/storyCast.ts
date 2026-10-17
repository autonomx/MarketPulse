import { writeStoryData } from './storySnapshotStore';
export type StoryCharacterRole = 'narrator' | 'protagonist' | 'supporting' | 'minor';
export type StoryCharacterSource = 'outline' | 'generation' | 'manual';

export interface StoryCharacter {
  id: string;
  displayName: string;
  aliases: string[];
  role: StoryCharacterRole;
  description?: string;
  traits?: string[];
  iconAssetId?: string;
  detectedFrom: StoryCharacterSource;
  confidence: number;
}

const STORY_CAST_STORAGE_PREFIX = 'omnix.storyteller.cast:';
const narrator: StoryCharacter = {
  id: 'narrator',
  displayName: 'Narrator',
  aliases: ['Narrator'],
  role: 'narrator',
  detectedFrom: 'manual',
  confidence: 1,
};

const ignoredNames = new Set(['Chapter', 'Story', 'The', 'A', 'An', 'As', 'I', 'He', 'She', 'They', 'You']);

export function storyCastStorageKey(storyFingerprint: string): string {
  return `${STORY_CAST_STORAGE_PREFIX}${storyFingerprint || 'draft'}`;
}

export function loadStoryCast(storyFingerprint: string): StoryCharacter[] {
  try {
    const raw = window.localStorage.getItem(storyCastStorageKey(storyFingerprint));
    if (!raw) return [narrator];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [narrator];
    return normalizeCast(parsed as Partial<StoryCharacter>[]);
  } catch {
    return [narrator];
  }
}

export function saveStoryCast(storyFingerprint: string, cast: StoryCharacter[]): void {
  writeStoryData(storyCastStorageKey(storyFingerprint), JSON.stringify(normalizeCast(cast)));
}

export function deriveStoryCast(text: string, existing: StoryCharacter[] = []): StoryCharacter[] {
  const byId = new Map<string, StoryCharacter>();
  for (const character of normalizeCast(existing)) {
    byId.set(character.id, character);
  }
  byId.set('narrator', narrator);

  const counts = new Map<string, number>();
  const namePattern = /\b([A-Z][a-zA-Z'’-]{2,})(?:\s+(?:of|the|von|de|da)\s+[A-Z][a-zA-Z'’-]{2,}|\s+[A-Z][a-zA-Z'’-]{2,})?/g;
  for (const match of text.matchAll(namePattern)) {
    const name = match[0].trim();
    if (ignoredNames.has(name) || /^Chapter\s+\d+/i.test(name)) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }

  const ranked = [...counts.entries()]
    .filter(([, count]) => count >= 2)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 12);

  for (const [name, count] of ranked) {
    const id = characterId(name);
    const current = byId.get(id);
    if (current) {
      byId.set(id, { ...current, aliases: mergeAliases(current.aliases, [name]), confidence: Math.max(current.confidence, confidenceForCount(count)) });
    } else {
      byId.set(id, {
        id,
        displayName: name,
        aliases: [name],
        role: byId.size === 1 ? 'protagonist' : 'supporting',
        detectedFrom: 'generation',
        confidence: confidenceForCount(count),
      });
    }
  }

  return normalizeCast([...byId.values()]);
}

export function updateStoryCharacter(cast: StoryCharacter[], updated: StoryCharacter): StoryCharacter[] {
  return normalizeCast(cast.map((character) => character.id === updated.id ? updated : character));
}

export function addStoryCharacter(cast: StoryCharacter[], name: string): StoryCharacter[] {
  const displayName = name.trim();
  if (!displayName) return normalizeCast(cast);
  const id = characterId(displayName);
  if (cast.some((character) => character.id === id)) return normalizeCast(cast);
  return normalizeCast([...cast, { id, displayName, aliases: [displayName], role: 'supporting', detectedFrom: 'manual', confidence: 1 }]);
}

export function characterId(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug || 'character';
}

function normalizeCast(cast: Partial<StoryCharacter>[]): StoryCharacter[] {
  const byId = new Map<string, StoryCharacter>();
  byId.set('narrator', narrator);
  for (const entry of cast) {
    const displayName = typeof entry.displayName === 'string' && entry.displayName.trim() ? entry.displayName.trim() : '';
    const id = entry.id === 'narrator' ? 'narrator' : characterId(entry.id || displayName);
    if (!displayName && id !== 'narrator') continue;
    const previous = byId.get(id);
    byId.set(id, {
      id,
      displayName: id === 'narrator' ? 'Narrator' : displayName,
      aliases: mergeAliases(previous?.aliases ?? [], Array.isArray(entry.aliases) ? entry.aliases : [displayName]),
      role: validRole(entry.role) ?? previous?.role ?? (id === 'narrator' ? 'narrator' : 'supporting'),
      description: typeof entry.description === 'string' ? entry.description : previous?.description,
      traits: Array.isArray(entry.traits) ? entry.traits.filter((trait): trait is string => typeof trait === 'string') : previous?.traits,
      iconAssetId: typeof entry.iconAssetId === 'string' ? entry.iconAssetId : previous?.iconAssetId,
      detectedFrom: validSource(entry.detectedFrom) ?? previous?.detectedFrom ?? (id === 'narrator' ? 'manual' : 'generation'),
      confidence: typeof entry.confidence === 'number' ? Math.max(0, Math.min(1, entry.confidence)) : previous?.confidence ?? 0.7,
    });
  }
  return [...byId.values()].sort((left, right) => left.id === 'narrator' ? -1 : right.id === 'narrator' ? 1 : left.displayName.localeCompare(right.displayName));
}

function mergeAliases(left: string[], right: string[]): string[] {
  return [...new Set([...left, ...right].map((alias) => alias.trim()).filter(Boolean))];
}

function confidenceForCount(count: number): number {
  return Math.min(0.95, 0.55 + count * 0.08);
}

function validRole(value: unknown): StoryCharacterRole | undefined {
  return value === 'narrator' || value === 'protagonist' || value === 'supporting' || value === 'minor' ? value : undefined;
}

function validSource(value: unknown): StoryCharacterSource | undefined {
  return value === 'outline' || value === 'generation' || value === 'manual' ? value : undefined;
}
