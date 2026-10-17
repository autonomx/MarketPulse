import { describe, expect, it } from 'vitest';
import { createAudioCaptureState } from './audio-capture';
import {
  refreshBrowserAudioCaptureDevices,
  requestBrowserAudioCapturePermission,
  startBrowserAudioCapture,
  stopBrowserAudioCapture,
  toAudioCaptureDevices,
  type BrowserAudioMediaDevices,
  type BrowserAudioStream,
} from './audio-capture-browser';

function streamWithCounter(counter: { stops: number }): BrowserAudioStream {
  return {
    getTracks: () => [{ stop: () => { counter.stops += 1; } }],
  };
}

describe('browser audio capture adapter', () => {
  it('maps only audio input devices', () => {
    expect(toAudioCaptureDevices([
      { deviceId: 'mic-1', label: 'Mic', kind: 'audioinput' },
      { deviceId: 'cam-1', label: 'Camera', kind: 'videoinput' },
    ])).toEqual([{ id: 'mic-1', label: 'Mic' }]);
  });

  it('requests permission, stops probe streams, and selects a device', async () => {
    const counter = { stops: 0 };
    const mediaDevices: BrowserAudioMediaDevices = {
      enumerateDevices: async () => [{ deviceId: 'mic-1', label: 'Mic', kind: 'audioinput' }],
      getUserMedia: async () => streamWithCounter(counter),
    };

    const state = await requestBrowserAudioCapturePermission(mediaDevices);

    expect(counter.stops).toBe(1);
    expect(state.permission).toBe('granted');
    expect(state.selectedDeviceId).toBe('mic-1');
  });

  it('starts and stops browser audio capture for the selected device', async () => {
    const counter = { stops: 0 };
    const mediaDevices: BrowserAudioMediaDevices = {
      enumerateDevices: async () => [],
      getUserMedia: async (constraints) => {
        expect(constraints).toEqual({ audio: { deviceId: { exact: 'mic-1' } }, video: false });
        return streamWithCounter(counter);
      },
    };

    const session = await startBrowserAudioCapture(
      mediaDevices,
      createAudioCaptureState({ permission: 'granted', selectedDeviceId: 'mic-1' }),
    );

    expect(session.state.active).toBe(true);
    expect(stopBrowserAudioCapture(session).active).toBe(false);
    expect(counter.stops).toBe(1);
  });

  it('denies capture when permission requests fail', async () => {
    const mediaDevices: BrowserAudioMediaDevices = {
      enumerateDevices: async () => [],
      getUserMedia: async () => {
        throw new Error('denied');
      },
    };

    expect((await requestBrowserAudioCapturePermission(mediaDevices)).permission).toBe('denied');
  });

  it('preserves an existing selected device when it is still available', async () => {
    const mediaDevices: BrowserAudioMediaDevices = {
      enumerateDevices: async () => [
        { deviceId: 'mic-1', label: 'Mic 1', kind: 'audioinput' },
        { deviceId: 'mic-2', label: 'Mic 2', kind: 'audioinput' },
      ],
      getUserMedia: async () => streamWithCounter({ stops: 0 }),
    };

    const state = await refreshBrowserAudioCaptureDevices(
      mediaDevices,
      createAudioCaptureState({ selectedDeviceId: 'mic-2' }),
    );

    expect(state.selectedDeviceId).toBe('mic-2');
  });
});
