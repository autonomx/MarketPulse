import { afterEach, describe, expect, it } from 'vitest';

import {
  clearPlaybackEchoSuppression,
  markPlaybackEchoSuppressed,
} from './live-voice-echo-suppression';
import {
  classifyOverlap,
  isLikelyEcho,
  shouldConfirmInterruption,
} from './live-voice-overlap-classifier';

afterEach(() => clearPlaybackEchoSuppression());

describe('live voice overlap classifier', () => {
  it('confirms hard stops, corrections, questions, and sustained speech', () => {
    expect(classifyOverlap('stop').intent).toBe('hard_stop');
    expect(classifyOverlap('No, I meant the eastern road').intent).toBe('interrupt');
    expect(classifyOverlap('Where did you say the entrance was?').intent).toBe('interrupt');
    expect(shouldConfirmInterruption(classifyOverlap('I need to change the destination now'), 'balanced')).toBe(true);
  });

  it('keeps acknowledgements and noise non-authoritative', () => {
    expect(classifyOverlap('mhm').intent).toBe('backchannel');
    expect(classifyOverlap('yeah').intent).toBe('backchannel');
    expect(classifyOverlap('[cough]').intent).toBe('noise');
    expect(shouldConfirmInterruption(classifyOverlap('right'), 'easy')).toBe(false);
  });

  it('recognizes likely assistant echo', () => {
    const assistant = 'The entrance is beneath the old watchtower near the river.';
    const echo = 'entrance beneath the old watchtower near the river';
    expect(isLikelyEcho(echo, assistant)).toBe(true);
    expect(classifyOverlap(echo, assistant).intent).toBe('noise');
  });

  it('suppresses semantic interruption while the acoustic gate identifies playback echo', () => {
    const sustained = classifyOverlap('I need to change the destination now');
    const hardStop = classifyOverlap('stop');
    markPlaybackEchoSuppressed('echo_residual_matches_playback');

    expect(shouldConfirmInterruption(sustained, 'easy')).toBe(false);
    expect(shouldConfirmInterruption(hardStop, 'finish_more')).toBe(false);

    clearPlaybackEchoSuppression();
    expect(shouldConfirmInterruption(hardStop, 'finish_more')).toBe(true);
  });

  it('allows users to tune ambiguous sustained overlap without weakening hard stops', () => {
    const sustained = classifyOverlap('I need to change the destination now');
    const hardStop = classifyOverlap('stop');
    expect(sustained.confidence).toBe(0.74);
    expect(shouldConfirmInterruption(sustained, 'easy')).toBe(true);
    expect(shouldConfirmInterruption(sustained, 'balanced')).toBe(true);
    expect(shouldConfirmInterruption(sustained, 'finish_more')).toBe(false);
    expect(shouldConfirmInterruption(hardStop, 'finish_more')).toBe(true);
  });
});
