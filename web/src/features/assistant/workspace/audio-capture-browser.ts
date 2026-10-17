import type { AudioCaptureDevice, AudioCaptureState } from './audio-capture';
import { canStartAudioCapture, createAudioCaptureState } from './audio-capture';

export type BrowserAudioDeviceInfo = {
  deviceId: string;
  label: string;
  kind: string;
};

export type BrowserAudioTrack = {
  stop(): void;
};

export type BrowserAudioStream = {
  getTracks(): BrowserAudioTrack[];
};

export type BrowserAudioConstraints = {
  audio: true | { deviceId?: { exact: string } };
  video: false;
};

export type BrowserAudioMediaDevices = {
  enumerateDevices(): Promise<BrowserAudioDeviceInfo[]>;
  getUserMedia(constraints: BrowserAudioConstraints): Promise<BrowserAudioStream>;
};

export type BrowserAudioCaptureSession = {
  state: AudioCaptureState;
  stream: BrowserAudioStream;
};

export function toAudioCaptureDevices(devices: BrowserAudioDeviceInfo[]): AudioCaptureDevice[] {
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device) => ({
      id: device.deviceId,
      label: device.label || `Microphone ${device.deviceId}`,
    }));
}

export async function refreshBrowserAudioCaptureDevices(
  mediaDevices: BrowserAudioMediaDevices,
  state: AudioCaptureState = createAudioCaptureState(),
): Promise<AudioCaptureState> {
  const devices = toAudioCaptureDevices(await mediaDevices.enumerateDevices());
  const selectedDeviceId = state.selectedDeviceId && devices.some((device) => device.id === state.selectedDeviceId)
    ? state.selectedDeviceId
    : devices[0]?.id;

  return createAudioCaptureState({
    ...state,
    devices,
    selectedDeviceId,
  });
}

export async function requestBrowserAudioCapturePermission(
  mediaDevices: BrowserAudioMediaDevices,
  state: AudioCaptureState = createAudioCaptureState(),
): Promise<AudioCaptureState> {
  try {
    const stream = await mediaDevices.getUserMedia({ audio: true, video: false });
    stopBrowserAudioStream(stream);
    return refreshBrowserAudioCaptureDevices(
      mediaDevices,
      createAudioCaptureState({ ...state, permission: 'granted' }),
    );
  } catch {
    return createAudioCaptureState({ ...state, permission: 'denied', active: false });
  }
}

export async function startBrowserAudioCapture(
  mediaDevices: BrowserAudioMediaDevices,
  state: AudioCaptureState,
): Promise<BrowserAudioCaptureSession> {
  if (!canStartAudioCapture(state)) {
    throw new Error('Audio capture requires granted permission and a selected device.');
  }

  const stream = await mediaDevices.getUserMedia({
    audio: { deviceId: { exact: state.selectedDeviceId as string } },
    video: false,
  });

  return {
    state: createAudioCaptureState({ ...state, active: true }),
    stream,
  };
}

export function stopBrowserAudioStream(stream: BrowserAudioStream): void {
  for (const track of stream.getTracks()) {
    track.stop();
  }
}

export function stopBrowserAudioCapture(session: BrowserAudioCaptureSession): AudioCaptureState {
  stopBrowserAudioStream(session.stream);
  return createAudioCaptureState({ ...session.state, active: false });
}
