import { useSyncExternalStore } from 'react';

/**
 * The story the storyteller workspace shows, for its tool panels (WP-9.4).
 * The workspace publishes the title and the rendered text; the panels used to
 * read them back from the page with innerText every second or so.
 * `revision` advances when a panel saves story data (cast, voice cast,
 * document, audio manifests), so panels that read another panel's data see it.
 */
export type StorySnapshot = {
  title: string;
  text: string;
  fingerprint: string;
  revision: number;
};

let snapshot: StorySnapshot = { title: 'Untitled story', text: '', fingerprint: fingerprintStoryAudio(''), revision: 0 };
const listeners = new Set<() => void>();

function publish(next: StorySnapshot): void {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

export function normalizeStoryAudioText(value: string): string {
  return value.replace(/\r\n/g, '\n').split('\n').map((line) => line.trim()).filter(Boolean).join('\n\n').trim();
}

export function fingerprintStoryAudio(text: string): string {
  return `${text.length}:${text.slice(0, 80)}:${text.slice(-80)}`;
}

/** The workspace's story: its title and the text it renders (headings and paragraphs, one per line). */
export function publishStorySnapshot(title: string, renderedText: string): void {
  const text = normalizeStoryAudioText(renderedText);
  const nextTitle = title.trim() || 'Untitled story';
  if (nextTitle === snapshot.title && text === snapshot.text) return;
  publish({ ...snapshot, title: nextTitle, text, fingerprint: fingerprintStoryAudio(text) });
}

export function getStorySnapshot(): StorySnapshot {
  return snapshot;
}

export function useStorySnapshot(): StorySnapshot {
  return useSyncExternalStore(subscribeStorySnapshot, getStorySnapshot, getStorySnapshot);
}

function subscribeStorySnapshot(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Saves story data for this browser; other panels re-read it when the stored value changed. */
export function writeStoryData(key: string, value: string): void {
  try {
    if (window.localStorage.getItem(key) === value) return;
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
  publish({ ...snapshot, revision: snapshot.revision + 1 });
}

export function resetStorySnapshotForTests(): void {
  publish({ title: 'Untitled story', text: '', fingerprint: fingerprintStoryAudio(''), revision: 0 });
}
