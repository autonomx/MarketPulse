import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LIVE_TASK_INSTRUCTION_STORAGE_KEY,
  liveCallPresentationStore,
  liveCallVoiceMode,
  liveCaptureLabels,
} from './live-call-presentation-store';

describe('live call presentation store', () => {
  beforeEach(() => {
    window.localStorage.clear();
    liveCallPresentationStore.resetForTests();
  });

  it('notifies only when a value changes', () => {
    const listener = vi.fn();
    const unsubscribe = liveCallPresentationStore.subscribe(listener);

    liveCallPresentationStore.update({ hearing: false });
    liveCallPresentationStore.update({ hearing: true });
    liveCallPresentationStore.update({ hearing: true });

    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it('derives the orb mode: playback, then a capture error, then an open call', () => {
    const base = liveCallPresentationStore.getState();

    expect(liveCallVoiceMode(base)).toBe('idle');
    expect(liveCallVoiceMode(base, true)).toBe('listening');
    expect(liveCallVoiceMode({ ...base, captureStatus: 'connected' })).toBe('listening');
    expect(liveCallVoiceMode({ ...base, captureStatus: 'error', captureActive: true })).toBe('error');
    expect(liveCallVoiceMode({ ...base, captureStatus: 'error', speaking: true })).toBe('speaking');
  });

  it('words each capture status for the card', () => {
    expect(liveCaptureLabels('connecting')).toEqual({ state: 'Connecting', connection: 'Connecting', input: 'Requesting mic' });
    expect(liveCaptureLabels('connected')).toEqual({ state: 'Listening', connection: 'Connected', input: 'Listening' });
    expect(liveCaptureLabels('error')).toEqual({ state: 'Error', connection: 'Disconnected', input: 'Input error' });
  });

  it('remembers the live task for new calls', () => {
    liveCallPresentationStore.setTaskInstruction('Correct my grammar continuously while I speak.');

    expect(window.localStorage.getItem(LIVE_TASK_INSTRUCTION_STORAGE_KEY)).toBe('Correct my grammar continuously while I speak.');
    liveCallPresentationStore.resetForTests();
    expect(liveCallPresentationStore.getState().taskInstruction).toBe('Correct my grammar continuously while I speak.');
  });
});
