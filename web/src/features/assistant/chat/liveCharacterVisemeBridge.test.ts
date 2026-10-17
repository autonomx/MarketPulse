/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fitVisemesToDuration,
  installLiveCharacterVisemeBridge,
  visemeAnimationFrameKeys,
  visemeSequenceFromText,
  type RuntimeAvatarPack,
} from './liveCharacterVisemeBridge';

const phasedPack: RuntimeAvatarPack = {
  render_mode: 'viseme',
  renderer: 'sprite',
  base_asset_id: 'image:maya-closed',
  mouth_frames: {
    closed: 'image:maya-closed',
    silence: 'image:maya-closed',
    A_soft: 'image:maya-a-soft',
    A_medium: 'image:maya-a-medium',
    A_strong: 'image:maya-a-strong',
    A: 'image:maya-a-peak',
    E_soft: 'image:maya-e-soft',
    E_medium: 'image:maya-e-medium',
    E_strong: 'image:maya-e-strong',
    E: 'image:maya-e-peak',
  },
};

beforeEach(() => {
  // The bridges act only while Chat is the active workspace.
  window.history.replaceState(null, '', '/chatbot');
  installLiveCharacterVisemeBridge();
});

afterEach(() => {
  window.dispatchEvent(new CustomEvent('omnix:character-avatar-runtime', { detail: null }));
  document.body.innerHTML = '';
});

describe('live character timed visemes', () => {
  it('maps text into distinct visual mouth groups', () => {
    const sequence = visemeSequenceFromText('Maya loves five blue owls.');
    expect(sequence[0]).toBe('MBP');
    expect(sequence).toContain('A');
    expect(sequence).toContain('FV');
    expect(sequence).toContain('L');
    expect(sequence).toContain('O');
    expect(sequence).toContain('U');
    expect(sequence.at(-1)).toBe('silence');
  });

  it('fits cues to the actual streamed audio duration', () => {
    const cues = fitVisemesToDuration('Hello Maya', 1_250);
    expect(cues[0].startMs).toBe(0);
    const finalCue = cues.at(-1);
    expect(finalCue).toBeDefined();
    expect((finalCue?.startMs ?? 0) + (finalCue?.durationMs ?? 0)).toBeCloseTo(1_250, 5);
    expect(cues.every((cue) => cue.durationMs > 0)).toBe(true);
  });

  it('uses one bounded silence cue when no text is available', () => {
    expect(fitVisemesToDuration('', 400)).toEqual([
      { viseme: 'silence', startMs: 0, durationMs: 400 },
    ]);
  });

  it('uses subtle phases for ordinary speech and reserves peak poses for long cues', () => {
    expect(visemeAnimationFrameKeys(phasedPack, 'silence', 'A', 90)).toEqual([
      'A_soft',
      'A_medium',
      'A_strong',
    ]);
    expect(visemeAnimationFrameKeys(phasedPack, 'silence', 'A', 160)).toEqual([
      'A_soft',
      'A_medium',
      'A_strong',
      'A',
    ]);
    expect(visemeAnimationFrameKeys(phasedPack, 'A', 'E', 90)).toEqual([
      'A_soft',
      'E_soft',
      'E_medium',
      'E_strong',
    ]);
    expect(visemeAnimationFrameKeys(phasedPack, 'E', 'silence', 75)).toEqual([
      'E_strong',
      'E_medium',
      'E_soft',
      'silence',
    ]);
  });

  it('prevents the four-frame envelope renderer from overwriting precise visemes', () => {
    window.dispatchEvent(new CustomEvent('omnix:character-avatar-runtime', {
      detail: { display_name: 'Maya', avatar_pack: phasedPack },
    }));
    const listener = vi.fn();
    window.addEventListener('omnix:character-avatar-frame', listener);

    window.dispatchEvent(new CustomEvent('omnix:character-avatar-frame', {
      detail: { frame: 'wide' },
    }));

    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener('omnix:character-avatar-frame', listener);
  });
});
