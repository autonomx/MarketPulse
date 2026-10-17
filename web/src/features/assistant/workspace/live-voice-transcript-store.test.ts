import { beforeEach, describe, expect, it, vi } from 'vitest';
import { liveVoiceTranscriptStore } from './live-voice-transcript-store';

describe('live voice transcript store', () => {
  beforeEach(() => liveVoiceTranscriptStore.resetForTests());

  it('grows the speaker draft and settles it on the final', () => {
    liveVoiceTranscriptStore.write('You', 'where are', 'draft');
    liveVoiceTranscriptStore.write('You', 'where are we going', 'draft');
    expect(liveVoiceTranscriptStore.draftText()).toBe('where are we going');

    liveVoiceTranscriptStore.write('You', 'Where are we going?', 'final');

    expect(liveVoiceTranscriptStore.getState().rows).toEqual([
      expect.objectContaining({ speaker: 'You', text: 'Where are we going?', draft: false }),
    ]);
    expect(liveVoiceTranscriptStore.draftText()).toBe('');
  });

  it('gives another speaker its own row instead of writing into the draft', () => {
    liveVoiceTranscriptStore.write('You', 'hold on', 'draft');
    liveVoiceTranscriptStore.write('Omnix', 'Live voice paused.', 'final');

    expect(liveVoiceTranscriptStore.getState().rows.map((row) => [row.speaker, row.text, row.draft])).toEqual([
      ['You', 'hold on', false],
      ['Omnix', 'Live voice paused.', false],
    ]);
  });

  it('ignores empty words and keeps delivery when rows are cleared', () => {
    const listener = vi.fn();
    const unsubscribe = liveVoiceTranscriptStore.subscribe(listener);
    liveVoiceTranscriptStore.write('You', '   ', 'draft');
    expect(listener).not.toHaveBeenCalled();

    liveVoiceTranscriptStore.write('You', 'hello', 'final');
    liveVoiceTranscriptStore.setDelivery({ text: 'Hi there', partial: true });
    liveVoiceTranscriptStore.setDelivery({ text: 'Hi there', partial: true });
    liveVoiceTranscriptStore.clear();

    expect(liveVoiceTranscriptStore.getState()).toEqual({ rows: [], delivery: { text: 'Hi there', partial: true } });
    expect(listener).toHaveBeenCalledTimes(3);
    unsubscribe();
  });
});
