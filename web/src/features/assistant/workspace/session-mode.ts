export type LiveSessionMode = 'off' | 'starting' | 'ready' | 'input' | 'working' | 'output' | 'muted' | 'error';

export const LIVE_SESSION_MODES: LiveSessionMode[] = [
  'off',
  'starting',
  'ready',
  'input',
  'working',
  'output',
  'muted',
  'error',
];

export function isLiveSessionMode(value: string): value is LiveSessionMode {
  return LIVE_SESSION_MODES.includes(value as LiveSessionMode);
}

export function canStartInput(mode: LiveSessionMode): boolean {
  return mode === 'ready' || mode === 'muted';
}

export function canStartOutput(mode: LiveSessionMode): boolean {
  return mode === 'working';
}
