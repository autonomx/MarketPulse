import type { BrowserAudioCaptureSession, BrowserAudioStream } from './audio-capture-browser';
import type { CapturedAudioReader } from './browser-live-controller';

export type BrowserRecorderBlobEvent = {
  data: Blob;
};

export type BrowserRecorderErrorEvent = {
  error?: Error;
};

export type BrowserMediaRecorder = {
  ondataavailable: ((event: BrowserRecorderBlobEvent) => void) | null;
  onerror: ((event: BrowserRecorderErrorEvent) => void) | null;
  onstop: (() => void) | null;
  start(): void;
  stop(): void;
};

export type BrowserMediaRecorderFactory = (stream: BrowserAudioStream, options?: { mimeType?: string }) => BrowserMediaRecorder;

export type MediaRecorderAudioReaderOptions = {
  factory: BrowserMediaRecorderFactory;
  mimeType?: string;
  captureDurationMs?: number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

export function createMediaRecorderAudioReader(options: MediaRecorderAudioReaderOptions): CapturedAudioReader {
  const setTimer = options.setTimer ?? ((callback, ms) => globalThis.setTimeout(callback, ms));
  const clearTimer = options.clearTimer ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>));

  return function readCapturedAudio(session: BrowserAudioCaptureSession): Promise<Blob> {
    return new Promise((resolve, reject) => {
      const chunks: Blob[] = [];
      const recorder = options.factory(session.stream, { mimeType: options.mimeType });
      let timer: unknown;
      let settled = false;

      const cleanup = () => {
        if (timer !== undefined) {
          clearTimer(timer);
        }
        recorder.ondataavailable = null;
        recorder.onerror = null;
        recorder.onstop = null;
      };

      const settle = (callback: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        callback();
      };

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      recorder.onerror = (event) => {
        settle(() => reject(event.error ?? new Error('Audio recording failed.')));
      };
      recorder.onstop = () => {
        settle(() => resolve(new Blob(chunks, { type: options.mimeType ?? chunks[0]?.type ?? 'audio/webm' })));
      };

      recorder.start();

      if (options.captureDurationMs !== undefined) {
        timer = setTimer(() => recorder.stop(), options.captureDurationMs);
      }
    });
  };
}
