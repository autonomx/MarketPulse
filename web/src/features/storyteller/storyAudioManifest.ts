import { buildStoryDocumentFromText, storyDocumentFingerprint, type StoryAudioManifest, type StoryDocument } from './storyDocument';
import { voiceCastFingerprint, loadStoryVoiceCast } from './storyVoiceCast';
import { writeStoryData } from './storySnapshotStore';

const STORY_AUDIO_MANIFEST_STORAGE_PREFIX = 'omnix.storyteller.audioManifests:';

export type StoryAudioChapterStatus = 'missing' | 'ready' | 'stale' | 'failed';

export interface StoryAudioChapterState {
  chapterId: string;
  chapterTitle: string;
  textFingerprint: string;
  status: StoryAudioChapterStatus;
  manifest?: StoryAudioManifest;
}

export function storyAudioManifestStorageKey(storyId: string): string {
  return `${STORY_AUDIO_MANIFEST_STORAGE_PREFIX}${storyId || 'draft'}`;
}

export function loadStoryAudioManifests(storyId: string): StoryAudioManifest[] {
  try {
    const raw = window.localStorage.getItem(storyAudioManifestStorageKey(storyId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isManifest);
  } catch {
    return [];
  }
}

export function saveStoryAudioManifests(storyId: string, manifests: StoryAudioManifest[]): void {
  writeStoryData(storyAudioManifestStorageKey(storyId), JSON.stringify(manifests.filter(isManifest)));
}

export function buildChapterAudioStates(document: StoryDocument): StoryAudioChapterState[] {
  const manifests = loadStoryAudioManifests(document.id);
  const voiceFingerprint = voiceCastFingerprint(loadStoryVoiceCast(document.id));
  return document.chapters.map((chapter) => {
    const manifest = manifests.find((entry) => entry.scope === 'chapter' && entry.chapterIds.includes(chapter.id));
    if (!manifest) return { chapterId: chapter.id, chapterTitle: chapter.title, textFingerprint: chapter.textFingerprint, status: 'missing' };
    if (manifest.status === 'failed') return { chapterId: chapter.id, chapterTitle: chapter.title, textFingerprint: chapter.textFingerprint, status: 'failed', manifest };
    const stale = manifest.sourceFingerprint !== chapter.textFingerprint || manifest.voiceCastFingerprint !== voiceFingerprint;
    return { chapterId: chapter.id, chapterTitle: chapter.title, textFingerprint: chapter.textFingerprint, status: stale ? 'stale' : 'ready', manifest: stale ? { ...manifest, status: 'stale' } : manifest };
  });
}

export function upsertStoryAudioManifest(storyId: string, manifest: StoryAudioManifest): StoryAudioManifest[] {
  const next = [manifest, ...loadStoryAudioManifests(storyId).filter((entry) => entry.id !== manifest.id)];
  saveStoryAudioManifests(storyId, next);
  return next;
}

export function manifestForChapter(document: StoryDocument, chapterId: string, jobId?: string): StoryAudioManifest {
  const chapter = document.chapters.find((entry) => entry.id === chapterId);
  const now = new Date().toISOString();
  return {
    id: `audio:${document.id}:${chapterId}`,
    storyId: document.id,
    scope: 'chapter',
    chapterIds: [chapterId],
    sourceFingerprint: chapter?.textFingerprint ?? storyDocumentFingerprint(document),
    voiceCastFingerprint: voiceCastFingerprint(loadStoryVoiceCast(document.id)),
    jobId,
    status: jobId ? 'queued' : 'stale',
    createdAt: now,
    updatedAt: now,
  };
}

export function documentFromCurrentStory(title: string, text: string): StoryDocument {
  return buildStoryDocumentFromText({ title, text });
}

function isManifest(value: unknown): value is StoryAudioManifest {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<StoryAudioManifest>;
  return typeof record.id === 'string' && typeof record.storyId === 'string' && Array.isArray(record.chapterIds) && typeof record.status === 'string';
}
