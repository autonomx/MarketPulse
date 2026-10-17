import { describe, expect, it } from 'vitest';
import { canStartAudioCapture, createAudioCaptureState, selectAudioCaptureDevice } from './audio-capture';

describe('audio capture contracts', () => {
  it('requires permission and selected device', () => {
    const state = createAudioCaptureState({ permission: 'granted' });
    expect(canStartAudioCapture(state)).toBe(false);
    expect(canStartAudioCapture(selectAudioCaptureDevice(state, 'device-1'))).toBe(true);
  });

  it('copies device records', () => {
    const state = createAudioCaptureState({ devices: [{ id: 'd1', label: 'Mic' }] });
    expect(state.devices).toEqual([{ id: 'd1', label: 'Mic' }]);
  });
});
