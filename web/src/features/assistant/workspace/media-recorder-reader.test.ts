import { describe, expect, it, vi } from 'vitest';
import { createAudioCaptureState } from './audio-capture';
import type { BrowserAudioCaptureSession } from './audio-capture-browser';
import { createMediaRecorderAudioReader, type BrowserMediaRecorder } from './media-recorder-reader';

function session(): BrowserAudioCaptureSession {
  return {
    state: createAudioCaptureState({ permission: 'granted', active: true }),
    stream: { getTracks: () => [] },
  };
}

describe('createMediaRecorderAudioReader', () => {
  it('collects recorder chunks into a blob', async () => {
    let recorder: BrowserMediaRecorder | undefined;
    const reader = createMediaRecorderAudioReader({
      mimeType: 'audio/webm',
      factory: () => {
        recorder = {
          ondataavailable: null,
          onerror: null,
          onstop: null,
          start: vi.fn(),
          stop: vi.fn(function stop(this: BrowserMediaRecorder) {
            this.ondataavailable?.({ data: new Blob(['hello'], { type: 'audio/webm' }) });
            this.onstop?.();
          }),
        };
        return recorder;
      },
      captureDurationMs: 10,
      setTimer: (callback) => {
        callback();
        return 1;
      },
      clearTimer: vi.fn(),
    });

    const blob = (await reader(session())) as Blob;

    expect(recorder?.start).toHaveBeenCalledOnce();
    expect(recorder?.stop).toHaveBeenCalledOnce();
    expect(blob.type).toBe('audio/webm');
    expect(blob.size).toBeGreaterThan(0);
  });

  it('rejects recorder errors', async () => {
    const reader = createMediaRecorderAudioReader({
      factory: () => ({
        ondataavailable: null,
        onerror: null,
        onstop: null,
        start() {
          this.onerror?.({ error: new Error('denied') });
        },
        stop: vi.fn(),
      }),
    });

    await expect(reader(session())).rejects.toThrow('denied');
  });
});
