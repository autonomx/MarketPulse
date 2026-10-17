/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { listenerBackchannelsRolloutEnabled } from './live-voice-backchannel';
import {
  resetLiveVoiceHumanizationFlags,
  writeLiveVoiceHumanizationFlags,
} from './live-voice-humanization-flags';
import { shouldUseUnifiedLiveVoiceAudio } from './live-voice-unified-audio-controller';
import { liveCallPresentationStore } from './live-call-presentation-store';

beforeEach(() => {
  liveCallPresentationStore.resetForTests();
  liveCallPresentationStore.update({ autoSpeak: true });
  resetLiveVoiceHumanizationFlags();
});

afterEach(() => {
  document.body.innerHTML = '';
  window.localStorage.clear();
});

describe('live voice humanization rollout integration', () => {
  it('master-disables unified humanized playback without changing Auto-speak state', () => {
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'POST' })).toBe(true);

    writeLiveVoiceHumanizationFlags({ master: false });

    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'POST' })).toBe(false);
    expect(liveCallPresentationStore.getState().autoSpeak).toBe(true);
  });

  it('disables listener cues independently from response playback', () => {
    writeLiveVoiceHumanizationFlags({ listenerCues: false });

    expect(listenerBackchannelsRolloutEnabled()).toBe(false);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'POST' })).toBe(true);
  });
});
