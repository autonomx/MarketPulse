import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getStorySnapshot, publishStorySnapshot, resetStorySnapshotForTests, writeStoryData } from './storySnapshotStore';

describe('story snapshot store', () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetStorySnapshotForTests();
  });

  it('publishes the shown story as paragraphs with a fingerprint', () => {
    publishStorySnapshot('  The Lantern  ', 'Chapter One\n  The lamp flickered.\n\nIt went out.');

    const text = 'Chapter One\n\nThe lamp flickered.\n\nIt went out.';
    expect(getStorySnapshot()).toMatchObject({ title: 'The Lantern', text, fingerprint: `${text.length}:${text}:${text}` });
    publishStorySnapshot('', '');
    expect(getStorySnapshot().title).toBe('Untitled story');
  });

  it('advances the revision only when saved story data changes', () => {
    const before = getStorySnapshot().revision;
    writeStoryData('omnix.storyteller.cast.test', '[]');
    writeStoryData('omnix.storyteller.cast.test', '[]');
    expect(getStorySnapshot().revision).toBe(before + 1);

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    writeStoryData('omnix.storyteller.cast.test', '[{}]');
    expect(getStorySnapshot().revision).toBe(before + 1);
    vi.restoreAllMocks();
  });
});
