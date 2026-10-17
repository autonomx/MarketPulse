export type CapturePermission = 'unknown' | 'granted' | 'denied';

export type AudioCaptureDevice = {
  id: string;
  label: string;
};

export type AudioCaptureState = {
  permission: CapturePermission;
  selectedDeviceId?: string;
  devices: AudioCaptureDevice[];
  active: boolean;
};

export function createAudioCaptureState(input: Partial<AudioCaptureState> = {}): AudioCaptureState {
  return {
    permission: input.permission ?? 'unknown',
    selectedDeviceId: input.selectedDeviceId,
    devices: input.devices ? input.devices.map((device) => ({ ...device })) : [],
    active: input.active ?? false,
  };
}

export function selectAudioCaptureDevice(state: AudioCaptureState, deviceId: string): AudioCaptureState {
  return { ...state, selectedDeviceId: deviceId };
}

export function canStartAudioCapture(state: AudioCaptureState): boolean {
  return state.permission === 'granted' && Boolean(state.selectedDeviceId);
}
