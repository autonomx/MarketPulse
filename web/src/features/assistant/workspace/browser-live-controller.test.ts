/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { describe, expect, it, vi } from 'vitest';
import type { BrowserAudioCaptureSession, BrowserAudioMediaDevices } from './audio-capture-browser';
import { createBrowserLiveAssistantController } from './browser-live-controller';
import { createStaticModelProvider } from './provider';
import type { SttServiceClient, TtsServiceClient } from './speech-services';

function mediaDevices(): BrowserAudioMediaDevices {
  return {
    enumerateDevices: vi.fn(async () => [{ deviceId: 'mic-1', label: 'Mic 1', kind: 'audioinput' }]),
    getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })),
  };
}

describe('createBrowserLiveAssistantController', () => {
  it('connects browser capture, audio reading, and live turn execution', async () => {
    const devices = mediaDevices();
    const readCapturedAudio = vi.fn(async (_session: BrowserAudioCaptureSession) => new ArrayBuffer(4));
    const stt: SttServiceClient = {
      transcribeAudio: vi.fn(async () => ({ text: 'hello live assistant' })),
    };
    const tts: TtsServiceClient = {
      synthesizeSpeech: vi.fn(async () => ({ audioUrl: 'blob:reply' })),
    };
    const provider = createStaticModelProvider('local', 'Local model', {}, async () => ({
      content: [{ kind: 'text', text: 'hello back' }],
    }));

    const controller = await createBrowserLiveAssistantController({
      sessionId: 'session:voice',
      mediaDevices: devices,
      provider,
      model: 'qwen-local',
      stt,
      tts,
      readCapturedAudio,
      now: () => '2026-06-23T09:30:00Z',
    });

    const session = await controller.startCapture();
    const audio = await controller.readCapturedAudio(session);
    const result = await controller.runTurn(audio);
    controller.stopCapture(session);

    expect(devices.enumerateDevices).toHaveBeenCalled();
    expect(devices.getUserMedia).toHaveBeenCalled();
    expect(readCapturedAudio).toHaveBeenCalledWith(session);
    expect(result.assistantText).toBe('hello back');
    expect(result.playbackItem.id).toBe('playback:session:voice:2026-06-23T09:30:00Z');
  });
});
