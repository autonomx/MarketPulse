import { afterEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS, LIVE_VOICE_HUMANIZATION_FLAGS_KEY, readLiveVoiceHumanizationFlags, resetLiveVoiceHumanizationFlags, writeLiveVoiceHumanizationFlags } from './live-voice-humanization-flags';
import { LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT } from '../../../events/bus';

afterEach(() => {
  window.localStorage.clear();
});

describe('live voice humanization rollout controls', () => {
  it('defaults every independently reversible phase to enabled', () => {
    expect(readLiveVoiceHumanizationFlags()).toEqual(DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS);
  });

  it('persists only boolean overrides and preserves unspecified defaults', () => {
    window.localStorage.setItem(LIVE_VOICE_HUMANIZATION_FLAGS_KEY, JSON.stringify({
      naturalTiming: false,
      responseCues: 'no',
    }));

    expect(readLiveVoiceHumanizationFlags()).toMatchObject({
      master: true,
      naturalTiming: false,
      responseCues: true,
      stableClauses: true,
    });
  });

  it('publishes changes and supports a full reset', () => {
    const listener = vi.fn();
    window.addEventListener(LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT, listener);

    const updated = writeLiveVoiceHumanizationFlags({ responseCues: false, vocalContinuity: false });
    expect(updated).toMatchObject({ responseCues: false, vocalContinuity: false, master: true });
    expect(listener).toHaveBeenCalledTimes(1);

    resetLiveVoiceHumanizationFlags();
    expect(readLiveVoiceHumanizationFlags()).toEqual(DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS);
    expect(listener).toHaveBeenCalledTimes(2);
    window.removeEventListener(LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT, listener);
  });
});
