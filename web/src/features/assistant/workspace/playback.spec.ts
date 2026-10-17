import { describe, expect, it } from 'vitest';
import { createPlaybackQueue, enqueuePlaybackItem } from './playback';

describe('playback contracts', () => {
  it('queues playback items', () => {
    const item = { id: 'p1', text: 'Hello', createdAt: 't1' };
    expect(enqueuePlaybackItem(createPlaybackQueue(), item).items).toEqual([item]);
  });
});
